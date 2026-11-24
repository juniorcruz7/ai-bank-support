"""Testes da interface Streamlit com AppTest e cliente LLM falso (RF14)."""

import json
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import runtime, storage
from app.config import ConfigError
from tests.fakes import VALID_BY_TYPE, VALID_DIAGNOSIS, FakeClient, make_user, write_test_users

APP_PATH = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Dados, runs/ e logs/ numa pasta temporária; o Gemini vira um cliente falso."""
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(storage.PROFILES_PATH, data / "profiles.json")
    shutil.copy(storage.TICKETS_PATH, data / "tickets.json")
    monkeypatch.setattr(storage, "PROFILES_PATH", data / "profiles.json")
    monkeypatch.setattr(storage, "TICKETS_PATH", data / "tickets.json")
    monkeypatch.setattr(storage, "RUNS_DIR", tmp_path / "runs")
    write_test_users(data / "users.json")
    monkeypatch.setattr(storage, "USERS_PATH", data / "users.json")
    monkeypatch.setattr(runtime, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(runtime.setup_logging, "__defaults__", (tmp_path / "logs",))

    class Env:
        root = tmp_path
        client = FakeClient()

        def responses(self, *texts):
            self.client = FakeClient(*texts)
            monkeypatch.setattr(runtime, "make_client", lambda **_kwargs: self.client)

        def tickets(self):
            return json.loads((data / "tickets.json").read_text(encoding="utf-8"))

    environment = Env()
    environment.responses()
    return environment


def _app(page: str, user: str = "atendente") -> AppTest:
    """Abre o app já logado (o formulário de login tem testes próprios)."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["user"] = make_user(user)
    at.run()
    at.segmented_control(key="nav").set_value(page).run()
    return at


def _button(at: AppTest, label: str):
    """Botão da área principal pelo rótulo (a ordem muda conforme a página e o cabeçalho)."""
    matches = [b for b in at.main.button if b.label == label]
    assert len(matches) == 1, f"botão {label!r} não encontrado: {[b.label for b in at.main.button]}"
    return matches[0]


def _texts(at: AppTest) -> str:
    parts = [e.value for e in (*at.markdown, *at.caption, *at.code, *at.success, *at.error, *at.info)]
    return "\n".join(str(p) for p in parts)


def test_generate_one_type_and_cache_hit(env):
    env.responses(VALID_DIAGNOSIS)
    at = _app("Gerar conteúdo")
    at.multiselect[0].set_value(["diagnosis"]).run()
    _button(at, "Gerar").click().run()

    assert not at.error
    text = _texts(at)
    assert "Limite noturno de R$ 200" in text and "(gerado agora)" in text
    assert len(list((env.root / "runs" / "C001" / "T001").glob("*.json"))) == 1

    env.responses()  # nenhuma resposta: falharia se chamasse a API
    _button(at, "Gerar").click().run()
    assert "cache hit, sem chamada à API" in _texts(at)


def test_generate_all_types_renders_flowchart(env):
    env.responses(
        *(VALID_BY_TYPE[t] for t in ("diagnosis", "resolution_steps", "clarifying_questions", "flow_summary"))
    )
    at = _app("Gerar conteúdo")
    _button(at, "Gerar").click().run()

    assert not at.error
    assert len(at.code) == 1 and "[n1]" in at.code[0].value
    assert len(list((env.root / "runs" / "C001" / "T001").glob("*.json"))) == 4


def test_config_error_is_shown_without_traceback(env, monkeypatch):
    def no_key(**_kwargs):
        raise ConfigError("GEMINI_API_KEY não definida.")

    monkeypatch.setattr(runtime, "make_client", no_key)
    at = _app("Gerar conteúdo")
    _button(at, "Gerar").click().run()
    assert "GEMINI_API_KEY não definida" in at.error[0].value
    assert not at.exception


