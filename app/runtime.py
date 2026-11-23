"""Infraestrutura compartilhada pela CLI e pela interface web: cliente LLM e logging."""

import dataclasses
import logging
import sys
from pathlib import Path

from app.config import load_settings
from app.llm_client import GeminiClient, LLMClient

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_HANDLER_TAG = "_ai_bank_support"

logger = logging.getLogger(__name__)


def make_client(temperature: float | None = None) -> LLMClient:
    """Cria o cliente Gemini; `temperature` sobrescreve o .env (usado na comparação)."""
    settings = load_settings()
    if temperature is not None and temperature != settings.temperature:
        logger.warning("Comparação usa temperatura fixa %s (o .env pede %s).", temperature, settings.temperature)
        settings = dataclasses.replace(settings, temperature=temperature)
    return GeminiClient.from_settings(settings)


def setup_logging(log_dir: Path = LOG_DIR) -> None:
    """INFO no terminal (stderr) e em logs/app.log (RNF11); chamar de novo não duplica handlers."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("  [log] %(message)s"))
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    logfile.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for handler in (console, logfile):
        setattr(handler, _HANDLER_TAG, True)
        root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "google_genai", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
