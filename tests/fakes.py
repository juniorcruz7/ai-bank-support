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


VALID_STEPS = json.dumps(
    {
        "steps": [
            {"order": 2, "action": "Abra o extrato", "expected_result": "Vê o Pix", "if_fails": "Me avise"},
            {"order": 1, "action": "Abra o app", "expected_result": "Tela inicial", "if_fails": "Reinicie o celular"},
        ],
        "escalation_note": "Escalar para a equipe de Pix se o status não mudar.",
    }
)
VALID_QUESTIONS = json.dumps(
    {
        "questions": [
            {
                "question": "O seu filho olhou o extrato?",
                "why_it_matters": "Descarta atraso de notificação",
                "priority": 2,
            },
            {"question": "Qual chave Pix você usou?", "why_it_matters": "Confirma o destinatário", "priority": 1},
        ]
    }
)
VALID_FLOW = json.dumps(
    {
        "decision_tree": {
            "nodes": [
                {"id": "n1", "type": "decision", "text": "Pix concluído?", "yes": "n2", "no": "n3"},
                {"id": "n2", "type": "action", "text": "Pedir comprovante"},
                {"id": "n3", "type": "action", "text": "Escalar para equipe de Pix"},
            ]
        }
    }
)
VALID_BY_TYPE = {
    "diagnosis": VALID_DIAGNOSIS,
    "resolution_steps": VALID_STEPS,
    "clarifying_questions": VALID_QUESTIONS,
    "flow_summary": VALID_FLOW,
}
