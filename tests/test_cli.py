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
        client_kwargs: dict = {}

        def responses(self, *texts):
            self.client = FakeClient(*texts)

            def make_client(**kwargs):
                self.client_kwargs = kwargs
                return self.client

            monkeypatch.setattr(main, "make_client", make_client)

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


# --- compare (RF11, RF12) --------------------------------------------------

CRITERIA = ("clarity", "customer_fit", "actionability")
COMPARE_HEADER = "=== Comparação · Diagnóstico · T001 · Maria ==="


def _rubric_file(root, v1=(2, 1, 2), v2=(4, 5, 4)):
    data = {
        version: {key: {"score": score, "justification": f"{version} {key}"} for key, score in zip(CRITERIA, scores)}
        for version, scores in (("v1", v1), ("v2", v2))
    }
    path = root / "rubrica.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _compare(*extra):
    return main.main(["compare", "--customer", "C001", "--ticket", "T001", "--type", "diagnosis", *extra])


def _saved_runs(root):
    return [json.loads(p.read_text(encoding="utf-8")) for p in (root / "runs" / "C001" / "T001").glob("*.json")]


def test_compare_with_rubric_file(env, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    env.responses(VALID_DIAGNOSIS, VALID_DIAGNOSIS)
    assert _compare("--rubric", str(_rubric_file(env.root))) == 0

    lines = capsys.readouterr().out.splitlines()
    header_index = lines.index(COMPARE_HEADER)
    assert " | v2" in lines[header_index + 2]  # colunas lado a lado
    assert lines[-3].split() == ["Total", "5", "13"]
    assert env.client_kwargs == {"temperature": 0.2}  # RNF06

    runs = _saved_runs(env.root)
    assert sorted(r["prompt_version"] for r in runs) == ["v1", "v2"]
    assert len({r["evaluation"]["comparison_id"] for r in runs}) == 1
    v2 = next(r for r in runs if r["prompt_version"] == "v2")
    assert v2["evaluation"]["customer_fit"] == {"score": 5, "justification": "v2 customer_fit"}


def test_compare_interactive_rubric_reasks_invalid_input(env, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")  # terminal estreito: saídas empilhadas
    answers = ["7", "3", "", "clara"]  # nota inválida, nota ok, justificativa vazia, justificativa ok
    answers += ["3", "ok"] * 2 + ["4", "boa"] * 3
    env.answers(*answers)
    env.responses(VALID_DIAGNOSIS, VALID_DIAGNOSIS)
    assert _compare() == 0

    out = capsys.readouterr().out
    assert "--- v1 ---" in out and "--- v2 ---" in out
    assert "Nota inválida" in out and "não pode ficar vazia" in out
    v1 = next(r for r in _saved_runs(env.root) if r["prompt_version"] == "v1")
    assert v1["evaluation"]["clarity"] == {"score": 3, "justification": "clara"}


def test_compare_without_rubric_fails_before_calling_api(env, capsys):
    env.responses()
    assert _compare() == 1
    assert "Informe --rubric" in capsys.readouterr().err
    assert env.client.calls == []
    assert not (env.root / "runs").exists()


@pytest.mark.parametrize(
    "extra, message",
    [(["--versions", "v2"], "pelo menos 2 versões"), (["--versions", "v2", "v2"], "Versões repetidas")],
)
def test_compare_invalid_versions(env, capsys, extra, message):
    assert _compare(*extra) == 1
    assert message in capsys.readouterr().err


def test_compare_invalid_rubric_file_fails_before_calling_api(env, capsys):
    env.responses()
    assert _compare("--rubric", str(_rubric_file(env.root, v1=(2, 1, 9)))) == 1
    assert "Nota inválida em v1.actionability" in capsys.readouterr().err
    assert env.client.calls == []
