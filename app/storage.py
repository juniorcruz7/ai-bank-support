"""Leitura e escrita atômica de execuções e tickets."""

import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import APP_TIMEZONE
from app.models import Profile, Run, Ticket, User

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
PROFILES_PATH = DATA_DIR / "profiles.json"
TICKETS_PATH = DATA_DIR / "tickets.json"
USERS_PATH = DATA_DIR / "users.json"
RUNS_DIR = ROOT_DIR / "runs"

# O nome do arquivo termina com o começo da chave de cache: o cache acha a
# execução por glob, sem abrir todos os arquivos da pasta.
CACHE_KEY_PREFIX_LENGTH = 16

MIN_PROFILES = 3
MAX_PROFILES = 5

logger = logging.getLogger(__name__)


M = TypeVar("M", bound=BaseModel)


class DataError(Exception):
    """Dados ausentes ou inválidos; a mensagem é legível para o usuário."""


def write_json_atomic(path: Path, data: Any) -> None:
    """Grava JSON de forma atômica: escreve num temporário na mesma pasta e renomeia.

    Se algo falhar no meio, o arquivo de destino continua como estava e o
    temporário é apagado. Cria as pastas que faltarem.
    """
    path = Path(path)
    # Serializa antes de abrir qualquer arquivo: dado inválido falha sem tocar no disco.
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _read_json_list(path: Path) -> list[Any]:
    if not path.exists():
        raise DataError(f"Arquivo não encontrado: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(f"JSON inválido em {path.name} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}") from None
    if not isinstance(data, list):
        raise DataError(f"{path.name} deve conter uma lista JSON.")
    return data


def _describe_error(err: dict[str, Any]) -> str:
    field = ".".join(str(part) for part in err["loc"]) or "registro"
    kind = err["type"]
    if kind == "literal_error":
        allowed = err["ctx"]["expected"].replace(" or ", " ou ")
        return f"{field}: valor {err['input']!r} inválido; permitidos: {allowed}"
    if kind == "missing":
        return f"{field}: campo obrigatório ausente"
    if kind == "extra_forbidden":
        return f"{field}: campo não permitido"
    if kind == "string_pattern_mismatch":
        return f"{field}: formato inválido ({err['input']!r})"
    if kind == "value_error":
        return f"{field}: {err['ctx']['error']}" if field != "registro" else str(err["ctx"]["error"])
    return f"{field}: {err['msg']}"


def _validate_items(items: list[Any], model: type[M], label: str, filename: str) -> list[M]:
    result: list[M] = []
    for index, item in enumerate(items):
        try:
            result.append(model.model_validate(item))
        except ValidationError as exc:
            ref = item.get("id") if isinstance(item, dict) else None
            where = f"{label} {ref!r}" if ref else f"{label} na posição {index}"
            details = "; ".join(_describe_error(e) for e in exc.errors())
            raise DataError(f"{where} inválido em {filename}: {details}") from None
    return result


def _ensure_unique_ids(items: list[Profile] | list[Ticket], filename: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise DataError(f"ID duplicado em {filename}: {item.id!r}")
        seen.add(item.id)


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, Profile]:
    """Carrega e valida os perfis; retorna um dicionário id -> Profile."""
    profiles = _validate_items(_read_json_list(path), Profile, "Perfil", path.name)
    if not MIN_PROFILES <= len(profiles) <= MAX_PROFILES:
        raise DataError(f"{path.name} deve ter de {MIN_PROFILES} a {MAX_PROFILES} perfis; encontrados {len(profiles)}.")
    _ensure_unique_ids(profiles, path.name)
    return {profile.id: profile for profile in profiles}


def load_tickets(profiles: dict[str, Profile], path: Path = TICKETS_PATH) -> list[Ticket]:
    """Carrega e valida os tickets; todo ticket precisa pertencer a um perfil existente."""
    tickets = _validate_items(_read_json_list(path), Ticket, "Ticket", path.name)
    _ensure_unique_ids(tickets, path.name)
    for ticket in tickets:
        if ticket.customer_id not in profiles:
            raise DataError(f"Ticket {ticket.id!r} aponta para cliente inexistente: {ticket.customer_id!r}.")
    return tickets


def load_users(profiles: dict[str, Profile], path: Path = USERS_PATH) -> dict[str, User]:
    """Carrega e valida as contas (RF19); conta de cliente precisa apontar para um perfil existente."""
    users = _validate_items(_read_json_list(path), User, "Conta", path.name)
    seen: set[str] = set()
    for user in users:
        if user.username in seen:
            raise DataError(f"Usuário duplicado em {path.name}: {user.username!r}")
        seen.add(user.username)
        if user.customer_id is not None and user.customer_id not in profiles:
            raise DataError(f"Conta {user.username!r} aponta para cliente inexistente: {user.customer_id!r}.")
    return {user.username: user for user in users}


def next_ticket_id(tickets: list[Ticket]) -> str:
    """Próximo ID sequencial (T007, T008…), a partir do maior número existente."""
    highest = max((int(t.id[1:]) for t in tickets), default=0)
    return f"T{highest + 1:03d}"


