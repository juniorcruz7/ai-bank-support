"""Interface web (Streamlit) sobre o mesmo núcleo da CLI (RF14), com login (RF19–RF22).

As permissões são checadas na camada de serviço; esconder páginas e opções aqui é só
para a tela não oferecer o que a conta não pode fazer.

Rodar: streamlit run streamlit_app.py
"""

import os

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from app import auth, runtime, storage
from app.auth import AccessDeniedError, AuthError
from app.compare import COMPARISON_TEMPERATURE, DEFAULT_VERSIONS, CompareError, run_comparison, save_evaluations
from app.config import ConfigError
from app.generators import OUTPUT_MODELS, OutputValidationError
from app.llm_client import LLMError
from app.models import RUBRIC_CRITERIA, Profile, Score, Ticket, User
from app.prompt_builder import (
    CONTENT_TYPES,
    DEFAULT_VERSION,
    PROMPTS_DIR,
    PromptError,
    get_customer_history,
    list_versions,
)
from app.render import CONTENT_LABELS, render_decision_tree
from app.service import (
    RunResult,
    customer_history,
    customer_ticket_numbers,
    open_ticket,
    resolve_ticket,
    run_content,
    visible_tickets,
)

USER_ERRORS = (
    ConfigError,
    storage.DataError,
    PromptError,
    LLMError,
    OutputValidationError,
    CompareError,
    AccessDeniedError,
)
PROBABILITY_LABELS = {"alta": "🔴 alta", "media": "🟡 média", "baixa": "🟢 baixa"}
AGENT_PAGES = ("Gerar conteúdo", "Comparar versões", "Histórico do cliente", "Tickets")
CUSTOMER_PAGES = ("Gerar conteúdo", "Histórico do cliente", "Tickets")
# Nomes que a conta de cliente vê no menu (as chaves continuam as mesmas).
CUSTOMER_PAGE_LABELS = {
    "Gerar conteúdo": "Ajuda com um ticket",
    "Histórico do cliente": "Meu histórico",
    "Tickets": "Abrir ticket",
}


# --- dados -------------------------------------------------------------------
# Lidos de novo a cada interação: o tickets.json pode mudar pela CLI ou por esta tela.


def load_data() -> tuple[dict[str, Profile], list[Ticket]]:
    profiles = storage.load_profiles(storage.PROFILES_PATH)
    return profiles, storage.load_tickets(profiles, storage.TICKETS_PATH)


def profile_label(p: Profile) -> str:
    return f"{p.id} · {p.name} ({p.technical_level}, {p.communication_style}, {p.channel})"


def ticket_name(user: User, ticket_id: str, tickets: list[Ticket]) -> str:
    """Atendente vê o ID interno; o cliente vê o número do ticket na conta dele (nº 1, nº 2…)."""
    if auth.is_agent(user):
        return ticket_id
    return f"nº {customer_ticket_numbers(tickets, user.customer_id)[ticket_id]}"


def ticket_label(user: User, t: Ticket, tickets: list[Ticket]) -> str:
    return f"{ticket_name(user, t.id, tickets)} [{t.status}] {t.title}"


def pick_customer(user: User, profiles: dict[str, Profile], key: str) -> Profile:
    """Atendente escolhe o cliente; a conta de cliente usa o próprio perfil, sem seletor."""
    if not auth.is_agent(user):
        return profiles[user.customer_id]
    customer_id = st.selectbox("Cliente", list(profiles), format_func=lambda c: profile_label(profiles[c]), key=key)
    return profiles[customer_id]


def pick_ticket(user: User, tickets: list[Ticket], profile: Profile, key: str) -> Ticket | None:
    own = [t for t in tickets if t.customer_id == profile.id]
    if not own:
        if auth.is_agent(user):
            st.info(f"{profile.name} não tem tickets. Abra um na página Tickets.")
        else:
            st.info("Você ainda não tem tickets. Abra um em “Abrir ticket”, no menu ao lado.")
        return None
    if not auth.is_agent(user):  # mais recente primeiro: nº 3, nº 2, nº 1
        numbers = customer_ticket_numbers(tickets, profile.id)
        own.sort(key=lambda t: numbers[t.id], reverse=True)
    by_id = {t.id: t for t in own}
    ticket_id = st.selectbox(
        "Ticket", list(by_id), format_func=lambda t: ticket_label(user, by_id[t], tickets), key=key
    )
    return by_id[ticket_id]


def display_path(path) -> str:
    try:
        return str(path.relative_to(storage.ROOT_DIR))
    except ValueError:
        return str(path)


def show_ticket(user: User, ticket: Ticket, tickets: list[Ticket]) -> None:
    with st.expander(f"Ticket {ticket_name(user, ticket.id, tickets)}: {ticket.title}", expanded=True):
        st.write(ticket.description)
        st.caption(f"Aberto em {ticket.created_at:%d/%m/%Y %H:%M} · status: {ticket.status}")
        if ticket.resolution:
            st.caption(f"Resolução: {ticket.resolution}")
        history = get_customer_history(tickets, ticket)
        if history and auth.is_agent(user):
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


