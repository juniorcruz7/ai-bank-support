"""Testes da formatação no terminal e do fluxograma ASCII (RF07, RF13)."""

import json

from app.models import ClarifyingQuestions, Diagnosis, FlowSummary, ResolutionSteps
from app.render import render_decision_tree, render_output
from tests.fakes import VALID_DIAGNOSIS, VALID_QUESTIONS, VALID_STEPS


def _tree(*nodes):
    return FlowSummary.model_validate({"decision_tree": {"nodes": list(nodes)}}).decision_tree


def _decision(node_id, text, yes, no):
    return {"id": node_id, "type": "decision", "text": text, "yes": yes, "no": no}


def _action(node_id, text):
    return {"id": node_id, "type": "action", "text": text}


def test_ascii_tree_layout():
    tree = _tree(
        _decision("n1", "Pix concluído?", "n2", "n3"),
        _decision("n2", "Chave confirmada?", "n4", "n5"),
        _action("n3", "Verificar status e escalar"),
        _action("n4", "Pedir comprovante"),
        _action("n5", "Orientar sobre o MED"),
    )
    assert render_decision_tree(tree) == "\n".join(
        [
            "[n1] Pix concluído?",
            " +-- sim: [n2] Chave confirmada?",
            " |    +-- sim: [n4] (ação) Pedir comprovante",
            " |    `-- não: [n5] (ação) Orientar sobre o MED",
            " `-- não: [n3] (ação) Verificar status e escalar",
        ]
    )


def test_ascii_tree_is_pure_ascii_except_text():
    tree = _tree(_decision("n1", "A?", "n2", "n3"), _action("n2", "B"), _action("n3", "C"))
    drawing = render_decision_tree(tree).replace("não", "nao").replace("ação", "acao")
    assert drawing.isascii()


def test_shared_node_is_drawn_once():
    tree = _tree(
        _decision("n1", "A?", "n2", "n3"),
        _decision("n2", "B?", "n3", "n4"),
        _action("n3", "Comum"),
        _action("n4", "D"),
    )
    lines = render_decision_tree(tree).splitlines()
    assert sum("Comum" in line for line in lines) == 1
    assert lines[-1] == " `-- não: (ver n3)"


def test_single_action_tree():
    assert render_decision_tree(_tree(_action("n1", "Só isso"))) == "[n1] (ação) Só isso"


def test_diagnosis_render():
    text = render_output("diagnosis", Diagnosis.model_validate_json(VALID_DIAGNOSIS))
    assert "1. [alta] Limite noturno" in text
    assert "Informações faltantes:" in text and "Conclusão provável:" in text


def test_steps_render_sorted_by_order():
    text = render_output("resolution_steps", ResolutionSteps.model_validate_json(VALID_STEPS))
    assert text.index("Passo 1: Abra o app") < text.index("Passo 2: Abra o extrato")
    assert "Quando escalar:" in text


def test_questions_render_sorted_by_priority():
    text = render_output("clarifying_questions", ClarifyingQuestions.model_validate_json(VALID_QUESTIONS))
    assert text.splitlines()[0] == "[P1] Qual chave Pix você usou?"
    assert json.loads(VALID_QUESTIONS)  # sanidade do fixture
