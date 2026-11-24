"""Interface web (Streamlit) sobre o mesmo núcleo da CLI (RF14), com login (RF19–RF22).

As permissões são checadas na camada de serviço; esconder páginas e opções aqui é só
para a tela não oferecer o que a conta não pode fazer.

Rodar: streamlit run streamlit_app.py
"""

import html
import os
import secrets
import time

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
HOME = "Início"
AGENT_PAGES = (HOME, "Gerar conteúdo", "Comparar versões", "Histórico do cliente", "Tickets")
CUSTOMER_PAGES = (HOME, "Gerar conteúdo", "Histórico do cliente", "Tickets")
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
    number = customer_ticket_numbers(tickets, user.customer_id).get(ticket_id)
    return f"nº {number}" if number is not None else ticket_id  # ticket que não existe mais


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
            st.info("Você ainda não tem tickets. Abra um em “Abrir ticket”, no menu acima.")
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
    if getattr(output, "out_of_scope", None):  # o modelo recusou o ticket (v3)
        st.warning(output.out_of_scope)
        return
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


def go_to(page: str) -> None:
    """Callback dos botões que levam a outra página (roda antes da próxima execução do script)."""
    st.session_state.nav = page


def generate_and_show(
    user: User, profile: Profile, ticket: Ticket, tickets: list[Ticket], types: list[str], version: str
) -> None:
    client = runtime.make_client()
    agent = auth.is_agent(user)
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


