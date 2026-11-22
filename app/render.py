"""Formata as saídas validadas para leitura no terminal (inclui o fluxograma em ASCII)."""

from pydantic import BaseModel

from app.models import ROOT_NODE_ID, ClarifyingQuestions, DecisionTree, Diagnosis, FlowNode, FlowSummary, ResolutionSteps

CONTENT_LABELS = {
    "diagnosis": "Diagnóstico",
    "resolution_steps": "Passo a passo",
    "clarifying_questions": "Perguntas de esclarecimento",
    "flow_summary": "Fluxograma",
}


def _bullets(items: list[str], empty: str = "(nenhum)") -> list[str]:
    return [f"  - {item}" for item in items] or [f"  {empty}"]


def render_diagnosis(output: Diagnosis) -> str:
    lines = ["Hipóteses:"]
    lines += [f"  {i}. [{h.probability}] {h.description}" for i, h in enumerate(output.hypotheses, 1)]
    lines += ["", "Evidências:", *_bullets(output.evidence)]
    lines += ["", "Informações faltantes:", *_bullets(output.missing_info)]
    lines += ["", "Conclusão provável:", f"  {output.likely_conclusion}"]
    return "\n".join(lines)


def render_resolution_steps(output: ResolutionSteps) -> str:
    lines = []
    for step in sorted(output.steps, key=lambda s: s.order):
        lines += [
            f"Passo {step.order}: {step.action}",
            f"    Resultado esperado: {step.expected_result}",
            f"    Se não der certo: {step.if_fails}",
            "",
        ]
    lines += ["Quando escalar:", f"  {output.escalation_note}"]
    return "\n".join(lines)


def render_clarifying_questions(output: ClarifyingQuestions) -> str:
    lines = []
    for q in sorted(output.questions, key=lambda q: q.priority):
        lines += [f"[P{q.priority}] {q.question}", f"     Por que importa: {q.why_it_matters}", ""]
    return "\n".join(lines).rstrip()


def _node_label(node: FlowNode) -> str:
    return f"[{node.id}] (ação) {node.text}" if node.type == "action" else f"[{node.id}] {node.text}"


def render_decision_tree(tree: DecisionTree) -> str:
    """Desenha a árvore em ASCII puro; nó já desenhado em outro caminho vira "(ver nX)"."""
    by_id = {node.id: node for node in tree.nodes}
    root = by_id[ROOT_NODE_ID]
    lines = [_node_label(root)]
    drawn = {root.id}

    def draw_children(node: FlowNode, prefix: str) -> None:
        branches = [("sim", node.yes), ("não", node.no)]
        for index, (label, target_id) in enumerate(branches):
            last = index == len(branches) - 1
            connector = "`-- " if last else "+-- "
            if target_id in drawn:
                lines.append(f"{prefix}{connector}{label}: (ver {target_id})")
                continue
            child = by_id[target_id]
            drawn.add(child.id)
            lines.append(f"{prefix}{connector}{label}: {_node_label(child)}")
            if child.type == "decision":
                draw_children(child, prefix + ("     " if last else "|    "))

    if root.type == "decision":
        draw_children(root, " ")
    return "\n".join(lines)


def render_flow_summary(output: FlowSummary) -> str:
    return render_decision_tree(output.decision_tree)


_RENDERERS = {
    "diagnosis": render_diagnosis,
    "resolution_steps": render_resolution_steps,
    "clarifying_questions": render_clarifying_questions,
    "flow_summary": render_flow_summary,
}


def render_output(content_type: str, output: BaseModel) -> str:
    return _RENDERERS[content_type](output)
