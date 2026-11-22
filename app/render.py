"""Formata as saídas validadas para leitura no terminal (inclui o fluxograma em ASCII)."""

import textwrap
from itertools import zip_longest

from pydantic import BaseModel

from app.models import (
    ROOT_NODE_ID,
    RUBRIC_CRITERIA,
    ClarifyingQuestions,
    DecisionTree,
    Diagnosis,
    Evaluation,
    FlowNode,
    FlowSummary,
    ResolutionSteps,
)

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


# --- Comparação --------------------------------------------------------------

MIN_COLUMN_WIDTH = 40
COLUMN_GAP = " | "


def _wrap_keeping_indent(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped:
            lines.append("")
            continue
        indent = " " * min(len(line) - len(stripped), width // 2)
        lines += textwrap.wrap(stripped, width=width, initial_indent=indent, subsequent_indent=indent + "  ")
    return lines


def render_side_by_side(columns: list[tuple[str, str]], total_width: int) -> str:
    """Uma coluna por versão; se não couber (coluna < 40), empilha as saídas."""
    gaps = len(COLUMN_GAP) * (len(columns) - 1)
    width = (total_width - gaps) // len(columns)
    if width < MIN_COLUMN_WIDTH:
        return "\n\n".join(f"--- {title} ---\n{text}" for title, text in columns)

    wrapped = [_wrap_keeping_indent(text, width) for _, text in columns]
    rows = [COLUMN_GAP.join(title.ljust(width) for title, _ in columns).rstrip()]
    rows.append(COLUMN_GAP.join("-" * width for _ in columns))
    for cells in zip_longest(*wrapped, fillvalue=""):
        rows.append(COLUMN_GAP.join(cell.ljust(width) for cell in cells).rstrip())
    return "\n".join(rows)


def render_scores_table(evaluations: dict[str, Evaluation]) -> str:
    versions = list(evaluations)
    label_width = max(len(label) for label in RUBRIC_CRITERIA.values())
    header = "Critério".ljust(label_width) + "".join(f"{v:>6}" for v in versions)
    lines = [header, "-" * len(header)]
    for key, label in RUBRIC_CRITERIA.items():
        lines.append(label.ljust(label_width) + "".join(f"{getattr(evaluations[v], key).score:>6}" for v in versions))
    lines.append("-" * len(header))
    lines.append("Total".ljust(label_width) + "".join(f"{evaluations[v].total:>6}" for v in versions))
    return "\n".join(lines)
