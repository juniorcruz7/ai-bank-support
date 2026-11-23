"""Testes do fluxo completo: prompt -> cache -> LLM -> runs/ (RF09, RF10, RNF06, RNF11)."""

import json
import logging
import shutil

import pytest

from app.generators import OutputValidationError
from app.models import Diagnosis, Run
from app.prompt_builder import PROMPTS_DIR
from app.service import customer_ticket_numbers, run_content
from app.storage import load_profiles, load_tickets
from tests.fakes import AGENT, VALID_DIAGNOSIS, FakeClient

PROFILES = load_profiles()
TICKETS = load_tickets(PROFILES)
T001 = next(t for t in TICKETS if t.id == "T001")


def _run(client, runs_dir, version="v2", **kwargs):
    return run_content(
        AGENT, PROFILES["C001"], T001, TICKETS, "diagnosis", version, client, runs_dir=runs_dir, **kwargs
    )


def _run_files(runs_dir):
    return sorted((runs_dir / "C001" / "T001").glob("*.json"))


def test_first_run_calls_llm_and_saves_record(tmp_path):
    client = FakeClient(VALID_DIAGNOSIS)
    result = _run(client, tmp_path)

    assert len(client.calls) == 1
    assert isinstance(result.output, Diagnosis)
    assert result.path.parent == tmp_path / "C001" / "T001"
    assert result.path.name.endswith(f"_{result.run.cache_key[:16]}.json")
    assert "_diagnosis_v2_" in result.path.name

    saved = json.loads(result.path.read_text(encoding="utf-8"))
    run = Run.model_validate(saved)
    assert run.cache_hit is False
    assert run.customer_id == "C001" and run.ticket.id == "T001"
    assert run.provider == "fake" and run.model == "fake-model"
    assert run.config["temperature"] == 0.2  # RNF06: modelo e configs registrados
    assert run.context_ticket_ids == ["T005"]
    assert run.usage.input_tokens == 100 and run.usage.llm_calls == 1
    assert run.evaluation is None
    assert saved["timestamp"].endswith("-03:00")  # America/Recife


def test_second_identical_run_is_cache_hit(tmp_path, caplog):
    first = _run(FakeClient(VALID_DIAGNOSIS), tmp_path)
    client = FakeClient()  # sem respostas: falha se for chamado

    with caplog.at_level(logging.INFO, logger="app.service"):
        second = _run(client, tmp_path)

    assert client.calls == []
    assert second.run.cache_hit is True
    assert second.run.usage is None
    assert second.run.cache_key == first.run.cache_key
    assert second.run.run_id != first.run.run_id
    assert second.output == first.output
    assert len(_run_files(tmp_path)) == 2
    assert "cache hit" in caplog.text


def test_cache_miss_is_logged_with_latency_and_tokens(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="app.service"):
        _run(FakeClient(VALID_DIAGNOSIS), tmp_path)
    assert "cache miss" in caplog.text
    assert "7 ms" in caplog.text and "100/50" in caplog.text


def test_other_version_is_cache_miss(tmp_path):
    _run(FakeClient(VALID_DIAGNOSIS), tmp_path, version="v2")
    client = FakeClient(VALID_DIAGNOSIS)
    assert _run(client, tmp_path, version="v1").run.cache_hit is False
    assert len(client.calls) == 1


def test_template_edit_invalidates_cache(tmp_path):
    prompts = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, prompts)
    runs = tmp_path / "runs"
    _run(FakeClient(VALID_DIAGNOSIS), runs, prompts_dir=prompts)

    template = prompts / "v2" / "diagnosis.md"
    template.write_text(template.read_text(encoding="utf-8") + "\nSeja breve.", encoding="utf-8")
    client = FakeClient(VALID_DIAGNOSIS)
    assert _run(client, runs, prompts_dir=prompts).run.cache_hit is False


def test_corrupted_cache_file_is_ignored(tmp_path, caplog):
    first = _run(FakeClient(VALID_DIAGNOSIS), tmp_path)
    first.path.write_text("{ corrompido", encoding="utf-8")

    client = FakeClient(VALID_DIAGNOSIS)
    with caplog.at_level(logging.WARNING, logger="app.cache"):
        result = _run(client, tmp_path)
    assert result.run.cache_hit is False
    assert len(client.calls) == 1
    assert "ilegível" in caplog.text


def test_failed_generation_saves_nothing(tmp_path):
    with pytest.raises(OutputValidationError):
        _run(FakeClient("{}", "{}"), tmp_path)
    assert _run_files(tmp_path) == []


def test_customer_ticket_numbers_follow_opening_order():
    # T005 (out/2026) é o 1º ticket da Maria e T001 (nov/2026) o 2º, mesmo com ID global menor.
    assert customer_ticket_numbers(TICKETS, "C001") == {"T005": 1, "T001": 2}
    assert customer_ticket_numbers(TICKETS, "C004") == {}


