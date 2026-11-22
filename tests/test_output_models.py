"""Testes dos modelos de saída do LLM (RF04–RF07, RNF03)."""

import pytest
from pydantic import ValidationError

from app.models import ClarifyingQuestions, Diagnosis, FlowSummary, ResolutionSteps

DIAGNOSIS = {
    "hypotheses": [{"description": "Limite noturno", "probability": "alta"}],
    "evidence": ["Pix às 21h"],
    "missing_info": ["ID da transação"],
    "likely_conclusion": "Provável bloqueio por limite noturno.",
}
STEPS = {
    "steps": [{"order": 1, "action": "Abra o app", "expected_result": "Tela inicial", "if_fails": "Reinstale"}],
    "escalation_note": "Escalar se persistir.",
}
QUESTIONS = {"questions": [{"question": "Qual o valor?", "why_it_matters": "Limite", "priority": 1}]}


def _tree(*nodes):
    return {"decision_tree": {"nodes": list(nodes)}}


def _decision(node_id, yes, no):
    return {"id": node_id, "type": "decision", "text": "Pergunta?", "yes": yes, "no": no}


def _action(node_id):
    return {"id": node_id, "type": "action", "text": "Ação"}


VALID_TREE = _tree(_decision("n1", "n2", "n3"), _action("n2"), _action("n3"))


@pytest.mark.parametrize(
    "model, data",
    [(Diagnosis, DIAGNOSIS), (ResolutionSteps, STEPS), (ClarifyingQuestions, QUESTIONS), (FlowSummary, VALID_TREE)],
)
def test_valid_outputs(model, data):
    model.model_validate(data)


def test_schema_does_not_limit_quantity():
    # "No máximo 3 perguntas" é regra do prompt v2, não do schema.
    ClarifyingQuestions.model_validate({"questions": QUESTIONS["questions"] * 6})


@pytest.mark.parametrize(
    "model, data",
    [
        (Diagnosis, {**DIAGNOSIS, "hypotheses": []}),
        (Diagnosis, {**DIAGNOSIS, "hypotheses": [{"description": "x", "probability": "certa"}]}),
        (Diagnosis, {**DIAGNOSIS, "likely_conclusion": "  "}),
        (Diagnosis, {**DIAGNOSIS, "evidence": [""]}),
        (ResolutionSteps, {**STEPS, "steps": []}),
        (ResolutionSteps, {**STEPS, "steps": [{**STEPS["steps"][0], "order": 0}]}),
        (ClarifyingQuestions, {"questions": [{**QUESTIONS["questions"][0], "priority": 4}]}),
        (ClarifyingQuestions, {"questions": []}),
    ],
)
def test_invalid_outputs(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize(
    "tree, message",
    [
        (_tree(_decision("n1", "n2", "n2"), _action("n2"), _action("n2")), "duplicado"),
        (_tree(_decision("n2", "n3", "n4"), _action("n3"), _action("n4")), "raiz 'n1' ausente"),
        (_tree(_decision("n1", "n2", None), _action("n2")), "sem 'no'"),
        (_tree(_decision("n1", "n2", "n9"), _action("n2")), "inexistente 'n9'"),
        (_tree(_decision("n1", "n2", "n3"), {**_action("n2"), "yes": "n3"}, _action("n3")), "nó de ação 'n2'"),
        (_tree(_decision("n1", "n2", "n3"), _decision("n2", "n1", "n3"), _action("n3")), "ciclo"),
        (_tree(_decision("n1", "n2", "n3"), _action("n2"), _action("n3"), _action("n4")), "inalcançáveis.*n4"),
    ],
)
def test_invalid_decision_tree(tree, message):
    with pytest.raises(ValidationError, match=message):
        FlowSummary.model_validate(tree)


def test_shared_subtree_is_not_a_cycle():
    # Dois caminhos chegando ao mesmo nó é permitido (DAG), só ciclo não.
    FlowSummary.model_validate(_tree(_decision("n1", "n2", "n3"), _decision("n2", "n3", "n4"), _action("n3"), _action("n4")))
