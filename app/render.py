"""Formata as saídas validadas para leitura no terminal (inclui o fluxograma em ASCII)."""

import textwrap
from itertools import zip_longest

from pydantic import BaseModel

from app.generators import OUTPUT_MODELS
from app.models import (
    ROOT_NODE_ID,
    RUBRIC_CRITERIA,
    ClarifyingQuestions,
    DecisionTree,
    Diagnosis,
    Evaluation,
    FlowNode,
    FlowSummary,
    Profile,
    ResolutionSteps,
    Run,
    Ticket,
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


OUT_OF_SCOPE_LABEL = "Ticket fora do escopo do suporte"


def render_output(content_type: str, output: BaseModel) -> str:
    message = getattr(output, "out_of_scope", None)
    if message:  # o modelo recusou o ticket: não há conteúdo para desenhar
        return f"{OUT_OF_SCOPE_LABEL}:\n  {message}"
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


def render_history(profile: Profile, tickets: list[Ticket], runs: list[tuple[str, Run]]) -> str:
    """Tickets do cliente (mais recentes primeiro) e as execuções de cada um (RF15).

    `runs` traz pares (caminho já formatado para exibição, execução), em ordem cronológica.
    """
    lines = [
        f"=== Histórico · {profile.id} · {profile.name} "
        f"({profile.technical_level}, {profile.communication_style}, {profile.channel}) ===",
        "",
        f"Tickets ({len(tickets)}):",
    ]
    ordered = sorted(tickets, key=lambda t: t.created_at, reverse=True)
    for t in ordered:
        lines.append(f"  {t.id}  {t.created_at:%d/%m/%Y %H:%M}  [{t.status}]  {t.title}")
        if t.resolution:
            lines.append(f"        Resolução: {t.resolution}")
    if not tickets:
        lines.append("  (nenhum)")

    lines += ["", f"Execuções ({len(runs)}):"]
    if not runs:
        lines.append("  (nenhuma)")
    known = [t.id for t in ordered]
    orphan = sorted({run.ticket.id for _, run in runs} - set(known))
    for ticket_id in known + orphan:
        own = [(path, run) for path, run in runs if run.ticket.id == ticket_id]
        if not own:
            continue
        lines.append(f"  {ticket_id}")
        for path, run in own:
            status = "cache hit" if run.cache_hit else "gerado"
            lines.append(
                f"    {run.timestamp:%d/%m/%Y %H:%M}  {CONTENT_LABELS[run.content_type]} · "
                f"{run.prompt_version} · {status}"
            )
            if run.evaluation:
                ev = run.evaluation
                lines.append(f"      Rubrica: {ev.total}/15 (comparação {ev.comparison_id[:8]})")
            lines.append(f"      {path}")
    return "\n".join(lines)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def render_customer_history(profile: Profile, tickets: list[Ticket], runs: list[Run], numbers: dict[str, int]) -> str:
    """Histórico na visão do cliente (CLI): os próprios tickets e as respostas, sem dados técnicos.

    `numbers` é a numeração dos tickets na conta do cliente (nº 1, nº 2…). O ID interno vai entre
    parênteses porque é ele que a CLI usa em `--ticket`. Das respostas, mostra a mais recente de
    cada tipo por ticket: repetir a mesma resposta vinda do cache não ajuda o cliente.
    """

    def name(ticket_id: str) -> str:
        return f"nº {numbers[ticket_id]} ({ticket_id})" if ticket_id in numbers else ticket_id

    lines = [f"=== Meu histórico · {profile.name} ===", "", f"Meus tickets ({len(tickets)}):"]
    for t in sorted(tickets, key=lambda t: t.created_at, reverse=True):
        lines.append(f"  {name(t.id)}  {t.created_at:%d/%m/%Y %H:%M}  [{t.status}]  {t.title}")
        if t.resolution:
            lines.append(f"        Resolução: {t.resolution}")
    if not tickets:
        lines.append("  (nenhum)")

    latest: dict[tuple[str, str], Run] = {}
    for run in runs:  # em ordem cronológica: a última de cada par fica
        latest[(run.ticket.id, run.content_type)] = run
    answers = sorted(latest.values(), key=lambda r: r.timestamp, reverse=True)
    lines += ["", f"Respostas recebidas ({len(answers)}):"]
    if not answers:
        lines.append("  (nenhuma)")
    for run in answers:
        output = OUTPUT_MODELS[run.content_type].model_validate(run.output)
        title = f"{run.timestamp:%d/%m/%Y %H:%M} · Ticket {name(run.ticket.id)} · {CONTENT_LABELS[run.content_type]}"
        lines += ["", f"  --- {title} ---", _indent(render_output(run.content_type, output))]
    return "\n".join(lines)