def test_compare_saves_rubric_in_both_runs(env):
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["resolution_steps"])
    at = _app("Comparar versões")
    at.selectbox(key="cmp_customer").set_value("C001")
    at.selectbox[2].set_value("resolution_steps").run()
    _button(at, "Comparar").click().run()
    assert not at.error

    for area in at.text_area:
        area.input("Justificativa de teste.")
    at.slider(key="score_v2_clarity").set_value(5)
    _button(at, "Salvar avaliação").click().run()

    assert not at.error
    assert "Avaliação gravada" in at.success[0].value
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in (env.root / "runs" / "C001" / "T001").glob("*.json")]
    evaluations = {r["prompt_version"]: r["evaluation"] for r in runs}
    assert set(evaluations) == {"v1", "v2"}
    assert evaluations["v1"]["comparison_id"] == evaluations["v2"]["comparison_id"]
    assert evaluations["v2"]["clarity"]["score"] == 5


def test_compare_requires_justifications(env):
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["resolution_steps"])
    at = _app("Comparar versões")
    at.selectbox[2].set_value("resolution_steps").run()
    _button(at, "Comparar").click().run()
    _button(at, "Salvar avaliação").click().run()

    assert "Preencha todas as justificativas" in at.error[0].value
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in (env.root / "runs" / "C001" / "T001").glob("*.json")]
    assert all(r["evaluation"] is None for r in runs)


def test_open_and_close_ticket(env):
    at = _app("Tickets")
    at.selectbox(key="new_customer").set_value("C002")
    at.text_input[0].input("Boleto não compensou")
    at.text_area[0].input("Paguei um boleto anteontem e não compensou.")
    _button(at, "Abrir ticket").click().run()

    assert "Ticket T007 criado para C002" in at.success[0].value
    assert env.tickets()[-1]["id"] == "T007"

    at.selectbox[1].set_value("T001")
    at.text_area[1].input("Pix estornado pelo banco de destino.")
    _button(at, "Fechar como resolvido").click().run()
    assert "Ticket T001 fechado" in at.success[0].value
    closed = next(t for t in env.tickets() if t["id"] == "T001")
    assert closed["status"] == "resolvido"


def test_invalid_new_ticket_shows_error(env):
    at = _app("Tickets")
    at.text_input[0].input("Título")
    at.text_area[0].input("curto")
    _button(at, "Abrir ticket").click().run()
    assert "descrição com 5 caracteres" in at.error[0].value
    assert len(env.tickets()) == 6


def test_history_lists_tickets_and_runs(env):
    env.responses(VALID_DIAGNOSIS)
    at = _app("Gerar conteúdo")
    at.multiselect[0].set_value(["diagnosis"]).run()
    _button(at, "Gerar").click().run()

    at.segmented_control(key="nav").set_value("Histórico do cliente").run()
    text = _texts(at)
    assert "Resolução: Limite noturno" in text
    assert len(at.expander) == 1 and "Diagnóstico · v3 · gerado" in at.expander[0].label


# --- login e permissões (RF19–RF21) ------------------------------------------


def test_login_form(env):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    at.text_input[0].input("maria")
    at.text_input[1].input("errada")
    at.main.button[0].click().run()
    assert at.error[0].value == "Usuário ou senha inválidos."
    assert "user" not in at.session_state

    at.text_input[1].input("maria-demo")
    at.main.button[0].click().run()
    assert at.session_state["user"].username == "maria"
    assert any("Conectado como **maria** (cliente C001)" in m.value for m in at.markdown)


def test_logout(env):
    at = _app("Gerar conteúdo")
    _button(at, "Sair").click().run()
    assert "user" not in at.session_state
    assert at.text_input[1].label == "Senha"


