"""Testes do fluxo completo: prompt -> cache -> LLM -> runs/ (RF09, RF10, RNF06, RNF11)."""

import json
import logging
import shutil

import pytest

from app.generators import OutputValidationError
from app.models import Diagnosis, Run
from app.prompt_builder import PROMPTS_DIR
from app.service import run_content
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
