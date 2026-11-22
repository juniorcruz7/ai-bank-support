"""Testes da CLI de ponta a ponta com cliente LLM falso (RF02, RF13, RF17, RNF04)."""

import json
import shutil

import pytest

import main
from app.storage import PROFILES_PATH, TICKETS_PATH
from tests.fakes import VALID_BY_TYPE, VALID_DIAGNOSIS, FakeClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isola dados, runs/ e logs/ numa pasta temporária e troca o Gemini por um cliente falso."""
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(PROFILES_PATH, data / "profiles.json")
    shutil.copy(TICKETS_PATH, data / "tickets.json")
    monkeypatch.setattr(main, "PROFILES_PATH", data / "profiles.json")
    monkeypatch.setattr(main, "TICKETS_PATH", data / "tickets.json")
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(main, "is_interactive", lambda: False)

    class Env:
        root = tmp_path
        client = FakeClient()

        def responses(self, *texts):
            self.client = FakeClient(*texts)
            monkeypatch.setattr(main, "make_client", lambda: self.client)

        def answers(self, *values):
            monkeypatch.setattr(main, "is_interactive", lambda: True)
            queue = list(values)
            monkeypatch.setattr("builtins.input", lambda _prompt="": queue.pop(0))

    environment = Env()
    environment.responses()
    return environment


def _generate(*extra):
    return main.main(["generate", "--customer", "C001", "--ticket", "T001", *extra])


def test_generate_prints_output_and_saved_path(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    assert _generate("--type", "diagnosis", "--version", "v2") == 0

    out = capsys.readouterr().out
    assert "=== Diagnóstico · T001 · Maria · v2 ===" in out
    assert "[alta] Limite noturno" in out
    assert "JSON salvo em:" in out and "(gerado agora)" in out
    saved = list((env.root / "runs" / "C001" / "T001").glob("*.json"))
    assert len(saved) == 1 and saved[0].name in out


def test_second_run_shows_cache_hit(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    _generate("--type", "diagnosis")
    env.responses()  # nenhuma resposta: falharia se chamasse a API
    assert _generate("--type", "diagnosis") == 0
    out = capsys.readouterr()
    assert "(cache hit, sem chamada à API)" in out.out
    assert "cache hit" in out.err  # log no terminal
    assert "cache hit" in (env.root / "logs" / "app.log").read_text(encoding="utf-8")


def test_version_defaults_to_v2(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    _generate("--type", "diagnosis")
    assert "· v2 ===" in capsys.readouterr().out


def test_ticket_alone_infers_customer(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    assert main.main(["generate", "--ticket", "T001", "--type", "diagnosis"]) == 0
    assert "Maria" in capsys.readouterr().out


def test_type_all_continues_after_failure(env, capsys):
    env.responses(
        VALID_BY_TYPE["diagnosis"],
        "{}", "{}",  # passo a passo inválido duas vezes
        VALID_BY_TYPE["clarifying_questions"],
        VALID_BY_TYPE["flow_summary"],
    )
    assert _generate("--type", "all") == 1
    captured = capsys.readouterr()
    assert "=== Fluxograma" in captured.out and "=== Perguntas de esclarecimento" in captured.out
    assert "1 de 4 tipos falharam: Passo a passo" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "argv, message",
    [
        (["generate", "--customer", "C999", "--ticket", "T001", "--type", "diagnosis"], "Perfil 'C999' não existe"),
        (["generate", "--customer", "C001", "--ticket", "T999", "--type", "diagnosis"], "Ticket 'T999' não existe"),
        (["generate", "--customer", "C002", "--ticket", "T001", "--type", "diagnosis"], "pertence a C001"),
        (["generate", "--customer", "C001", "--ticket", "T001", "--type", "diagnosis", "--version", "v9"], "Versão 'v9'"),
        (["generate", "--customer", "C001", "--ticket", "T001"], "Informe --type"),
    ],
)
def test_user_errors_are_clean(env, capsys, argv, message):
    env.responses(VALID_DIAGNOSIS)
    assert main.main(argv) == 1
    err = capsys.readouterr().err
    assert f"Erro: " in err and message in err
    assert "Traceback" not in err


def test_missing_api_key_is_clean(env, capsys, monkeypatch):
    from app.config import ConfigError

    def no_key():
        raise ConfigError("GEMINI_API_KEY não definida.")

    monkeypatch.setattr(main, "make_client", no_key)
    assert _generate("--type", "diagnosis") == 1
    assert "Erro: GEMINI_API_KEY não definida." in capsys.readouterr().err


def test_ticket_add(env, capsys):
    argv = ["ticket", "add", "--customer", "C003", "--title", "Maquininha", "--description", "A maquininha não conecta."]
    assert main.main(argv) == 0
    assert "Ticket T007 criado para C003 · Carlos." in capsys.readouterr().out
    tickets = json.loads((env.root / "data" / "tickets.json").read_text(encoding="utf-8"))
    assert tickets[-1]["id"] == "T007" and tickets[-1]["status"] == "aberto"


def test_interactive_flow_with_new_ticket(env, capsys):
    # cliente 2 (C002) -> último item (novo ticket) -> título -> descrição -> tipo 1 -> versão padrão
    env.answers("2", "2", "Pix não chegou", "Mandei um Pix e não chegou.", "1", "")
    env.responses(VALID_DIAGNOSIS)
    assert main.main(["generate"]) == 0
    out = capsys.readouterr().out
    assert "Ticket T007 criado para C002 · Lucas." in out
    assert "=== Diagnóstico · T007 · Lucas · v2 ===" in out
    assert (env.root / "runs" / "C002" / "T007").is_dir()


def test_interactive_rejects_invalid_option(env, capsys):
    env.answers("9", "1", "1", "1", "2")  # 9 é inválido, depois C001, T001, diagnosis, v2
    env.responses(VALID_DIAGNOSIS)
    assert main.main(["generate"]) == 0
    assert "Opção inválida" in capsys.readouterr().out