def test_customer_help_page_lists_tickets_as_cards(env):
    at = _app("Gerar conteúdo", user="maria")
    assert at.header[0].value == "Ajuda com um ticket"
    assert not at.selectbox and not at.multiselect  # nada para procurar em menus: os tickets estão na tela
    # Cartões com a numeração da conta, mais recente primeiro (T001 é o 2º ticket da Maria).
    titles = [m.value for m in at.markdown if m.value.startswith("**Ticket")]
    assert titles[0].startswith("**Ticket nº 2 · Pix não chegou**")
    assert titles[1].startswith("**Ticket nº 1 · Erro ao fazer Pix à noite**")
    assert [b.label for b in at.main.button if b.label == "Pedir ajuda"] == ["Pedir ajuda", "Pedir ajuda"]
    assert "Histórico usado como contexto" not in _texts(at)


def test_customer_gets_both_answers_without_technical_details(env):
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["clarifying_questions"])
    at = _app("Gerar conteúdo", user="maria")
    at.button(key="help_T001").click().run()

    assert not at.error
    assert [h.value for h in at.subheader] == ["Passo a passo", "Perguntas de esclarecimento"]
    assert "JSON salvo em" not in _texts(at) and "tokens" not in _texts(at)
    saved = [json.loads(f.read_text(encoding="utf-8")) for f in (env.root / "runs" / "C001" / "T001").glob("*.json")]
    assert {r["prompt_version"] for r in saved} == {"v3"}  # versão padrão


def test_customer_without_tickets_opens_first_ticket(env):
    # Regressão: com só o próprio perfil carregado, gravar validava o tickets.json inteiro
    # e falhava em "Ticket 'T001' aponta para cliente inexistente".
    at = _app("Gerar conteúdo", user="renata")
    assert "Você ainda não tem tickets" in at.info[0].value
    _button(at, "Abrir meu primeiro ticket").click().run()
    assert at.session_state["nav"] == "Tickets"  # o botão leva direto ao formulário

    assert at.header[0].value == "Abrir ticket"
    assert not at.selectbox  # sem seletor de cliente
    assert [b.label for b in at.main.button] == ["Sair", "Abrir ticket"]  # sem o formulário de fechar ticket
    at.text_input[0].input("Boleto não compensou")
    at.text_area[0].input("Paguei um boleto anteontem e não compensou.")
    _button(at, "Abrir ticket").click().run()

    assert not at.error
    assert at.success[0].value == "Ticket nº 1 aberto."
    assert env.tickets()[-1]["id"] == "T007"  # o ID interno continua global (RF17)
    assert env.tickets()[-1]["customer_id"] == "C004"

    _button(at, "Pedir ajuda com ele agora").click().run()  # atalho logo após abrir
    assert at.session_state["nav"] == "Gerar conteúdo"
    assert at.button(key="help_T007").label == "Pedir ajuda"


def test_customer_history_shows_answers_not_run_records(env):
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["clarifying_questions"])
    at = _app("Gerar conteúdo", user="maria")
    at.button(key="help_T001").click().run()

    at.segmented_control(key="nav").set_value("Histórico do cliente").run()
    assert at.header[0].value == "Meu histórico"
    assert "Respostas recebidas (2)" in [s.value for s in at.subheader]
    assert any("Ticket nº 2 · Passo a passo" in e.label for e in at.expander)
    assert "**nº 2**" in _texts(at) and "T001" not in _texts(at)
    assert not at.json  # nada do registro técnico da execução


def test_demo_accounts_open_in_a_popup_with_test_only_notice(env):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not at.dataframe and not at.tabs  # nada de credencial antes do clique
    assert [b.label for b in at.main.button] == ["Entrar", "Ver contas de teste"]

    at.main.button[1].click().run()
    assert "apenas para testar o produto" in at.info[0].value
    assert "fictício" in at.info[0].value
    table = at.dataframe[0].value
    assert list(table["Usuário"]) == ["atendente", "maria", "lucas", "carlos", "renata", "ana"]
    assert table["Senha"][1] == "maria-demo"
    assert table["Conta"][1].startswith("Cliente C001 · Maria")
    assert table["Conta"][0] == "Atendente (todos os clientes)"


