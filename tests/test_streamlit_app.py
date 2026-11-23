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
    at.sidebar.radio[0].set_value(page).run()
    return at


def _texts(at: AppTest) -> str:
    parts = [e.value for e in (*at.markdown, *at.caption, *at.code, *at.success, *at.error, *at.info)]
    return "\n".join(str(p) for p in parts)


def test_generate_one_type_and_cache_hit(env):
    env.responses(VALID_DIAGNOSIS)
    at = _app("Gerar conteúdo")
    at.multiselect[0].set_value(["diagnosis"]).run()
    at.main.button[0].click().run()

    assert not at.error
    text = _texts(at)
    assert "Limite noturno de R$ 200" in text and "(gerado agora)" in text
    assert len(list((env.root / "runs" / "C001" / "T001").glob("*.json"))) == 1

    env.responses()  # nenhuma resposta: falharia se chamasse a API
    at.main.button[0].click().run()
    assert "cache hit, sem chamada à API" in _texts(at)


def test_generate_all_types_renders_flowchart(env):
    env.responses(
        *(VALID_BY_TYPE[t] for t in ("diagnosis", "resolution_steps", "clarifying_questions", "flow_summary"))
    )
    at = _app("Gerar conteúdo")
    at.main.button[0].click().run()

    assert not at.error
    assert len(at.code) == 1 and "[n1]" in at.code[0].value
    assert len(list((env.root / "runs" / "C001" / "T001").glob("*.json"))) == 4


def test_config_error_is_shown_without_traceback(env, monkeypatch):
    def no_key(**_kwargs):
        raise ConfigError("GEMINI_API_KEY não definida.")

    monkeypatch.setattr(runtime, "make_client", no_key)
    at = _app("Gerar conteúdo")
    at.main.button[0].click().run()
    assert "GEMINI_API_KEY não definida" in at.error[0].value
    assert not at.exception


def test_compare_saves_rubric_in_both_runs(env):
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["resolution_steps"])
    at = _app("Comparar versões")
    at.selectbox(key="cmp_customer").set_value("C001")
    at.selectbox[2].set_value("resolution_steps").run()
    at.main.button[0].click().run()
    assert not at.error

    for area in at.text_area:
        area.input("Justificativa de teste.")
    at.slider(key="score_v2_clarity").set_value(5)
    at.main.button[-1].click().run()

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
    at.main.button[0].click().run()
    at.main.button[-1].click().run()

    assert "Preencha todas as justificativas" in at.error[0].value
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in (env.root / "runs" / "C001" / "T001").glob("*.json")]
    assert all(r["evaluation"] is None for r in runs)


def test_open_and_close_ticket(env):
    at = _app("Tickets")
    at.selectbox(key="new_customer").set_value("C002")
    at.text_input[0].input("Boleto não compensou")
    at.text_area[0].input("Paguei um boleto anteontem e não compensou.")
    at.main.button[0].click().run()

    assert "Ticket T007 criado para C002" in at.success[0].value
    assert env.tickets()[-1]["id"] == "T007"

    at.selectbox[1].set_value("T001")
    at.text_area[1].input("Pix estornado pelo banco de destino.")
    at.main.button[1].click().run()
    assert "Ticket T001 fechado" in at.success[0].value
    closed = next(t for t in env.tickets() if t["id"] == "T001")
    assert closed["status"] == "resolvido"


def test_invalid_new_ticket_shows_error(env):
    at = _app("Tickets")
    at.text_input[0].input("Título")
    at.text_area[0].input("curto")
    at.main.button[0].click().run()
    assert "descrição com 5 caracteres" in at.error[0].value
    assert len(env.tickets()) == 6


def test_history_lists_tickets_and_runs(env):
    env.responses(VALID_DIAGNOSIS)
    at = _app("Gerar conteúdo")
    at.multiselect[0].set_value(["diagnosis"]).run()
    at.main.button[0].click().run()

    at.sidebar.radio[0].set_value("Histórico do cliente").run()
    text = _texts(at)
    assert "Resolução: Limite noturno" in text
    assert len(at.expander) == 1 and "Diagnóstico · v2 · gerado" in at.expander[0].label


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
    assert "Conectado como **maria** (cliente C001)" in at.sidebar.caption[0].value


def test_logout(env):
    at = _app("Gerar conteúdo")
    at.sidebar.button[0].click().run()
    assert "user" not in at.session_state
    assert at.text_input[1].label == "Senha"


def test_customer_sees_only_allowed_pages_and_types(env):
    at = _app("Gerar conteúdo", user="maria")
    assert list(at.sidebar.radio[0].options) == ["Gerar conteúdo", "Histórico do cliente", "Tickets"]
    assert [o.split(" ")[0] for o in at.selectbox(key="gen_customer").options] == ["C001"]
    assert [t.split(" ")[0] for t in at.selectbox(key="gen_ticket").options] == ["T001", "T005"]
    assert at.multiselect[0].value == ["resolution_steps", "clarifying_questions"]
    assert not any(s.label == "Versão do prompt" for s in at.selectbox)


def test_customer_generates_with_default_version(env):
    env.responses(VALID_BY_TYPE["resolution_steps"])
    at = _app("Gerar conteúdo", user="maria")
    at.multiselect[0].set_value(["resolution_steps"]).run()
    at.main.button[0].click().run()
    assert not at.error
    assert any("Passo a passo · v2" in h.value for h in at.subheader)


def test_customer_tickets_page_has_no_close_form(env):
    at = _app("Tickets", user="maria")
    assert [o.split(" ")[0] for o in at.selectbox(key="new_customer").options] == ["C001"]
    assert len(at.main.button) == 1  # só "Abrir ticket"
    assert not any("Fechar" in s.value for s in at.subheader)