# --- cache entre tickets do mesmo cliente --------------------------------------

from datetime import datetime, timedelta  # noqa: E402

from app.config import APP_TIMEZONE  # noqa: E402
from app.prompt_builder import get_customer_history  # noqa: E402
from app.storage import TICKETS_PATH, add_ticket, close_ticket  # noqa: E402
from tests.fakes import VALID_STEPS  # noqa: E402

PIX_TITLE = "Pix não caiu"
PIX_TEXT = "Meu filho fez um Pix ontem e não caiu."


def _renata_tickets(tmp_path, *texts):
    """Abre tickets da Renata (C004) com 5 minutos de intervalo e devolve (tickets abertos, todos)."""
    path = tmp_path / "tickets.json"
    shutil.copy(TICKETS_PATH, path)
    start = datetime(2026, 11, 23, 10, 0, tzinfo=APP_TIMEZONE)
    opened = [
        add_ticket(PROFILES, "C004", title, text, path, now=start + timedelta(minutes=5 * i))
        for i, (title, text) in enumerate(texts)
    ]
    return opened, path


def _steps(ticket, tickets, client, runs_dir):
    return run_content(AGENT, PROFILES["C004"], ticket, tickets, "resolution_steps", "v2", client, runs_dir=runs_dir)


def test_duplicate_open_ticket_reuses_cached_answer(tmp_path):
    (first, second), path = _renata_tickets(tmp_path, (PIX_TITLE, PIX_TEXT), (PIX_TITLE, f"  {PIX_TEXT}\n"))
    tickets = load_tickets(PROFILES, path)
    client = FakeClient(VALID_STEPS)

    original = _steps(first, tickets, client, tmp_path / "runs")
    repeated = _steps(second, tickets, client, tmp_path / "runs")

    assert len(client.calls) == 1  # o 2º ticket não chamou a API
    assert repeated.run.cache_hit and repeated.run.cache_key == original.run.cache_key
    assert repeated.run.ticket.id == second.id  # a execução fica registrada no próprio ticket
    assert repeated.path.parent.name == second.id
    assert repeated.run.output == original.run.output
    assert repeated.run.context_ticket_ids == []  # o duplicado aberto não entra como histórico


def test_resolved_duplicate_stays_in_context_and_is_generated_again(tmp_path):
    (first, second), path = _renata_tickets(tmp_path, (PIX_TITLE, PIX_TEXT), (PIX_TITLE, PIX_TEXT))
    client = FakeClient(VALID_STEPS, VALID_STEPS)
    _steps(first, load_tickets(PROFILES, path), client, tmp_path / "runs")
    close_ticket(PROFILES, first.id, "Pix devolvido: chave do destinatário estava errada.", path)

    tickets = load_tickets(PROFILES, path)
    result = _steps(second, tickets, client, tmp_path / "runs")

    # A resolução é informação nova: o ticket resolvido entra no contexto e a resposta é gerada de novo.
    assert result.run.context_ticket_ids == [first.id]
    assert not result.run.cache_hit and len(client.calls) == 2


def test_different_text_does_not_reuse_cache(tmp_path):
    (first, second), path = _renata_tickets(tmp_path, (PIX_TITLE, PIX_TEXT), (PIX_TITLE, "Meu cartão foi recusado."))
    tickets = load_tickets(PROFILES, path)
    client = FakeClient(VALID_STEPS, VALID_STEPS)
    _steps(first, tickets, client, tmp_path / "runs")
    result = _steps(second, tickets, client, tmp_path / "runs")
    assert not result.run.cache_hit and len(client.calls) == 2
    assert result.run.context_ticket_ids == [first.id]  # ticket diferente continua como histórico


def test_cache_is_not_shared_between_customers(tmp_path):
    client = FakeClient(VALID_DIAGNOSIS, VALID_DIAGNOSIS)
    _run(client, tmp_path)  # T001 da Maria
    lucas_copy = T001.model_copy(update={"id": "T900", "customer_id": "C002"})
    run_content(AGENT, PROFILES["C002"], lucas_copy, TICKETS, "diagnosis", "v2", client, runs_dir=tmp_path)
    assert len(client.calls) == 2


def test_history_skips_only_open_duplicates():
    current = next(t for t in TICKETS if t.id == "T001")
    later = current.model_copy(
        update={"id": "T901", "created_at": current.created_at + timedelta(days=1), "description": current.description}
    )
    history = get_customer_history([*TICKETS, later], later)
    assert "T001" not in [t.id for t in history]  # mesmo texto e ainda aberto
    assert "T005" in [t.id for t in history]  # outro problema, resolvido: continua
