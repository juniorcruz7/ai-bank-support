"""Leitura e escrita atômica de execuções e tickets."""

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.models import Profile, Ticket

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_PATH = DATA_DIR / "profiles.json"
TICKETS_PATH = DATA_DIR / "tickets.json"

MIN_PROFILES = 3
MAX_PROFILES = 5


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
        raise DataError(
            f"{path.name} deve ter de {MIN_PROFILES} a {MAX_PROFILES} perfis; encontrados {len(profiles)}."
        )
    _ensure_unique_ids(profiles, path.name)
    return {profile.id: profile for profile in profiles}


def load_tickets(profiles: dict[str, Profile], path: Path = TICKETS_PATH) -> list[Ticket]:
    """Carrega e valida os tickets; todo ticket precisa pertencer a um perfil existente."""
    tickets = _validate_items(_read_json_list(path), Ticket, "Ticket", path.name)
    _ensure_unique_ids(tickets, path.name)
    for ticket in tickets:
        if ticket.customer_id not in profiles:
            raise DataError(
                f"Ticket {ticket.id!r} aponta para cliente inexistente: {ticket.customer_id!r}."
            )
    return tickets


def get_profile(profiles: dict[str, Profile], profile_id: str) -> Profile:
    """Retorna o perfil pelo id ou lança DataError listando os disponíveis."""
    try:
        return profiles[profile_id]
    except KeyError:
        available = ", ".join(profiles)
        raise DataError(f"Perfil {profile_id!r} não existe. Disponíveis: {available}.") from None
