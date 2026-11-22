"""Chave de cache SHA-256 e busca de execuções em runs/."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models import Profile, Run, Ticket
from app.prompt_builder import BuiltPrompt
from app.storage import RUNS_DIR, run_files_with_key

logger = logging.getLogger(__name__)


def _sha256_json(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """Colapsa espaços e quebras de linha: diferenças só de espaço não mudam a chave."""
    return " ".join(text.split())


def cache_key_components(
    profile: Profile,
    ticket: Ticket,
    prompt: BuiltPrompt,
    history: list[Ticket],
    provider: str,
    model: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Tudo que define uma entrada equivalente (RNF02)."""
    return {
        "profile": profile.model_dump(mode="json"),
        "ticket": {"title": normalize_text(ticket.title), "description": normalize_text(ticket.description)},
        "content_type": prompt.content_type,
        "prompt_version": prompt.version,
        "template_hash": prompt.template_hash,
        "provider": provider,
        "model": model,
        "config": config,
        "context_tickets": [{"id": t.id, "hash": _sha256_json(t.model_dump(mode="json"))} for t in history],
    }


def compute_cache_key(*args, **kwargs) -> str:
    """SHA-256 dos componentes da chave (mesmos argumentos de cache_key_components)."""
    return _sha256_json(cache_key_components(*args, **kwargs))


def find_cached_run(customer_id: str, ticket_id: str, cache_key: str, runs_dir: Path = RUNS_DIR) -> Run | None:
    """Execução já gravada com a mesma chave; arquivos ilegíveis são ignorados (viram cache miss)."""
    for path in run_files_with_key(customer_id, ticket_id, cache_key, runs_dir):
        try:
            run = Run.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            logger.warning("Ignorando execução ilegível em %s: %s", path, type(exc).__name__)
            continue
        if run.cache_key == cache_key:
            return run
    return None
