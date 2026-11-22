"""Testes do GeminiClient com o SDK simulado (RNF04, RNF08)."""

import pytest
from google.genai import errors, types

from app.llm_client import (
    GeminiClient,
    LLMAuthError,
    LLMBlockedError,
    LLMEmptyResponseError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    _TIMEOUT_ERRORS,
)
from app.models import Diagnosis


def _response(text='{"ok": true}', finish="STOP", block_reason=None, candidates=True):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=getattr(types.FinishReason, finish),
            )
        ]
        if candidates
        else [],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=block_reason)
        if block_reason
        else None,
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=120, candidates_token_count=45),
    )


def _api_error(cls, code, message="erro"):
    return cls(code, {"error": {"code": code, "message": message, "status": "X"}})


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSDK:
    def __init__(self, *outcomes):
        self.models = FakeModels(outcomes)


def _client(*outcomes):
    sleeps: list[float] = []
    sdk = FakeSDK(*outcomes)
    client = GeminiClient(
        api_key="x", model="gemini-test", temperature=0.2, max_tokens=2048, sleep=sleeps.append, client=sdk
    )
    return client, sdk, sleeps


def _generate(client):
    return client.generate("sistema", "usuário", Diagnosis)


def test_success_sends_expected_config():
    client, sdk, sleeps = _client(_response('{"a": 1}'))
    result = _generate(client)

    assert result.text == '{"a": 1}'
    assert (result.input_tokens, result.output_tokens, result.attempts) == (120, 45, 1)
    call = sdk.models.calls[0]
    config = call["config"]
    assert call["model"] == "gemini-test" and call["contents"] == "usuário"
    assert config.system_instruction == "sistema"
    assert config.temperature == 0.2 and config.max_output_tokens == 2048
    assert config.response_mime_type == "application/json"
    assert config.response_schema is Diagnosis
    assert config.thinking_config.thinking_budget == 0
    assert sleeps == []
    assert client.config == {"temperature": 0.2, "max_tokens": 2048, "thinking_budget": 0}


def test_real_sdk_client_uses_v1beta_and_timeout():
    # Constrói o cliente do SDK de verdade (sem chamada de rede) e confere as opções HTTP.
    client = GeminiClient(api_key="x", model="m", temperature=0.2, max_tokens=10, timeout_s=30)
    options = client._client._api_client._http_options
    assert options.api_version == "v1beta"
    assert options.timeout == 30_000


def test_rate_limit_retries_with_exponential_backoff():
    client, sdk, sleeps = _client(
        _api_error(errors.ClientError, 429), _api_error(errors.ClientError, 429), _response()
    )
    assert _generate(client).attempts == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limit_gives_up_after_max_attempts():
    client, sdk, sleeps = _client(*[_api_error(errors.ClientError, 429)] * 3)
    with pytest.raises(LLMRateLimitError, match="Desisti após 3 tentativas"):
        _generate(client)
    assert len(sdk.models.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_server_error_is_retried():
    client, _, sleeps = _client(_api_error(errors.ServerError, 503), _response())
    assert _generate(client).attempts == 2
    assert sleeps == [1.0]


def test_timeout_is_retried_then_clear_error():
    timeout = _TIMEOUT_ERRORS[-1]("tempo esgotado")
    client, _, _ = _client(timeout, timeout, timeout)
    with pytest.raises(LLMTimeoutError, match="não respondeu a tempo"):
        _generate(client)


@pytest.mark.parametrize(
    "error",
    [
        _api_error(errors.ClientError, 400, "API key not valid. Please pass a valid API key."),
        _api_error(errors.ClientError, 403, "Permission denied"),
    ],
)
def test_auth_error_is_not_retried(error):
    client, sdk, sleeps = _client(error)
    with pytest.raises(LLMAuthError, match="GEMINI_API_KEY inválida"):
        _generate(client)
    assert len(sdk.models.calls) == 1 and sleeps == []


def test_unknown_model():
    client, _, _ = _client(_api_error(errors.ClientError, 404, "models/x is not found"))
    with pytest.raises(LLMError, match="Modelo 'gemini-test' não encontrado"):
        _generate(client)


def test_blocked_prompt():
    client, _, _ = _client(_response(candidates=False, block_reason=types.BlockedReason.SAFETY))
    with pytest.raises(LLMBlockedError, match="Pedido bloqueado.*SAFETY"):
        _generate(client)


def test_blocked_answer():
    client, _, _ = _client(_response(finish="SAFETY"))
    with pytest.raises(LLMBlockedError, match="Resposta bloqueada"):
        _generate(client)


def test_truncated_answer():
    client, _, _ = _client(_response('{"hypo', finish="MAX_TOKENS"))
    with pytest.raises(LLMEmptyResponseError, match="limite de 2048 tokens"):
        _generate(client)


@pytest.mark.parametrize("response", [_response(text="   "), _response(candidates=False)])
def test_empty_answer(response):
    client, _, _ = _client(response)
    with pytest.raises(LLMEmptyResponseError, match="sem conteúdo"):
        _generate(client)