def show_result(result: RunResult, user: User) -> None:
    run = result.run
    show_output(run.content_type, result.output)
    if not auth.is_agent(user):  # detalhes técnicos da execução são do atendente
        return
    status = "cache hit, sem chamada à API" if run.cache_hit else "gerado agora"
    details = f"JSON salvo em `{display_path(result.path)}` ({status})"
    if run.usage:
        details += f" · {run.usage.latency_ms} ms · tokens {run.usage.input_tokens}/{run.usage.output_tokens}"
    st.caption(details)


# --- páginas -----------------------------------------------------------------


def page_generate(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    agent = auth.is_agent(user)
    st.header("Gerar conteúdo" if agent else "Ajuda com um ticket")
    if not agent:
        st.caption("Escolha um dos seus tickets para receber o passo a passo e as perguntas que ajudam a resolvê-lo.")
    profile = pick_customer(user, profiles, "gen_customer")
    ticket = pick_ticket(user, tickets, profile, "gen_ticket")
    if ticket is None:
        return
    show_ticket(user, ticket, tickets)

    allowed = list(auth.allowed_content_types(user))
    col_types, col_version = st.columns([3, 1])
    types_label = "Tipos de conteúdo" if agent else "O que você quer receber"
    types = col_types.multiselect(types_label, allowed, default=allowed, format_func=CONTENT_LABELS.get)
    version = DEFAULT_VERSION
    if auth.is_agent(user):  # cliente usa a versão padrão
        versions = list_versions(PROMPTS_DIR)
        index = versions.index(DEFAULT_VERSION) if DEFAULT_VERSION in versions else 0
        version = col_version.selectbox("Versão do prompt", versions, index=index)
    if not st.button("Gerar" if agent else "Pedir ajuda", type="primary", disabled=not types):
        return

    client = runtime.make_client()
    for content_type in types:
        st.subheader(f"{CONTENT_LABELS[content_type]} · {version}" if agent else CONTENT_LABELS[content_type])
        try:
            with st.spinner(f"Gerando {CONTENT_LABELS[content_type].lower()}…"):
                result = run_content(
                    user,
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
        show_result(result, user)


def page_compare(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    st.header("Comparar versões")
    st.caption(f"Mesmo cenário em cada versão, temperatura fixa em {COMPARISON_TEMPERATURE}; muda só o template.")
    profile = pick_customer(user, profiles, "cmp_customer")
    ticket = pick_ticket(user, tickets, profile, "cmp_ticket")
    if ticket is None:
        return
    show_ticket(user, ticket, tickets)

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
                user,
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
            show_result(result, user)

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
    st.session_state.evaluations = save_evaluations(user, comparison, scores)
    st.rerun()


def show_scores(evaluations) -> None:
    rows = {
        label: {v: getattr(ev, key).score for v, ev in evaluations.items()} for key, label in RUBRIC_CRITERIA.items()
    }
    rows["Total"] = {v: ev.total for v, ev in evaluations.items()}
    st.success(f"Avaliação gravada nas execuções (comparison_id {next(iter(evaluations.values())).comparison_id}).")
    st.table(rows)


def page_history(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    st.header("Histórico do cliente" if auth.is_agent(user) else "Meu histórico")
    profile = pick_customer(user, profiles, "hist_customer")
    own, runs = customer_history(user, profile.id, tickets, storage.RUNS_DIR)
    own.sort(key=lambda t: t.created_at, reverse=True)

    st.subheader(f"Tickets ({len(own)})" if auth.is_agent(user) else f"Meus tickets ({len(own)})")
    for t in own:
        name = ticket_name(user, t.id, tickets)
        st.markdown(f"**{name}** · {t.created_at:%d/%m/%Y %H:%M} · `{t.status}` · {t.title}")
        if t.resolution:
            st.caption(f"Resolução: {t.resolution}")

    if not auth.is_agent(user):
        show_customer_answers(user, runs, tickets)
        return

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


def show_customer_answers(user: User, runs, tickets: list[Ticket]) -> None:
    """Respostas já recebidas pelo cliente, no mesmo formato da tela de ajuda (sem dados técnicos)."""
    st.subheader(f"Respostas recebidas ({len(runs)})")
    if not runs:
        st.caption("Você ainda não pediu ajuda para nenhum ticket.")
    for _, run in reversed(runs):
        name = ticket_name(user, run.ticket.id, tickets)
        with st.expander(f"{run.timestamp:%d/%m/%Y %H:%M} · Ticket {name} · {CONTENT_LABELS[run.content_type]}"):
            show_output(run.content_type, OUTPUT_MODELS[run.content_type].model_validate(run.output))


def page_tickets(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    agent = auth.is_agent(user)
    st.header("Tickets" if agent else "Abrir ticket")
    if message := st.session_state.pop("ticket_message", None):
        st.success(message)

    if agent:
        st.subheader("Abrir ticket")
    with st.form("new_ticket", clear_on_submit=True):
        profile = pick_customer(user, profiles, "new_customer")
        title = st.text_input("Título")
        description = st.text_area("Descrição (como o cliente escreveu)" if agent else "Descreva o problema")
        if st.form_submit_button("Abrir ticket"):
            ticket = open_ticket(user, profiles, profile.id, title, description, path=storage.TICKETS_PATH)
            st.session_state.ticket_message = (
                f"Ticket {ticket.id} criado para {profile.id} · {profile.name}."
                if agent
                else f"Ticket {ticket_name(user, ticket.id, [*tickets, ticket])} aberto. "
                "Você já pode pedir ajuda com ele em “Ajuda com um ticket”."
            )
            st.rerun()

    if not auth.is_agent(user):
        return
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
            ticket = resolve_ticket(user, profiles, ticket_id, resolution, path=storage.TICKETS_PATH)
            st.session_state.ticket_message = (
                f"Ticket {ticket.id} fechado. A resolução passa a entrar no contexto "
                f"dos próximos tickets de {ticket.customer_id}."
            )
            st.rerun()


PAGE_HANDLERS = {
    "Gerar conteúdo": page_generate,
    "Comparar versões": page_compare,
    "Histórico do cliente": page_history,
    "Tickets": page_tickets,
}


def show_demo_accounts(profiles: dict[str, Profile], users: dict[str, User]) -> None:
    st.caption(
        "Senhas fictícias, só para a demonstração (o arquivo de contas guarda apenas o hash). "
        "O atendente vê tudo; cada cliente vê só os próprios tickets e recebe passo a passo e perguntas."
    )
    rows = []
    for username, password in auth.DEMO_ACCOUNTS:
        user = users.get(username)
        if user is None:  # conta removida do users.json: não oferece credencial que não funciona
            continue
        if auth.is_agent(user):
            account = "Atendente (todos os clientes)"
        else:
            p = profiles[user.customer_id]
            account = f"Cliente {p.id} · {p.name} ({p.technical_level}, {p.channel})"
        rows.append({"Usuário": username, "Senha": password, "Conta": account})
    st.dataframe(rows, hide_index=True)


def page_login() -> None:
    st.title("Assistente de suporte")
    st.caption("Entre com uma conta de cliente ou de atendente. As contas de teste estão na aba ao lado.")
    try:
        profiles = storage.load_profiles(storage.PROFILES_PATH)
        users = storage.load_users(profiles, storage.USERS_PATH)
    except storage.DataError as exc:
        st.error(f"Erro: {exc}")
        return

    tab_login, tab_demo = st.tabs(["Entrar", "Contas de demonstração"])
    with tab_demo:
        show_demo_accounts(profiles, users)
    with tab_login, st.form("login"):
        username = st.text_input("Usuário")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar", type="primary")
    if not submitted:
        return
    try:
        st.session_state.user = auth.authenticate(users, username, password)
    except AuthError as exc:
        tab_login.error(str(exc))
        return
    st.rerun()


SECRET_KEYS = ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_TEMPERATURE", "GEMINI_MAX_TOKENS")


def load_cloud_secrets() -> None:
    """Copia os secrets do Streamlit (Cloud ou .streamlit/secrets.toml) para o ambiente.

    O núcleo lê a configuração só do ambiente/.env (app/config.py), sem depender do Streamlit.
    Variável já definida no ambiente tem prioridade; sem arquivo de secrets, nada muda.
    """
    try:
        secrets = {key: st.secrets[key] for key in SECRET_KEYS if key in st.secrets}
    except (StreamlitSecretNotFoundError, FileNotFoundError):
        return
    for key, value in secrets.items():
        os.environ.setdefault(key, str(value))


def main() -> None:
    st.set_page_config(page_title="Suporte · Banco Digital", page_icon="💬", layout="wide")
    load_cloud_secrets()
    if not st.session_state.get("logging_ready"):
        runtime.setup_logging()
        st.session_state.logging_ready = True

    user: User | None = st.session_state.get("user")
    if user is None:
        page_login()
        return

    st.sidebar.title("Assistente de suporte")
    role = "atendente" if auth.is_agent(user) else f"cliente {user.customer_id}"
    st.sidebar.caption(f"Conectado como **{user.username}** ({role})")
    if st.sidebar.button("Sair"):
        st.session_state.clear()
        st.rerun()
    pages = AGENT_PAGES if auth.is_agent(user) else CUSTOMER_PAGES
    labels = {} if auth.is_agent(user) else CUSTOMER_PAGE_LABELS
    page = st.sidebar.radio("Página", pages, label_visibility="collapsed", format_func=lambda p: labels.get(p, p))
    if page != "Comparar versões":
        st.session_state.pop("comparison", None)
        st.session_state.pop("evaluations", None)

    try:
        profiles, tickets = load_data()
        # Todos os perfis vão para o núcleo: gravar um ticket valida o tickets.json inteiro, e
        # cada ticket precisa apontar para um cliente existente. O que a conta vê é filtrado
        # na seleção (pick_customer) e nas checagens do serviço.
        PAGE_HANDLERS[page](user, profiles, visible_tickets(user, tickets))
    except USER_ERRORS as exc:
        st.error(f"Erro: {exc}")


main()
