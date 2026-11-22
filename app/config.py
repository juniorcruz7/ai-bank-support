"""Configuração do projeto lida do .env (chave, modelo e parâmetros do Gemini)."""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2048
APP_TIMEZONE = ZoneInfo("America/Recife")  # exige o pacote tzdata no Windows


class ConfigError(Exception):
    """Configuração ausente ou inválida; a mensagem é legível para o usuário."""


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __repr__(self) -> str:
        # Nunca expor a chave em logs ou tracebacks.
        return (
            f"Settings(gemini_api_key='***', gemini_model={self.gemini_model!r}, "
            f"temperature={self.temperature}, max_tokens={self.max_tokens})"
        )


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} não definida. Copie .env.example para .env e preencha {name}.")
    return value


def _optional_number(name: str, default, cast, low, high):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} inválido; use um número entre {low} e {high}.") from None
    if not low <= value <= high:
        raise ConfigError(f"{name}={raw!r} fora do intervalo; use um número entre {low} e {high}.")
    return value


def load_settings() -> Settings:
    """Lê o .env e retorna as configurações; lança ConfigError se faltar algo."""
    load_dotenv()
    return Settings(
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
        temperature=_optional_number("GEMINI_TEMPERATURE", DEFAULT_TEMPERATURE, float, 0.0, 2.0),
        max_tokens=_optional_number("GEMINI_MAX_TOKENS", DEFAULT_MAX_TOKENS, int, 1, 65536),
    )
