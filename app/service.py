"""Camada de serviço: monta o prompt, consulta o cache, gera e grava a execução.

Toda função que lê ou altera ticket, execução ou histórico recebe o usuário logado
e checa a permissão antes de qualquer outra coisa (RNF14)."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app import auth
from app.cache import compute_cache_key, find_cached_run
from app.config import APP_TIMEZONE
from app.generators import OUTPUT_MODELS, generate
from app.llm_client import LLMClient
from app.models import Profile, Run, RunTicket, RunUsage, Ticket, User
from app.prompt_builder import PROMPTS_DIR, build_prompt, get_customer_history
from app.storage import RUNS_DIR, TICKETS_PATH, add_ticket, close_ticket, list_customer_runs, save_run

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    run: Run
    path: Path
    output: BaseModel


def _cached_output(run: Run | None, content_type: str) -> BaseModel | None:
    if run is None:
        return None
    try:
        return OUTPUT_MODELS[content_type].model_validate(run.output)
    except ValidationError:
        logger.warning("Saída em cache inválida para o modelo atual (run %s); gerando de novo.", run.run_id)
        return None


def run_content(
    user: User,
    profile: Profile,
    ticket: Ticket,
    tickets: list[Ticket],
    content_type: str,
    version: str,
    client: LLMClient,
    runs_dir: Path = RUNS_DIR,
    prompts_dir: Path = PROMPTS_DIR,
) -> RunResult:
    """Fluxo completo de uma execução; só saída validada é gravada em runs/."""
    auth.require_customer_access(user, ticket.customer_id)
    auth.require_content_type(user, content_type)
    auth.require_version(user, version)
    prompt = build_prompt(profile, ticket, tickets, content_type, version, prompts_dir)
    history = get_customer_history(tickets, ticket)
    cache_key = compute_cache_key(profile, ticket, prompt, history, client.provider, client.model, client.config)
    label = f"{ticket.id} {content_type} {version}"

    output = _cached_output(find_cached_run(profile.id, ticket.id, cache_key, runs_dir), content_type)
    usage = None
    if output is not None:
        logger.info("cache hit: %s (chave %s)", label, cache_key[:12])
    else:
        logger.info("cache miss: %s (chave %s); chamando %s/%s", label, cache_key[:12], client.provider, client.model)
        result = generate(prompt, client, prompts_dir)
        output = result.output
        responses = result.responses
        usage = RunUsage(
            input_tokens=_sum_or_none(r.input_tokens for r in responses),
            output_tokens=_sum_or_none(r.output_tokens for r in responses),
            latency_ms=sum(r.latency_ms for r in responses),
            llm_calls=len(responses),
            http_attempts=sum(r.attempts for r in responses),
        )
        logger.info(
            "LLM ok: %s em %d ms, tokens entrada/saída %s/%s, chamadas %d",
            label,
            usage.latency_ms,
            usage.input_tokens,
            usage.output_tokens,
            usage.llm_calls,
        )

    run = Run(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(APP_TIMEZONE),
        customer_id=profile.id,
        ticket=RunTicket(id=ticket.id, title=ticket.title, text=ticket.description),
        content_type=content_type,
        prompt_version=version,
        provider=client.provider,
        model=client.model,
        config=client.config,
        context_ticket_ids=list(prompt.context_ticket_ids),
        template_hash=prompt.template_hash,
        cache_key=cache_key,
        cache_hit=usage is None,
        output=output.model_dump(mode="json"),
        usage=usage,
    )
    path = save_run(run, runs_dir)
    return RunResult(run=run, path=path, output=output)


def _sum_or_none(values) -> int | None:
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


# --- tickets e histórico com checagem de acesso (RF20–RF22) -----------------


def visible_profiles(user: User, profiles: dict[str, Profile]) -> dict[str, Profile]:
    """Atendente vê todos os clientes; cliente vê só o próprio perfil."""
    return profiles if auth.is_agent(user) else {k: v for k, v in profiles.items() if k == user.customer_id}


def visible_tickets(user: User, tickets: list[Ticket]) -> list[Ticket]:
    return tickets if auth.is_agent(user) else [t for t in tickets if t.customer_id == user.customer_id]


def open_ticket(
    user: User, profiles: dict[str, Profile], customer_id: str, title: str, description: str, path: Path = TICKETS_PATH
) -> Ticket:
    """Cliente abre ticket só para si; atendente para qualquer cliente."""
    auth.require_customer_access(user, customer_id)
    return add_ticket(profiles, customer_id, title, description, path=path)


def resolve_ticket(
    user: User, profiles: dict[str, Profile], ticket_id: str, resolution: str, path: Path = TICKETS_PATH
) -> Ticket:
    """Fechar ticket é só do atendente (RF18, RF21)."""
    auth.require_agent(user, "fechar ticket")
    return close_ticket(profiles, ticket_id, resolution, path=path)


def customer_history(
    user: User, customer_id: str, tickets: list[Ticket], runs_dir: Path = RUNS_DIR
) -> tuple[list[Ticket], list[tuple[Path, Run]]]:
    """Tickets e execuções de um cliente (RF15, RF22).

    Para a conta de cliente, só as respostas que ele pode receber: sem diagnóstico,
    fluxograma nem as notas internas da rubrica.
    """
    auth.require_customer_access(user, customer_id)
    own = [t for t in tickets if t.customer_id == customer_id]
    runs = list_customer_runs(customer_id, runs_dir)
    if not auth.is_agent(user):
        allowed = auth.allowed_content_types(user)
        runs = [(p, r.model_copy(update={"evaluation": None})) for p, r in runs if r.content_type in allowed]
    return own, runs
