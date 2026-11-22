"""Montagem do prompt por camadas, versão e histórico do cliente."""

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from app.models import Profile, Ticket

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

CONTENT_TYPES = ("diagnosis", "resolution_steps", "clarifying_questions", "flow_summary")
BASE_TEMPLATES = ("system.md", "context.md")
PROFILE_RULES_FILE = "profile_rules.json"
PROFILE_RULE_FIELDS = {
    "technical_level": "Nível técnico",
    "communication_style": "Estilo",
    "channel": "Canal",
}

MAX_HISTORY = 3
NO_HISTORY_TEXT = "Nenhum atendimento anterior."


class PromptError(Exception):
    """Versão, tipo ou template inválido; a mensagem é legível para o usuário."""


@dataclass(frozen=True)
class BuiltPrompt:
    """Prompt pronto para o LLM: instrução de sistema + mensagem do usuário."""

    version: str
    content_type: str
    system: str
    user: str
    context_ticket_ids: tuple[str, ...]


def list_versions(prompts_dir: Path = PROMPTS_DIR) -> list[str]:
    """Versões disponíveis = subpastas de prompts/ (criar v3 = criar a pasta)."""
    if not prompts_dir.is_dir():
        return []
    return sorted(p.name for p in prompts_dir.iterdir() if p.is_dir() and not p.name.startswith("."))


def get_customer_history(tickets: list[Ticket], ticket: Ticket, limit: int = MAX_HISTORY) -> list[Ticket]:
    """Até `limit` tickets anteriores do mesmo cliente, do mais recente ao mais antigo."""
    previous = [
        t
        for t in tickets
        if t.customer_id == ticket.customer_id and t.id != ticket.id and t.created_at < ticket.created_at
    ]
    previous.sort(key=lambda t: t.created_at, reverse=True)
    return previous[:limit]


def _format_datetime(value) -> str:
    return value.strftime("%d/%m/%Y %H:%M")


def _format_history(history: list[Ticket]) -> str:
    if not history:
        return NO_HISTORY_TEXT
    lines = []
    for t in history:
        line = f"- {t.id} ({_format_datetime(t.created_at)}, {t.status}): {t.title}"
        if t.resolution:
            line += f" | Resolução: {t.resolution}"
        lines.append(line)
    return "\n".join(lines)


def _load_profile_rules(version_dir: Path, profile: Profile) -> str | None:
    """Traduz o perfil em regras, se a versão tiver profile_rules.json; senão None."""
    path = version_dir / PROFILE_RULES_FILE
    if not path.exists():
        return None
    try:
        rules: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptError(f"JSON inválido em {path} (linha {exc.lineno}): {exc.msg}") from None
    lines = []
    for field, label in PROFILE_RULE_FIELDS.items():
        value = getattr(profile, field)
        try:
            lines.append(f"- {label} ({value}): {rules[field][value]}")
        except KeyError:
            raise PromptError(f"{path} não tem regra para {field}={value!r}.") from None
    return "\n".join(lines)


def _render(path: Path, variables: dict[str, Any]) -> str:
    template = Template(path.read_text(encoding="utf-8"))
    try:
        return template.substitute(variables).strip()
    except KeyError as exc:
        raise PromptError(f"Placeholder ${exc.args[0]} não existe (em {path}).") from None
    except ValueError as exc:
        raise PromptError(f"Placeholder mal formado em {path}: {exc}. Use $$ para um $ literal.") from None


def build_prompt(
    profile: Profile,
    ticket: Ticket,
    tickets: list[Ticket],
    content_type: str,
    version: str,
    prompts_dir: Path = PROMPTS_DIR,
) -> BuiltPrompt:
    """Monta o prompt em camadas: persona (system) + contexto + tarefa/formato do tipo."""
    if content_type not in CONTENT_TYPES:
        raise PromptError(f"Tipo {content_type!r} inválido. Disponíveis: {', '.join(CONTENT_TYPES)}.")
    versions = list_versions(prompts_dir)
    if version not in versions:
        raise PromptError(f"Versão {version!r} não existe. Disponíveis: {', '.join(versions) or 'nenhuma'}.")
    if ticket.customer_id != profile.id:
        raise PromptError(f"Ticket {ticket.id} pertence a {ticket.customer_id}, não a {profile.id}.")

    version_dir = prompts_dir / version
    for name in (*BASE_TEMPLATES, f"{content_type}.md"):
        if not (version_dir / name).exists():
            raise PromptError(f"Template ausente: {version_dir / name}")

    history = get_customer_history(tickets, ticket)
    variables: dict[str, Any] = {
        "customer_name": profile.name,
        "technical_level": profile.technical_level,
        "communication_style": profile.communication_style,
        "channel": profile.channel,
        "ticket_id": ticket.id,
        "ticket_created_at": _format_datetime(ticket.created_at),
        "ticket_title": ticket.title,
        "ticket_description": ticket.description,
        "customer_history": _format_history(history),
    }
    profile_rules = _load_profile_rules(version_dir, profile)
    if profile_rules is not None:
        variables["profile_rules"] = profile_rules

    system = _render(version_dir / "system.md", variables)
    context = _render(version_dir / "context.md", variables)
    task = _render(version_dir / f"{content_type}.md", variables)
    return BuiltPrompt(
        version=version,
        content_type=content_type,
        system=system,
        user=f"{context}\n\n{task}",
        context_ticket_ids=tuple(t.id for t in history),
    )
