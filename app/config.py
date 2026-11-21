"""Configuração do projeto lida do .env (chave e modelo do Gemini)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class ConfigError(Exception):
    """Configuração ausente ou inválida; a mensagem é legível para o usuário."""


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str

    def __repr__(self) -> str:
        # Nunca expor a chave em logs ou tracebacks.
        return f"Settings(gemini_api_key='***', gemini_model={self.gemini_model!r})"


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} não definida. Copie .env.example para .env e preencha {name}."
        )
    return value


def load_settings() -> Settings:
    """Lê o .env e retorna as configurações; lança ConfigError se faltar algo."""
    load_dotenv()
    return Settings(
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
    )
