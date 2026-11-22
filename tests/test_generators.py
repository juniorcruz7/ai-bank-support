"""Testes dos geradores com cliente LLM falso (RNF03, RNF08)."""

import json

import pytest

from app.generators import (
    OutputValidationError,
    generate,
    generate_diagnosis,
    generate_flow_summary,
)
from app.models import Diagnosis
from app.prompt_builder import build_prompt
from app.storage import load_profiles, load_tickets
from tests.fakes import VALID_DIAGNOSIS, FakeClient

PROFILES = load_profiles()
TICKETS = load_tickets(PROFILES)
T001 = next(t for t in TICKETS if t.id == "T001")


def _prompt(content_type="diagnosis", version="v2"):
    return build_prompt(PROFILES["C001"], T001, TICKETS, content_type, version)


def test_valid_output_first_try():
    client = FakeClient(VALID_DIAGNOSIS)
    result = generate(_prompt(), client)
    assert isinstance(result.output, Diagnosis)
    assert result.validation_attempts == 1
    assert client.calls[0]["response_model"] is Diagnosis


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_invalid_then_valid_retries_once_with_error(version):
    client = FakeClient('{"hypotheses": "quebrado"', VALID_DIAGNOSIS)
    prompt = _prompt(version=version)
    result = generate(prompt, client)
    assert result.validation_attempts == 2
    assert client.calls[0]["user"] == prompt.user
    retry_user = client.calls[1]["user"]
    assert retry_user.startswith(prompt.user)
    assert "não passou na validação" in retry_user


def test_invalid_twice_raises_clear_error():
    client = FakeClient("{}", "não é json")
    with pytest.raises(OutputValidationError, match="'diagnosis' veio fora do formato 2 vezes"):
        generate(_prompt(), client)
    assert len(client.calls) == 2


def test_semantic_error_goes_to_retry_note():
    cyclic = json.dumps(
        {
            "decision_tree": {
                "nodes": [
                    {"id": "n1", "type": "decision", "text": "A?", "yes": "n2", "no": "n2"},
                    {"id": "n2", "type": "decision", "text": "B?", "yes": "n1", "no": "n1"},
                ]
            }
        }
    )
    valid = json.dumps({"decision_tree": {"nodes": [{"id": "n1", "type": "action", "text": "Fazer X"}]}})
    client = FakeClient(cyclic, valid)
    generate_flow_summary(_prompt("flow_summary"), client)
    assert "ciclo" in client.calls[1]["user"]


def test_typed_function_rejects_wrong_prompt_type():
    with pytest.raises(ValueError, match="'flow_summary' usado para gerar 'diagnosis'"):
        generate_diagnosis(_prompt("flow_summary"), FakeClient(VALID_DIAGNOSIS))
