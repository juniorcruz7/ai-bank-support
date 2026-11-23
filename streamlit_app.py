"""Interface web (Streamlit) sobre o mesmo núcleo da CLI (RF14).

Rodar: streamlit run streamlit_app.py
"""

import streamlit as st

from app import runtime, storage
from app.compare import COMPARISON_TEMPERATURE, DEFAULT_VERSIONS, CompareError, run_comparison, save_evaluations
from app.config import ConfigError
from app.generators import OutputValidationError
from app.llm_client import LLMError
from app.models import RUBRIC_CRITERIA, Profile, Score, Ticket
from app.prompt_builder import CONTENT_TYPES, PROMPTS_DIR, PromptError, get_customer_history, list_versions
from app.render import CONTENT_LABELS, render_decision_tree
from app.service import RunResult, run_content

USER_ERRORS = (ConfigError, storage.DataError, PromptError, LLMError, OutputValidationError, CompareError)
DEFAULT_VERSION = "v2"
PROBABILITY_LABELS = {"alta": "🔴 alta", "media": "🟡 média", "baixa": "🟢 baixa"}
PAGES = ("Gerar conteúdo", "Comparar versões", "Histórico do cliente", "Tickets")


# --- dados -------------------------------------------------------------------
# Lidos de novo a cada interação: o tickets.json pode mudar pela CLI ou por esta tela.


def load_data() -> tuple[dict[str, Profile], list[Ticket]]:
    profiles = storage.load_profiles(storage.PROFILES_PATH)
    return profiles, storage.load_tickets(profiles, storage.TICKETS_PATH)


def profile_label(p: Profile) -> str:
    return f"{p.id} · {p.name} ({p.technical_level}, {p.communication_style}, {p.channel})"


def ticket_label(t: Ticket) -> str:
    return f"{t.id} [{t.status}] {t.title}"


def pick_customer(profiles: dict[str, Profile], key: str) -> Profile:
    customer_id = st.selectbox("Cliente", list(profiles), format_func=lambda c: profile_label(profiles[c]), key=key)
    return profiles[customer_id]


def pick_ticket(tickets: list[Ticket], profile: Profile, key: str) -> Ticket | None:
    own = [t for t in tickets if t.customer_id == profile.id]
    if not own:
        st.info(f"{profile.name} não tem tickets. Abra um na página Tickets.")
        return None
    by_id = {t.id: t for t in own}
    ticket_id = st.selectbox("Ticket", list(by_id), format_func=lambda t: ticket_label(by_id[t]), key=key)
    return by_id[ticket_id]


def display_path(path) -> str:
    try:
        return str(path.relative_to(storage.ROOT_DIR))
    except ValueError:
        return str(path)


def show_ticket(ticket: Ticket, tickets: list[Ticket]) -> None:
    with st.expander(f"Ticket {ticket.id}: {ticket.title}", expanded=True):
        st.write(ticket.description)
        st.caption(f"Aberto em {ticket.created_at:%d/%m/%Y %H:%M} · status: {ticket.status}")
        if ticket.resolution:
            st.caption(f"Resolução: {ticket.resolution}")
        history = get_customer_history(tickets, ticket)
        if history:
            st.caption("Histórico usado como contexto: " + ", ".join(f"{t.id} ({t.status})" for t in history))


# --- saídas ------------------------------------------------------------------


def show_output(content_type: str, output) -> None:
    if content_type == "diagnosis":
        st.markdown("**Hipóteses**")
        for h in output.hypotheses:
            st.markdown(f"- {PROBABILITY_LABELS[h.probability]}: {h.description}")
        st.markdown("**Evidências**")
        st.markdown("\n".join(f"- {e}" for e in output.evidence) or "_(nenhuma)_")
        st.markdown("**Informações faltantes**")
        st.markdown("\n".join(f"- {m}" for m in output.missing_info) or "_(nenhuma)_")
        st.markdown("**Conclusão provável**")
        st.write(output.likely_conclusion)
    elif content_type == "resolution_steps":
        for step in sorted(output.steps, key=lambda s: s.order):
            st.markdown(f"**Passo {step.order}.** {step.action}")
            st.caption(f"Resultado esperado: {step.expected_result}  \nSe não der certo: {step.if_fails}")
        st.markdown(f"**Quando escalar:** {output.escalation_note}")
    elif content_type == "clarifying_questions":
        for q in sorted(output.questions, key=lambda q: q.priority):
            st.markdown(f"**P{q.priority}.** {q.question}")
            st.caption(f"Por que importa: {q.why_it_matters}")
    else:
        st.code(render_decision_tree(output.decision_tree), language=None)