# --- secrets do Streamlit Community Cloud ------------------------------------


def test_cloud_secrets_become_environment_variables(env, monkeypatch):
    import os

    # setenv + delenv: o monkeypatch apaga de novo a variável que o app criar.
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY")
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.secrets["GEMINI_API_KEY"] = "chave-dos-secrets"
    at.run()
    assert os.environ["GEMINI_API_KEY"] == "chave-dos-secrets"


def test_environment_variable_wins_over_secrets(env, monkeypatch):
    import os

    monkeypatch.setenv("GEMINI_API_KEY", "chave-do-ambiente")
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.secrets["GEMINI_API_KEY"] = "chave-dos-secrets"
    at.run()
    assert os.environ["GEMINI_API_KEY"] == "chave-do-ambiente"


def test_customer_sees_out_of_scope_message(env):
    from tests.fakes import OUT_OF_SCOPE_BY_TYPE, OUT_OF_SCOPE_MESSAGE

    env.responses(OUT_OF_SCOPE_BY_TYPE["resolution_steps"], OUT_OF_SCOPE_BY_TYPE["clarifying_questions"])
    at = _app("Tickets", user="renata")
    at.text_input[0].input("Gato")
    at.text_area[0].input("Meu gato comeu pedra e eu não sei o que fazer.")
    _button(at, "Abrir ticket").click().run()
    at.segmented_control(key="nav").set_value("Gerar conteúdo").run()
    _button(at, "Pedir ajuda").click().run()

    assert not at.error
    assert [w.value for w in at.warning] == [OUT_OF_SCOPE_MESSAGE, OUT_OF_SCOPE_MESSAGE]
    assert not any(m.value.startswith("**Passo") for m in at.markdown)


def test_navigation_is_at_the_top_not_in_the_sidebar(env):
    at = _app("Gerar conteúdo")
    nav = at.segmented_control(key="nav")
    assert list(nav.options) == ["Início", "Gerar conteúdo", "Comparar versões", "Histórico do cliente", "Tickets"]
    assert not at.sidebar.radio and not at.sidebar.button  # nada escondido na barra lateral
    assert any("Assistente de suporte" in m.value for m in at.markdown)
    nav.set_value("Tickets").run()
    assert at.header[0].value == "Tickets"


def test_app_opens_on_home_with_action_cards(env):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["user"] = make_user("atendente")
    at.run()
    assert at.segmented_control(key="nav").value == "Início"
    assert "O que você quer fazer agora?" in _texts(at)
    assert [m.label for m in at.metric] == ["Tickets abertos", "Tickets resolvidos", "Clientes"]
    assert [m.value for m in at.metric] == ["4", "2", "5"]
    assert [b.label for b in at.main.button] == ["Sair", "Gerar", "Comparar", "Ver histórico", "Gerenciar tickets"]

    _button(at, "Comparar").click().run()  # cada cartão leva à página dele
    assert at.header[0].value == "Comparar versões"


def test_customer_home_greets_and_points_to_help(env):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["user"] = make_user("maria")
    at.run()
    assert list(at.segmented_control(key="nav").options) == [
        "Início",
        "Ajuda com um ticket",
        "Meu histórico",
        "Abrir ticket",
    ]
    assert "Olá, Maria! Como podemos ajudar?" in _texts(at)
    assert "Você tem 1 ticket aberto." in _texts(at)
    assert [b.label for b in at.main.button] == ["Sair", "Pedir ajuda", "Abrir ticket", "Ver histórico"]

    _button(at, "Pedir ajuda").click().run()
    assert at.header[0].value == "Ajuda com um ticket"


# --- modo noturno ----------------------------------------------------------------


