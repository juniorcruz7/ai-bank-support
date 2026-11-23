"""Testes da formatação no terminal e do fluxograma ASCII (RF07, RF13)."""

import json
from datetime import UTC, datetime

from app.models import ClarifyingQuestions, Diagnosis, Evaluation, FlowSummary, ResolutionSteps, Score
from app.render import render_decision_tree, render_output, render_scores_table, render_side_by_side
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


# --- comparação --------------------------------------------------------------


def test_side_by_side_columns():
    text = render_side_by_side([("v1", "linha curta"), ("v2", "outra\nsegunda")], total_width=100)
    lines = text.splitlines()
    assert lines[0].startswith("v1") and " | v2" in lines[0]
    assert lines[2].startswith("linha curta") and lines[2].endswith("| outra")
    assert lines[3].strip() == "| segunda"
    assert all(len(line) <= 100 for line in lines)


def test_side_by_side_wraps_long_lines_inside_column():
    text = render_side_by_side([("v1", "palavra " * 30), ("v2", "x")], total_width=100)
    body = text.splitlines()[2:]
    assert len(body) > 1  # quebrou em várias linhas
    # coluna de 48 = (100 - 3) // 2; o separador fica sempre na mesma posição
    assert all(line[48:].startswith(" |") for line in body)


def test_narrow_terminal_stacks_outputs():
    assert render_side_by_side([("v1", "a"), ("v2", "b")], total_width=60) == "--- v1 ---\na\n\n--- v2 ---\nb"


def _evaluation(*values):
    keys = ("clarity", "customer_fit", "actionability")
    scores = {k: Score(score=v, justification="ok") for k, v in zip(keys, values, strict=True)}
    return Evaluation(comparison_id="c", evaluated_at=datetime.now(UTC), **scores)


def test_scores_table():
    lines = render_scores_table({"v1": _evaluation(2, 1, 2), "v2": _evaluation(4, 5, 4)}).splitlines()
    assert lines[0].split() == ["Critério", "v1", "v2"]
    assert lines[3].split()[-2:] == ["1", "5"]  # Adequação ao cliente
    assert lines[-1].split() == ["Total", "5", "13"]


def test_out_of_scope_render_shows_only_the_message():
    from tests.fakes import OUT_OF_SCOPE_BY_TYPE, OUT_OF_SCOPE_MESSAGE

    text = render_output(
        "resolution_steps", ResolutionSteps.model_validate_json(OUT_OF_SCOPE_BY_TYPE["resolution_steps"])
    )
    assert text == f"Ticket fora do escopo do suporte:\n  {OUT_OF_SCOPE_MESSAGE}"
    flow = render_output("flow_summary", FlowSummary.model_validate_json(OUT_OF_SCOPE_BY_TYPE["flow_summary"]))
    assert flow.startswith("Ticket fora do escopo do suporte:") and "[n1]" not in flow
