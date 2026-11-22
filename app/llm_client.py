"""Interface única de LLM e implementação Gemini."""

import importlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.config import Settings

DEFAULT_TIMEOUT_S = 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_S = 1.0
# A API "v1" (padrão do SDK) recusa system_instruction; a v1beta aceita.
GEMINI_API_VERSION = "v1beta"


def _timeout_errors() -> tuple[type[BaseException], ...]:
    # O SDK usa httpx ou httpx2 por baixo (dependências dele, não nossas):
    # pega a exceção de timeout do que estiver instalado.
    found: list[type[BaseException]] = [TimeoutError]
    for name in ("httpx", "httpx2"):
        try:
            found.append(importlib.import_module(name).TimeoutException)
        except (ImportError, AttributeError):
            pass
    return tuple(found)


_TIMEOUT_ERRORS = _timeout_errors()
_BLOCKED_FINISH_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}


class LLMError(Exception):
    """Falha ao obter resposta do LLM; a mensagem é legível para o usuário."""


class LLMAuthError(LLMError):
    """Chave de API inválida ou sem permissão."""


class LLMRateLimitError(LLMError):
    """Limite de requisições (429) esgotado mesmo após as retentativas."""


class LLMTimeoutError(LLMError):
    """O provedor não respondeu a tempo, mesmo após as retentativas."""


class LLMBlockedError(LLMError):
    """Pedido ou resposta bloqueados pelos filtros do provedor."""


class LLMEmptyResponseError(LLMError):
    """O provedor respondeu sem conteúdo ou com a resposta cortada."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    attempts: int


class LLMClient(Protocol):
    """Contrato que todo provedor implementa (RNF08)."""

    provider: str
    model: str

    @property
    def config(self) -> dict[str, Any]: ...

    def generate(self, system: str, user: str, response_model: type[BaseModel]) -> LLMResponse: ...


class _Retryable(Exception):
    def __init__(self, error: LLMError):
        self.error = error


class GeminiClient:
    """Cliente Gemini com saída JSON por schema, timeout e backoff exponencial."""

    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_s: float = DEFAULT_BASE_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
        client: Any = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self._sleep = sleep
        # O SDK não refaz chamadas por padrão; as retentativas ficam neste cliente.
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version=GEMINI_API_VERSION, timeout=int(timeout_s * 1000)),
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "GeminiClient":
        return cls(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            **kwargs,
        )

    @property
    def config(self) -> dict[str, Any]:
        return {"temperature": self.temperature, "max_tokens": self.max_tokens, "thinking_budget": 0}

    def generate(self, system: str, user: str, response_model: type[BaseModel]) -> LLMResponse:
        request_config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json",
            response_schema=response_model,
            # Sem "pensamento": resposta mais previsível e nenhum raciocínio interno guardado.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        for attempt in range(1, self.max_attempts + 1):
            start = time.perf_counter()
            try:
                response = self._call(user, request_config)
            except _Retryable as retry:
                if attempt == self.max_attempts:
                    raise type(retry.error)(f"{retry.error} Desisti após {attempt} tentativas.") from None
                self._sleep(self.base_delay_s * 2 ** (attempt - 1))
                continue
            latency_ms = int((time.perf_counter() - start) * 1000)
            return self._to_response(response, latency_ms, attempt)
        raise AssertionError("inalcançável")

    def _call(self, user: str, request_config: types.GenerateContentConfig):
        try:
            return self._client.models.generate_content(model=self.model, contents=user, config=request_config)
        except errors.ClientError as exc:
            message = str(exc.message or exc)
            if exc.code == 429:
                raise _Retryable(LLMRateLimitError("Limite de requisições do Gemini atingido (429).")) from None
            if exc.code in (401, 403) or "API key" in message:
                raise LLMAuthError("GEMINI_API_KEY inválida ou sem permissão. Confira a chave no .env.") from None
            if exc.code == 404:
                raise LLMError(f"Modelo {self.model!r} não encontrado. Confira GEMINI_MODEL no .env.") from None
            raise LLMError(f"O Gemini recusou a requisição ({exc.code}): {message}") from None
        except errors.ServerError as exc:
            raise _Retryable(LLMError(f"Gemini indisponível no momento ({exc.code}).")) from None
        except _TIMEOUT_ERRORS:
            raise _Retryable(LLMTimeoutError("O Gemini não respondeu a tempo (timeout).")) from None

    def _to_response(self, response, latency_ms: int, attempts: int) -> LLMResponse:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and feedback.block_reason:
            raise LLMBlockedError(f"Pedido bloqueado pelo Gemini ({_enum_name(feedback.block_reason)}).")
        if not response.candidates:
            raise LLMEmptyResponseError("O Gemini respondeu sem conteúdo.")

        finish = _enum_name(response.candidates[0].finish_reason)
        if finish in _BLOCKED_FINISH_REASONS:
            raise LLMBlockedError(f"Resposta bloqueada pelo Gemini ({finish}).")
        if finish == "MAX_TOKENS":
            raise LLMEmptyResponseError(
                f"Resposta cortada no limite de {self.max_tokens} tokens. Aumente GEMINI_MAX_TOKENS no .env."
            )
        text = (response.text or "").strip()
        if not text:
            raise LLMEmptyResponseError("O Gemini respondeu sem conteúdo.")

        usage = response.usage_metadata
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            latency_ms=latency_ms,
            attempts=attempts,
        )


def _enum_name(value) -> str:
    return getattr(value, "name", None) or str(value)