def _app_module():
    """Carrega as funções do app sem executar a tela (o arquivo chama main() ao ser importado)."""
    import ast
    import types

    source = Path(APP_PATH).read_text(encoding="utf-8")
    tree = ast.parse(source)
    tree.body = [
        n
        for n in tree.body
        if not (
            isinstance(n, ast.Expr) and getattr(n.value, "func", None) and getattr(n.value.func, "id", "") == "main"
        )
    ]
    module = types.ModuleType("streamlit_app_funcs")
    exec(compile(tree, APP_PATH, "exec"), module.__dict__)  # noqa: S102
    return module


def test_resume_code_is_single_use_and_expires():
    app = _app_module()
    code = app.create_resume_code("maria", "Tickets", now=1000.0)
    assert app.consume_resume_code(code, now=1010.0) == ("maria", "Tickets")
    assert app.consume_resume_code(code, now=1010.0) is None  # uso único
    late = app.create_resume_code("maria", "Tickets", now=1000.0)
    assert app.consume_resume_code(late, now=1000.0 + app.RESUME_TTL_S + 1) is None  # vencido
    assert app.consume_resume_code("inventado") is None


def test_theme_switch_script_saves_choice_and_carries_resume_code():
    app = _app_module()
    script = app.theme_switch_script(dark=True, resume_code="abc")
    assert "stActiveTheme-" in script and "-v2" in script and "'Dark'" in script
    assert "?resume=abc" in script
    assert "?resume" not in app.theme_switch_script(dark=False, resume_code=None)


def test_dark_mode_toggle_keeps_user_logged_in_after_reload(env):
    at = _app("Histórico do cliente", user="maria")
    toggle = at.toggle(key="dark_mode")
    assert toggle.label.endswith("Modo noturno") and toggle.value is False
    toggle.set_value(True).run()

    # O app emite o script que grava o tema e recarrega com um código de retomada.
    script = next(e.proto.body for e in at.get("html") if "stActiveTheme" in e.proto.body)
    assert "'Dark'" in script
    code = script.split("?resume=")[1].split("'")[0]

    # Recarregamento: sessão nova, só com o código na URL.
    reloaded = AppTest.from_file(APP_PATH, default_timeout=30)
    reloaded.query_params["resume"] = code
    reloaded.run()
    assert reloaded.session_state["user"].username == "maria"
    assert reloaded.segmented_control(key="nav").value == "Histórico do cliente"
    assert "resume" not in reloaded.query_params  # o código some da URL

    # O código não serve uma segunda vez.
    again = AppTest.from_file(APP_PATH, default_timeout=30)
    again.query_params["resume"] = code
    again.run()
    assert "user" not in again.session_state


def test_login_page_has_hero_and_dark_mode_toggle(env):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert at.toggle(key="dark_mode")
    assert "Assistente de suporte" in _texts(at) and "dados 100% fictícios" in _texts(at)
    assert [t.label for t in at.text_input] == ["Usuário", "Senha"]


def test_customer_history_ignores_answers_of_deleted_tickets(env):
    # Resposta gravada para um ticket que não existe mais (ex.: tickets.json restaurado com git):
    # a visão do cliente não pode quebrar nem mostrar a resposta solta.
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["clarifying_questions"])
    at = _app("Gerar conteúdo", user="maria")
    at.button(key="help_T001").click().run()
    source = next((env.root / "runs" / "C001" / "T001").glob("*resolution_steps*.json"))
    orphan = json.loads(source.read_text(encoding="utf-8"))
    orphan["ticket"] = {"id": "T008", "title": "Ticket apagado", "text": "Não existe mais no tickets.json."}
    (env.root / "runs" / "C001" / "T008").mkdir(parents=True)
    (env.root / "runs" / "C001" / "T008" / source.name).write_text(json.dumps(orphan), encoding="utf-8")

    at.segmented_control(key="nav").set_value("Histórico do cliente").run()
    assert not at.exception
    assert "Respostas recebidas (2)" in [s.value for s in at.subheader]  # só as do T001
    assert not any("T008" in e.label or "apagado" in e.label for e in at.expander)
