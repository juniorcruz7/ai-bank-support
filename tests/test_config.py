"""Testes da leitura de configuração (RNF01, RNF04)."""

import pytest

from app import config
from app.config import DEFAULT_GEMINI_MODEL, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, ConfigError, load_settings

ENV_VARS = ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_TEMPERATURE", "GEMINI_MAX_TOKENS")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    # Ignora o .env real da máquina: cada teste define só o que precisa.
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_missing_key_gives_clear_error():
    with pytest.raises(ConfigError, match="GEMINI_API_KEY não definida"):
        load_settings()


def test_defaults(isolated_env):
    isolated_env.setenv("GEMINI_API_KEY", "segredo")
    settings = load_settings()
    assert settings.gemini_model == DEFAULT_GEMINI_MODEL
    assert settings.temperature == DEFAULT_TEMPERATURE
    assert settings.max_tokens == DEFAULT_MAX_TOKENS


def test_overrides(isolated_env):
    isolated_env.setenv("GEMINI_API_KEY", "segredo")
    isolated_env.setenv("GEMINI_MODEL", "outro")
    isolated_env.setenv("GEMINI_TEMPERATURE", "0")
    isolated_env.setenv("GEMINI_MAX_TOKENS", "4096")
    settings = load_settings()
    assert (settings.gemini_model, settings.temperature, settings.max_tokens) == ("outro", 0.0, 4096)


@pytest.mark.parametrize(
    "name, value", [("GEMINI_TEMPERATURE", "quente"), ("GEMINI_TEMPERATURE", "3"), ("GEMINI_MAX_TOKENS", "0")]
)
def test_invalid_numbers(isolated_env, name, value):
    isolated_env.setenv("GEMINI_API_KEY", "segredo")
    isolated_env.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_repr_hides_key(isolated_env):
    isolated_env.setenv("GEMINI_API_KEY", "segredo")
    assert "segredo" not in repr(load_settings())
