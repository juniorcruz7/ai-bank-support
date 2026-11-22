"""Dublês de teste compartilhados."""

import json

from app.llm_client import LLMResponse

VALID_DIAGNOSIS = json.dumps(
    {
        "hypotheses": [{"description": "Limite noturno de R$ 200", "probability": "alta"}],
        "evidence": ["Pix de R$ 350 às 21h"],
        "missing_info": ["ID da transação"],
        "likely_conclusion": "Provável retenção pelo limite noturno.",
    }
)


class FakeClient:
    """Implementa a interface LLMClient devolvendo respostas programadas (RNF08)."""

    provider = "fake"

    def __init__(self, *texts: str, model: str = "fake-model", config: dict | None = None):
        self._texts = list(texts)
        self.model = model
        self.config = config or {"temperature": 0.2, "max_tokens": 2048, "thinking_budget": 0}
        self.calls: list[dict] = []

    def generate(self, system, user, response_model):
        self.calls.append({"system": system, "user": user, "response_model": response_model})
        if not self._texts:
            raise AssertionError("FakeClient chamado mais vezes do que o esperado")
        return LLMResponse(text=self._texts.pop(0), input_tokens=100, output_tokens=50, latency_ms=7, attempts=1)