def add_ticket(
    profiles: dict[str, Profile],
    customer_id: str,
    title: str,
    description: str,
    path: Path = TICKETS_PATH,
    now: datetime | None = None,
) -> Ticket:
    """Anexa um ticket aberto ao tickets.json (RF17); nada é gravado se algo for inválido."""
    get_profile(profiles, customer_id)  # cliente inexistente -> DataError, sem ticket órfão
    tickets = load_tickets(profiles, path)
    created_at = (now or datetime.now(APP_TIMEZONE)).replace(microsecond=0)
    data = {
        "id": next_ticket_id(tickets),
        "customer_id": customer_id,
        "created_at": created_at,
        "title": title,
        "description": description,
        "status": "aberto",
        "resolution": None,
    }
    try:
        ticket = Ticket.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(_describe_error(e) for e in exc.errors())
        raise DataError(f"Ticket inválido: {details}") from None
    write_json_atomic(path, [t.model_dump(mode="json") for t in [*tickets, ticket]])
    return ticket


def close_ticket(profiles: dict[str, Profile], ticket_id: str, resolution: str, path: Path = TICKETS_PATH) -> Ticket:
    """Marca o ticket como resolvido e grava a resolução (RF18); nada é gravado se algo for inválido.

    A resolução passa a entrar no histórico dos próximos pedidos do cliente, e o hash
    dos tickets de contexto na chave de cache invalida o que dependia da versão antiga.
    """
    tickets = load_tickets(profiles, path)
    index = next((i for i, t in enumerate(tickets) if t.id == ticket_id), None)
    if index is None:
        raise DataError(f"Ticket {ticket_id!r} não existe.")
    current = tickets[index]
    if current.status == "resolvido":
        raise DataError(f"Ticket {ticket_id} já está resolvido. Resolução atual: {current.resolution}")
    try:
        closed = Ticket.model_validate({**current.model_dump(), "status": "resolvido", "resolution": resolution})
    except ValidationError as exc:
        details = "; ".join(_describe_error(e) for e in exc.errors())
        raise DataError(f"Resolução inválida: {details}") from None
    tickets[index] = closed
    write_json_atomic(path, [t.model_dump(mode="json") for t in tickets])
    return closed


def get_profile(profiles: dict[str, Profile], profile_id: str) -> Profile:
    """Retorna o perfil pelo id ou lança DataError listando os disponíveis."""
    try:
        return profiles[profile_id]
    except KeyError:
        available = ", ".join(profiles)
        raise DataError(f"Perfil {profile_id!r} não existe. Disponíveis: {available}.") from None


# --- Execuções (runs/) -------------------------------------------------------


def run_folder(customer_id: str, ticket_id: str, runs_dir: Path = RUNS_DIR) -> Path:
    return runs_dir / customer_id / ticket_id


def save_run(run: Run, runs_dir: Path = RUNS_DIR) -> Path:
    """Grava a execução de forma atômica e retorna o caminho do JSON."""
    stamp = run.timestamp.strftime("%Y%m%dT%H%M%S")
    key_prefix = run.cache_key[:CACHE_KEY_PREFIX_LENGTH]
    name = f"{stamp}_{run.content_type}_{run.prompt_version}_{run.run_id[:8]}_{key_prefix}.json"
    path = run_folder(run.customer_id, run.ticket.id, runs_dir) / name
    write_json_atomic(path, run.model_dump(mode="json"))
    return path


def update_run(path: Path, run: Run) -> None:
    """Regrava uma execução existente (ex.: para anexar a avaliação) de forma atômica."""
    if not path.exists():
        raise DataError(f"Execução não encontrada: {path}")
    write_json_atomic(path, run.model_dump(mode="json"))


def run_files_with_key(customer_id: str, cache_key: str, runs_dir: Path = RUNS_DIR) -> list[Path]:
    """Execuções do cliente (em qualquer ticket) cujo nome bate com o prefixo da chave, da mais nova à mais antiga.

    A chave não depende do ID do ticket, só do conteúdo: dois tickets do mesmo cliente com o
    mesmo texto e o mesmo contexto reaproveitam a mesma resposta.
    """
    folder = runs_dir / customer_id
    if not folder.is_dir():
        return []
    files = folder.glob(f"*/*_{cache_key[:CACHE_KEY_PREFIX_LENGTH]}.json")
    return sorted(files, key=lambda p: p.name, reverse=True)  # o nome começa pelo timestamp


def list_customer_runs(customer_id: str, runs_dir: Path = RUNS_DIR) -> list[tuple[Path, Run]]:
    """Todas as execuções de um cliente (RF15), em ordem cronológica; arquivos ilegíveis são ignorados."""
    folder = runs_dir / customer_id
    if not folder.is_dir():
        return []
    runs: list[tuple[Path, Run]] = []
    for path in folder.glob("*/*.json"):
        try:
            runs.append((path, Run.model_validate_json(path.read_text(encoding="utf-8"))))
        except (OSError, ValidationError) as exc:
            logger.warning("Ignorando execução ilegível em %s: %s", path, type(exc).__name__)
    runs.sort(key=lambda item: item[1].timestamp)
    return runs