def page_generate(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    if not auth.is_agent(user):
        page_customer_help(user, profiles, tickets)
        return
    st.header("Gerar conteúdo")
    profile = pick_customer(user, profiles, "gen_customer")
    ticket = pick_ticket(user, tickets, profile, "gen_ticket")
    if ticket is None:
        return
    show_ticket(user, ticket, tickets)

    allowed = list(auth.allowed_content_types(user))
    col_types, col_version = st.columns([3, 1])
    types = col_types.multiselect("Tipos de conteúdo", allowed, default=allowed, format_func=CONTENT_LABELS.get)
    versions = list_versions(PROMPTS_DIR)
    index = versions.index(DEFAULT_VERSION) if DEFAULT_VERSION in versions else 0
    version = col_version.selectbox("Versão do prompt", versions, index=index)
    if st.button("Gerar", type="primary", icon=":material/auto_awesome:", width="stretch", disabled=not types):
        generate_and_show(user, profile, ticket, tickets, types, version)


def status_badge(ticket: Ticket) -> str:
    return ":green-badge[resolvido]" if ticket.status == "resolvido" else ":blue-badge[aberto]"


def page_customer_help(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    """Cliente: cada ticket é um cartão com o botão "Pedir ajuda"; a resposta aparece no próprio cartão."""
    profile = profiles[user.customer_id]
    st.header("Ajuda com um ticket")
    st.caption(
        "Escolha o ticket e toque em **Pedir ajuda**: você recebe o passo a passo e as perguntas para resolvê-lo."
    )
    numbers = customer_ticket_numbers(tickets, profile.id)
    own = sorted((t for t in tickets if t.customer_id == profile.id), key=lambda t: numbers[t.id], reverse=True)
    if not own:
        st.info("Você ainda não tem tickets.")
        st.button("Abrir meu primeiro ticket", type="primary", icon=":material/add:", on_click=go_to, args=("Tickets",))
        return
    for t in own:
        with st.container(border=True):
            col_text, col_action = st.columns([4, 1], vertical_alignment="center")
            col_text.markdown(f"**Ticket {ticket_name(user, t.id, tickets)} · {t.title}** {status_badge(t)}")
            col_text.caption(f"Aberto em {t.created_at:%d/%m/%Y %H:%M} · {t.description}")
            if t.resolution:
                col_text.caption(f"Resolução: {t.resolution}")
            clicked = col_action.button(
                "Pedir ajuda", key=f"help_{t.id}", type="primary", icon=":material/support_agent:", width="stretch"
            )
            if clicked:
                generate_and_show(user, profile, t, tickets, list(auth.allowed_content_types(user)), DEFAULT_VERSION)


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
        st.markdown(f"**{name}** · {t.created_at:%d/%m/%Y %H:%M} · {status_badge(t)} · {t.title}")
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
    """Respostas já recebidas pelo cliente, no mesmo formato da tela de ajuda (sem dados técnicos).

    Mostra a mais recente de cada tipo por ticket, como a CLI: a mesma resposta repetida pelo cache
    não acrescenta nada ao cliente.
    """
    latest = {}
    for _, run in runs:  # em ordem cronológica: a última de cada par fica
        latest[(run.ticket.id, run.content_type)] = run
    answers = sorted(latest.values(), key=lambda r: r.timestamp, reverse=True)
    st.subheader(f"Respostas recebidas ({len(answers)})")
    if not answers:
        st.caption("Você ainda não pediu ajuda para nenhum ticket.")
    for run in answers:
        name = ticket_name(user, run.ticket.id, tickets)
        with st.expander(f"{run.timestamp:%d/%m/%Y %H:%M} · Ticket {name} · {CONTENT_LABELS[run.content_type]}"):
            show_output(run.content_type, OUTPUT_MODELS[run.content_type].model_validate(run.output))


def page_tickets(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    agent = auth.is_agent(user)
    st.header("Tickets" if agent else "Abrir ticket")
    if message := st.session_state.pop("ticket_message", None):
        st.success(message)
        if not agent:
            st.button(
                "Pedir ajuda com ele agora",
                type="primary",
                icon=":material/support_agent:",
                on_click=go_to,
                args=("Gerar conteúdo",),
            )

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
                else f"Ticket {ticket_name(user, ticket.id, [*tickets, ticket])} aberto."
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


HOME_CARDS = {
    "agent": [
        ("Gerar conteúdo", "Diagnóstico, passo a passo, perguntas e fluxograma para o ticket de um cliente.", "Gerar"),
        ("Comparar versões", "As mesmas respostas em duas versões do prompt, lado a lado, com a rubrica.", "Comparar"),
        ("Histórico do cliente", "Tickets e todas as respostas já geradas para um cliente.", "Ver histórico"),
        ("Tickets", "Abrir um ticket em nome de um cliente ou fechar um ticket resolvido.", "Gerenciar tickets"),
    ],
    "customer": [
        (
            "Gerar conteúdo",
            "Receba o passo a passo e as perguntas para resolver um problema que você já relatou.",
            "Pedir ajuda",
        ),
        ("Tickets", "Conte o que aconteceu. Depois é só pedir ajuda com o ticket novo.", "Abrir ticket"),
        ("Histórico do cliente", "Seus tickets e as respostas que você já recebeu.", "Ver histórico"),
    ],
}


def action_card(page: str, description: str, button: str, labels: dict[str, str]) -> None:
    # "distribute" deixa o título no alto e o botão no rodapé: os botões dos cartões ficam alinhados.
    with st.container(border=True, height="stretch", vertical_alignment="distribute"):
        st.markdown(f"#### {PAGE_ICONS[page]} {labels.get(page, page)}")
        st.caption(description)
        st.button(button, key=f"home_{page}", type="primary", width="stretch", on_click=go_to, args=(page,))


def page_home(user: User, profiles: dict[str, Profile], tickets: list[Ticket]) -> None:
    agent = auth.is_agent(user)
    st.markdown(HERO_CSS, unsafe_allow_html=True)
    open_count = sum(t.status == "aberto" for t in tickets)
    if agent:
        st.markdown(
            '<div class="app-hero"><h2>O que você quer fazer agora?</h2>'
            '<p class="lead">Escolha uma ação abaixo ou use o menu no topo a qualquer momento.</p></div>',
            unsafe_allow_html=True,
        )
        col1, col2, col3 = st.columns(3)
        col1.metric("Tickets abertos", open_count, border=True)
        col2.metric("Tickets resolvidos", len(tickets) - open_count, border=True)
        col3.metric("Clientes", len(profiles), border=True)
    else:
        profile = profiles[user.customer_id]
        if open_count:
            plural = "s" if open_count > 1 else ""
            status = f"Você tem {open_count} ticket{plural} aberto{plural}."
        else:
            status = "Você não tem tickets abertos."
        st.markdown(
            f'<div class="app-hero"><h2>Olá, {html.escape(profile.name)}! Como podemos ajudar?</h2>'
            f'<p class="lead">{status} Escolha o que você precisa:</p></div>',
            unsafe_allow_html=True,
        )
    st.write("")
    labels = {} if agent else CUSTOMER_PAGE_LABELS
    cards = HOME_CARDS["agent" if agent else "customer"]
    for column, (page, description, button) in zip(st.columns(len(cards)), cards, strict=True):
        with column:
            action_card(page, description, button, labels)


PAGE_HANDLERS = {
    HOME: page_home,
    "Gerar conteúdo": page_generate,
    "Comparar versões": page_compare,
    "Histórico do cliente": page_history,
    "Tickets": page_tickets,
}


# Botão "Ver contas de teste": maior e num azul-marinho, mais escuro que o "Entrar" (azul do tema), na
# mesma família de cores do painel de apresentação.
# O Streamlit põe a classe `st-key-<key>` no contêiner do widget; o CSS vale só para este botão.
DEMO_BUTTON_KEY = "demo_accounts"
DEMO_BUTTON_CSS = f"""<style>
.st-key-{DEMO_BUTTON_KEY} button {{
    background-color: #0b3d91;
    border: 1px solid #0b3d91;
    color: #ffffff;
    min-height: 3rem;
}}
.st-key-{DEMO_BUTTON_KEY} button:hover, .st-key-{DEMO_BUTTON_KEY} button:focus {{
    background-color: #082d6b;
    border-color: #082d6b;
    color: #ffffff;
}}
.st-key-{DEMO_BUTTON_KEY} button p {{
    font-size: 1.1rem;
    font-weight: 600;
}}
</style>"""

TEST_ACCOUNTS_NOTICE = (
    "**Estas contas existem apenas para testar o produto.** Tudo aqui é fictício: os clientes, os tickets e as "
    "senhas foram criados para a demonstração e não pertencem a pessoas nem a contas bancárias reais. "
    "As senhas não dão acesso a nada além deste site de teste."
)


def demo_account_rows(profiles: dict[str, Profile], users: dict[str, User]) -> list[dict[str, str]]:
    """Linhas da tabela de contas de teste; conta removida do users.json não aparece."""
    rows = []
    for username, password in auth.DEMO_ACCOUNTS:
        user = users.get(username)
        if user is None:  # não oferece credencial que não funciona
            continue
        if auth.is_agent(user):
            account = "Atendente (todos os clientes)"
        else:
            p = profiles[user.customer_id]
            account = f"Cliente {p.id} · {p.name} ({p.technical_level}, {p.channel})"
        rows.append({"Usuário": username, "Senha": password, "Conta": account})
    return rows


@st.dialog("Contas de teste", width="large")
def show_demo_accounts(profiles: dict[str, Profile], users: dict[str, User]) -> None:
    st.info(TEST_ACCOUNTS_NOTICE)
    st.markdown(
        "- **Atendente:** vê todos os clientes e as 4 funções (diagnóstico, passo a passo, perguntas e "
        "fluxograma), compara versões do prompt e fecha tickets.\n"
        "- **Cliente:** vê só os próprios tickets, abre tickets novos e recebe o passo a passo e as perguntas."
    )
    st.dataframe(demo_account_rows(profiles, users), hide_index=True)
    st.caption(
        "Os dados do site de teste são compartilhados entre os visitantes e voltam aos de exemplo quando o app "
        "reinicia. Feche esta janela e use um usuário e senha da tabela para entrar."
    )


HERO_CSS = """<style>
.app-hero {
    background: linear-gradient(135deg, #1f6feb 0%, #0b3d91 100%);
    color: #ffffff;
    border-radius: 18px;
    padding: 2.25rem 2.5rem;
    box-shadow: 0 10px 30px rgba(15, 50, 120, 0.25);
    margin-bottom: 1.25rem;
}
.app-hero > :last-child { margin-bottom: 0; }
.app-hero h1, .app-hero h2, .app-hero p, .app-hero li { color: #ffffff; }
.app-hero h1 { font-size: 2.3rem; line-height: 1.15; margin: 0 0 0.6rem 0; padding: 0; }
.app-hero h2 { font-size: 1.7rem; line-height: 1.2; margin: 0 0 0.4rem 0; padding: 0; }
.app-hero .lead { font-size: 1.1rem; opacity: 0.92; margin: 0 0 1.4rem 0; }
.app-hero ul { list-style: none; padding: 0; margin: 0; }
.app-hero li { margin: 0.65rem 0; font-size: 1.02rem; display: flex; gap: 0.6rem; align-items: baseline; }
.app-hero li span { font-size: 1.1rem; }
.app-hero .tag {
    display: inline-block; margin-top: 1.4rem; padding: 0.3rem 0.8rem; border-radius: 999px;
    background: rgba(255, 255, 255, 0.16); font-size: 0.85rem;
}
</style>"""

LOGIN_HERO_HTML = """<div class="app-hero">
  <h1>Assistente de suporte</h1>
  <p class="lead">Respostas de suporte geradas com IA para um banco digital: do diagnóstico para o atendente
  ao passo a passo no jeito de cada cliente.</p>
  <ul>
    <li><span>🧭</span> Diagnóstico e fluxograma para o atendente decidir rápido</li>
    <li><span>🗣️</span> Passo a passo e perguntas no tom e no canal de cada cliente</li>
    <li><span>🗂️</span> Histórico por cliente, e nada é gerado duas vezes</li>
  </ul>
  <span class="tag">Projeto de demonstração · dados 100% fictícios</span>
</div>"""


def page_login() -> None:
    st.markdown(HERO_CSS + DEMO_BUTTON_CSS, unsafe_allow_html=True)
    _, col_theme = st.columns([5, 1])
    with col_theme:
        theme_toggle(None)
    try:
        profiles = storage.load_profiles(storage.PROFILES_PATH)
        users = storage.load_users(profiles, storage.USERS_PATH)
    except storage.DataError as exc:
        st.error(f"Erro: {exc}")
        return

    col_hero, col_login = st.columns([1.15, 1], gap="large", vertical_alignment="center")
    col_hero.markdown(LOGIN_HERO_HTML, unsafe_allow_html=True)
    with col_login, st.container(border=True):
        st.markdown("### Entrar")
        st.caption("Use sua conta de cliente ou de atendente.")
        with st.form("login", border=False):
            username = st.text_input("Usuário")
            password = st.text_input("Senha", type="password")
            submitted = st.form_submit_button("Entrar", type="primary", icon=":material/login:", width="stretch")
        st.caption("Primeira vez aqui? Use uma das contas de teste.")
        if st.button(
            "Ver contas de teste",
            key=DEMO_BUTTON_KEY,
            icon=":material/key:",
            width="stretch",
            help="Usuários e senhas fictícios para testar o produto",
        ):
            show_demo_accounts(profiles, users)
        if not submitted:
            return
        try:
            st.session_state.user = auth.authenticate(users, username, password)
        except AuthError as exc:
            st.error(str(exc))
            return
    st.rerun()


# --- modo noturno ---------------------------------------------------------------------
# O tema escuro é o nativo do Streamlit (estiliza todos os componentes). A escolha fica no navegador,
# na mesma chave que o seletor do menu ⋮ usa, e o Streamlit só a lê ao carregar a página: por isso o
# botão grava a escolha e recarrega. Para não perder o login no recarregamento, o servidor entrega um
# código aleatório, de uso único e curto, que devolve a pessoa à mesma conta e página.
RESUME_TTL_S = 60


@st.cache_resource
def _resume_codes() -> dict[str, tuple[str, str, float]]:
    return {}


def create_resume_code(username: str, page: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    codes = _resume_codes()
    for code, (_, _, expires) in list(codes.items()):  # descarta os vencidos
        if expires < now:
            codes.pop(code, None)
    code = secrets.token_urlsafe(24)
    codes[code] = (username, page, now + RESUME_TTL_S)
    return code


def consume_resume_code(code: str, now: float | None = None) -> tuple[str, str] | None:
    """Devolve (usuário, página) uma única vez, se o código existir e não tiver vencido."""
    entry = _resume_codes().pop(code, None)
    if entry is None or entry[2] < (time.time() if now is None else now):
        return None
    return entry[0], entry[1]


def theme_switch_script(dark: bool, resume_code: str | None) -> str:
    target = "Dark" if dark else "Light"
    query = f"?resume={resume_code}" if resume_code else ""
    return (
        "<script>"
        "const key = 'stActiveTheme-' + window.location.pathname + '-v2';"
        f"window.localStorage.setItem(key, JSON.stringify('{target}'));"
        f"window.location.replace(window.location.pathname + '{query}');"
        "</script>"
    )


def theme_toggle(user: User | None) -> None:
    is_dark = st.context.theme.type == "dark"
    wants_dark = st.toggle(":material/dark_mode: Modo noturno", value=is_dark, key="dark_mode")
    if wants_dark != is_dark:
        code = create_resume_code(user.username, st.session_state.get("nav", HOME)) if user else None
        st.html(theme_switch_script(wants_dark, code), unsafe_allow_javascript=True)


def resume_after_theme_switch() -> None:
    """Depois do recarregamento do modo noturno, devolve a pessoa à conta e à página em que estava."""
    code = st.query_params.get("resume")
    if not code:
        return
    del st.query_params["resume"]
    resumed = consume_resume_code(code)
    if resumed is None or "user" in st.session_state:
        return
    username, page = resumed
    profiles = storage.load_profiles(storage.PROFILES_PATH)
    user = storage.load_users(profiles, storage.USERS_PATH).get(username)
    if user is not None:
        st.session_state.user = user
        st.session_state.nav = page


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


# Ícones da navegação (Material Symbols, embutidos no Streamlit).
PAGE_ICONS = {
    HOME: ":material/home:",
    "Gerar conteúdo": ":material/auto_awesome:",
    "Comparar versões": ":material/compare_arrows:",
    "Histórico do cliente": ":material/history:",
    "Tickets": ":material/confirmation_number:",
}


# Navegação: botões altos, texto maior e a página atual preenchida com a cor do tema (o botão
# selecionado tem aria-checked="true"). Também reduz o espaço vazio acima do cabeçalho.
NAV_CSS = """<style>
.st-key-nav button { min-height: 3.25rem; }
.st-key-nav button p { font-size: 1.05rem; font-weight: 600; }
.st-key-nav button[aria-checked="true"] {
    background-color: #1f6feb !important;
    border-color: #1f6feb !important;
    color: #ffffff !important;
}
.st-key-nav button[aria-checked="true"] p,
.st-key-nav button[aria-checked="true"] span { color: #ffffff !important; }
[data-testid="stMainBlockContainer"] { padding-top: 4rem; }
</style>"""


def render_header(user: User) -> str:
    """Cabeçalho com quem está conectado, o botão Sair e a navegação em botões grandes no topo.

    A navegação fica na área principal, e não na barra lateral, para ser vista de cara (inclusive
    no celular, onde a barra lateral começa recolhida).
    """
    st.markdown(NAV_CSS, unsafe_allow_html=True)
    col_title, col_user, col_theme, col_logout = st.columns([4, 3, 1.6, 1], vertical_alignment="center")
    col_title.markdown("### :material/support_agent: Assistente de suporte")
    role = "atendente" if auth.is_agent(user) else f"cliente {user.customer_id}"
    col_user.markdown(f":material/account_circle: Conectado como **{user.username}** ({role})")
    with col_theme:
        theme_toggle(user)
    if col_logout.button("Sair", icon=":material/logout:", width="stretch"):
        st.session_state.clear()
        st.rerun()

    pages = AGENT_PAGES if auth.is_agent(user) else CUSTOMER_PAGES
    labels = {} if auth.is_agent(user) else CUSTOMER_PAGE_LABELS
    page = st.segmented_control(
        "Navegação",
        pages,
        default=pages[0],
        required=True,  # sempre há uma página escolhida: clicar na atual não desmarca
        format_func=lambda p: f"{PAGE_ICONS[p]} {labels.get(p, p)}",
        key="nav",
        label_visibility="collapsed",
        width="stretch",
    )
    return page


def main() -> None:
    st.set_page_config(page_title="Suporte · Banco Digital", page_icon="💬", layout="wide")
    load_cloud_secrets()
    if not st.session_state.get("logging_ready"):
        runtime.setup_logging()
        st.session_state.logging_ready = True

    resume_after_theme_switch()
    user: User | None = st.session_state.get("user")
    if user is None:
        page_login()
        return

    page = render_header(user)
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