def show_result(result: RunResult) -> None:
    run = result.run
    show_output(run.content_type, result.output)
    status = "cache hit, sem chamada à API" if run.cache_hit else "gerado agora"
    details = f"JSON salvo em `{display_path(result.path)}` ({status})"
    if run.usage:
        details += f" · {run.usage.latency_ms} ms · tokens {run.usage.input_tokens}/{run.usage.output_tokens}"
    st.caption(details)


# --- páginas -----------------------------------------------------------------


def page_generate(profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    st.header("Gerar conteúdo")
    profile = pick_customer(profiles, "gen_customer")
    ticket = pick_ticket(tickets, profile, "gen_ticket")
    if ticket is None:
        return
    show_ticket(ticket, tickets)

    versions = list_versions(PROMPTS_DIR)
    col_types, col_version = st.columns([3, 1])
    types = col_types.multiselect(
        "Tipos de conteúdo", CONTENT_TYPES, default=list(CONTENT_TYPES), format_func=CONTENT_LABELS.get
    )
    version = col_version.selectbox(
        "Versão do prompt", versions, index=versions.index(DEFAULT_VERSION) if DEFAULT_VERSION in versions else 0
    )
    if not st.button("Gerar", type="primary", disabled=not types):
        return

    client = runtime.make_client()
    for content_type in types:
        st.subheader(f"{CONTENT_LABELS[content_type]} · {version}")
        try:
            with st.spinner(f"Gerando {CONTENT_LABELS[content_type].lower()}…"):
                result = run_content(
                    profile,
                    ticket,
                    tickets,
                    content_type,
                    version,
                    client,
                    runs_dir=storage.RUNS_DIR,
                    prompts_dir=PROMPTS_DIR,
                )
        except (LLMError, OutputValidationError) as exc:
            st.error(f"Erro: {exc}")
            continue
        show_result(result)


def page_compare(profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    st.header("Comparar versões")
    st.caption(f"Mesmo cenário em cada versão, temperatura fixa em {COMPARISON_TEMPERATURE}; muda só o template.")
    profile = pick_customer(profiles, "cmp_customer")
    ticket = pick_ticket(tickets, profile, "cmp_ticket")
    if ticket is None:
        return
    show_ticket(ticket, tickets)

    available = list_versions(PROMPTS_DIR)
    col_type, col_versions = st.columns(2)
    content_type = col_type.selectbox("Tipo de conteúdo", CONTENT_TYPES, format_func=CONTENT_LABELS.get)
    versions = col_versions.multiselect("Versões", available, default=[v for v in DEFAULT_VERSIONS if v in available])

    # Trocar o cenário descarta a comparação anterior da tela (o que foi gravado continua em runs/).
    scenario = (ticket.id, content_type, tuple(versions))
    if st.session_state.get("comparison_scenario") != scenario:
        st.session_state.pop("comparison", None)
        st.session_state.pop("evaluations", None)
        st.session_state.comparison_scenario = scenario

    if st.button("Comparar", type="primary"):
        client = runtime.make_client(temperature=COMPARISON_TEMPERATURE)
        with st.spinner("Gerando as versões…"):
            st.session_state.comparison = run_comparison(
                profile,
                ticket,
                tickets,
                content_type,
                versions,
                client,
                runs_dir=storage.RUNS_DIR,
                prompts_dir=PROMPTS_DIR,
            )
        st.session_state.pop("evaluations", None)

    comparison = st.session_state.get("comparison")
    if comparison is None:
        return
    first = comparison.results[0].run
    st.subheader(f"{CONTENT_LABELS[comparison.content_type]} · {first.ticket.id} · {profiles[first.customer_id].name}")
    for column, result in zip(st.columns(len(comparison.results)), comparison.results, strict=True):
        with column:
            st.markdown(f"#### {result.run.prompt_version}")
            show_result(result)

    if "evaluations" in st.session_state:
        show_scores(st.session_state.evaluations)
        return

    st.subheader("Rubrica")
    with st.form("rubric"):
        inputs = {}
        for column, version in zip(st.columns(len(comparison.versions)), comparison.versions, strict=True):
            with column:
                st.markdown(f"**{version}**")
                for key, label in RUBRIC_CRITERIA.items():
                    score = st.slider(label, 1, 5, 3, key=f"score_{version}_{key}")
                    text = st.text_area(f"Justificativa: {label.lower()}", key=f"just_{version}_{key}")
                    inputs[(version, key)] = (score, text.strip())
        if not st.form_submit_button("Salvar avaliação"):
            return
    empty = [f"{v}: {RUBRIC_CRITERIA[k].lower()}" for (v, k), (_, text) in inputs.items() if not text]
    if empty:
        st.error("Preencha todas as justificativas. Faltam: " + "; ".join(empty) + ".")
        return
    scores = {
        v: {k: Score(score=s, justification=t) for (vv, k), (s, t) in inputs.items() if vv == v}
        for v in comparison.versions
    }
    st.session_state.evaluations = save_evaluations(comparison, scores)
    st.rerun()


def show_scores(evaluations) -> None:
    rows = {
        label: {v: getattr(ev, key).score for v, ev in evaluations.items()} for key, label in RUBRIC_CRITERIA.items()
    }
    rows["Total"] = {v: ev.total for v, ev in evaluations.items()}
    st.success(f"Avaliação gravada nas execuções (comparison_id {next(iter(evaluations.values())).comparison_id}).")
    st.table(rows)


def page_history(profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    st.header("Histórico do cliente")
    profile = pick_customer(profiles, "hist_customer")
    own = sorted((t for t in tickets if t.customer_id == profile.id), key=lambda t: t.created_at, reverse=True)
    runs = storage.list_customer_runs(profile.id, storage.RUNS_DIR)

    st.subheader(f"Tickets ({len(own)})")
    for t in own:
        st.markdown(f"**{t.id}** · {t.created_at:%d/%m/%Y %H:%M} · `{t.status}` · {t.title}")
        if t.resolution:
            st.caption(f"Resolução: {t.resolution}")

    st.subheader(f"Execuções ({len(runs)})")
    if not runs:
        st.caption("Nenhuma execução gravada.")
    for path, run in reversed(runs):
        title = (
            f"{run.timestamp:%d/%m/%Y %H:%M} · {run.ticket.id} · {CONTENT_LABELS[run.content_type]} · "
            f"{run.prompt_version} · {'cache hit' if run.cache_hit else 'gerado'}"
        )
        if run.evaluation:
            title += f" · rubrica {run.evaluation.total}/15"
        with st.expander(title):
            st.caption(display_path(path))
            st.json(run.model_dump(mode="json"), expanded=False)


def page_tickets(profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    st.header("Tickets")
    if message := st.session_state.pop("ticket_message", None):
        st.success(message)

    st.subheader("Abrir ticket")
    with st.form("new_ticket", clear_on_submit=True):
        profile = pick_customer(profiles, "new_customer")
        title = st.text_input("Título")
        description = st.text_area("Descrição (como o cliente escreveu)")
        if st.form_submit_button("Abrir ticket"):
            ticket = storage.add_ticket(profiles, profile.id, title, description, path=storage.TICKETS_PATH)
            st.session_state.ticket_message = f"Ticket {ticket.id} criado para {profile.id} · {profile.name}."
            st.rerun()

    st.subheader("Fechar ticket")
    open_tickets = {t.id: t for t in tickets if t.status == "aberto"}
    if not open_tickets:
        st.caption("Não há tickets abertos.")
        return
    with st.form("close_ticket"):
        ticket_id = st.selectbox(
            "Ticket aberto",
            list(open_tickets),
            format_func=lambda t: f"{t} · {open_tickets[t].customer_id} · {open_tickets[t].title}",
        )
        resolution = st.text_area("Resolução")
        if st.form_submit_button("Fechar como resolvido"):
            ticket = storage.close_ticket(profiles, ticket_id, resolution, path=storage.TICKETS_PATH)
            st.session_state.ticket_message = (
                f"Ticket {ticket.id} fechado. A resolução passa a entrar no contexto "
                f"dos próximos tickets de {ticket.customer_id}."
            )
            st.rerun()


PAGE_HANDLERS = dict(zip(PAGES, (page_generate, page_compare, page_history, page_tickets), strict=True))


def main() -> None:
    st.set_page_config(page_title="Suporte · Banco Digital", page_icon="💬", layout="wide")
    if not st.session_state.get("logging_ready"):
        runtime.setup_logging()
        st.session_state.logging_ready = True

    st.sidebar.title("Assistente de suporte")
    page = st.sidebar.radio("Página", PAGES, label_visibility="collapsed")
    st.sidebar.caption("Mesmo núcleo da CLI: prompts versionados, cache e histórico em runs/.")
    if page != "Comparar versões":
        st.session_state.pop("comparison", None)
        st.session_state.pop("evaluations", None)

    try:
        profiles, tickets = load_data()
        PAGE_HANDLERS[page](profiles, tickets)
    except USER_ERRORS as exc:
        st.error(f"Erro: {exc}")


main()
