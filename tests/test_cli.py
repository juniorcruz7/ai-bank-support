"""Testes da CLI de ponta a ponta com cliente LLM falso (RF02, RF13, RF17, RNF04)."""

import json
import shutil

import pytest

import main
from app.storage import PROFILES_PATH, TICKETS_PATH
from tests.fakes import VALID_BY_TYPE, VALID_DIAGNOSIS, FakeClient, write_test_users


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
    write_test_users(data / "users.json")
    monkeypatch.setattr(main, "USERS_PATH", data / "users.json")
    # A senha vem do getpass; nos testes, a senha de demonstração do usuário informado.
    monkeypatch.setattr(main, "_ask_password", lambda _prompt="": env_password[0])
    env_password = ["atendente-demo"]

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
    return main.main(["generate", "--user", "atendente", "--customer", "C001", "--ticket", "T001", *extra])


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


def test_version_defaults_to_v3(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    _generate("--type", "diagnosis")
    assert "· v3 ===" in capsys.readouterr().out


def test_flags_without_version_skip_menu_even_in_terminal(env, capsys):
    env.answers()  # terminal interativo, mas nenhuma resposta disponível
    env.responses(VALID_DIAGNOSIS)
    assert _generate("--type", "diagnosis") == 0
    assert "· v3 ===" in capsys.readouterr().out


def test_ticket_alone_infers_customer(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    assert main.main(["generate", "--user", "atendente", "--ticket", "T001", "--type", "diagnosis"]) == 0
    assert "Maria" in capsys.readouterr().out


def test_type_all_continues_after_failure(env, capsys):
    env.responses(
        VALID_BY_TYPE["diagnosis"],
        "{}",
        "{}",  # passo a passo inválido duas vezes
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
        (
            ["generate", "--user", "atendente", "--customer", "C999", "--ticket", "T001", "--type", "diagnosis"],
            "Perfil 'C999' não existe",
        ),
        (
            ["generate", "--user", "atendente", "--customer", "C001", "--ticket", "T999", "--type", "diagnosis"],
            "Ticket 'T999' não existe",
        ),
        (
            ["generate", "--user", "atendente", "--customer", "C002", "--ticket", "T001", "--type", "diagnosis"],
            "pertence a C001",
        ),
        (
            [
                "generate",
                "--user",
                "atendente",
                "--customer",
                "C001",
                "--ticket",
                "T001",
                "--type",
                "diagnosis",
                "--version",
                "v9",
            ],
            "Versão 'v9'",
        ),
        (["generate", "--user", "atendente", "--customer", "C001", "--ticket", "T001"], "Informe --type"),
    ],
)
def test_user_errors_are_clean(env, capsys, argv, message):
    env.responses(VALID_DIAGNOSIS)
    assert main.main(argv) == 1
    err = capsys.readouterr().err
    assert "Erro: " in err and message in err
    assert "Traceback" not in err


def test_missing_api_key_is_clean(env, capsys, monkeypatch):
    from app.config import ConfigError

    def no_key():
        raise ConfigError("GEMINI_API_KEY não definida.")

    monkeypatch.setattr(main, "make_client", no_key)
    assert _generate("--type", "diagnosis") == 1
    assert "Erro: GEMINI_API_KEY não definida." in capsys.readouterr().err


def test_ticket_add(env, capsys):
    argv = [
        "ticket",
        "add",
        "--user",
        "atendente",
        "--customer",
        "C003",
        "--title",
        "Boleto não compensou",
        "--description",
        "Um cliente pagou um boleto ontem e não compensou.",
    ]
    assert main.main(argv) == 0
    assert "Ticket T007 criado para C003 · Carlos." in capsys.readouterr().out
    tickets = json.loads((env.root / "data" / "tickets.json").read_text(encoding="utf-8"))
    assert tickets[-1]["id"] == "T007" and tickets[-1]["status"] == "aberto"


def test_interactive_flow_with_new_ticket(env, capsys):
    # cliente 2 (C002) -> último item (novo ticket) -> título -> descrição -> tipo 1 -> versão padrão
    env.answers("2", "2", "Pix não chegou", "Mandei um Pix e não chegou.", "1", "")
    env.responses(VALID_DIAGNOSIS)
    assert main.main(["generate", "--user", "atendente"]) == 0
    out = capsys.readouterr().out
    assert "Ticket T007 criado para C002 · Lucas." in out
    assert "=== Diagnóstico · T007 · Lucas · v3 ===" in out
    assert (env.root / "runs" / "C002" / "T007").is_dir()


def test_interactive_rejects_invalid_option(env, capsys):
    env.answers("9", "1", "1", "1", "2")  # 9 é inválido, depois C001, T001, diagnosis, v2
    env.responses(VALID_DIAGNOSIS)
    assert main.main(["generate", "--user", "atendente"]) == 0
    assert "Opção inválida" in capsys.readouterr().out


# --- compare (RF11, RF12) --------------------------------------------------

CRITERIA = ("clarity", "customer_fit", "actionability")
COMPARE_HEADER = "=== Comparação · Diagnóstico · T001 · Maria ==="


def _rubric_file(root, v1=(2, 1, 2), v2=(4, 5, 4)):
    data = {
        version: {
            key: {"score": score, "justification": f"{version} {key}"}
            for key, score in zip(CRITERIA, scores, strict=True)
        }
        for version, scores in (("v1", v1), ("v2", v2))
    }
    path = root / "rubrica.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _compare(*extra):
    return main.main(
        ["compare", "--user", "atendente", "--customer", "C001", "--ticket", "T001", "--type", "diagnosis", *extra]
    )


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


# --- ticket close (RF18) e history (RF15) ----------------------------------


def _ticket_by_id(env, ticket_id):
    data = json.loads((env.root / "data" / "tickets.json").read_text(encoding="utf-8"))
    return next(t for t in data if t["id"] == ticket_id)


def test_ticket_close_with_flags(env, capsys):
    assert (
        main.main(
            ["ticket", "close", "--user", "atendente", "T001", "--resolution", "Pix estornado pelo banco de destino."]
        )
        == 0
    )
    assert "Ticket T001 fechado como resolvido." in capsys.readouterr().out
    ticket = _ticket_by_id(env, "T001")
    assert ticket["status"] == "resolvido"
    assert ticket["resolution"] == "Pix estornado pelo banco de destino."


def test_ticket_close_interactive_lists_only_open_tickets(env, capsys):
    env.answers("1", "Cartão internacional habilitado no app.")
    assert main.main(["ticket", "close", "--user", "atendente"]) == 0
    out = capsys.readouterr().out
    assert "T001 · C001" in out and "T005" not in out
    assert _ticket_by_id(env, "T001")["status"] == "resolvido"


def test_ticket_close_errors_are_readable(env, capsys):
    assert main.main(["ticket", "close", "--user", "atendente", "T005", "--resolution", "Outra."]) == 1
    assert main.main(["ticket", "close", "--user", "atendente", "--resolution", "Sem ID."]) == 1
    err = capsys.readouterr().err
    assert "Erro: Ticket T005 já está resolvido" in err
    assert "Erro: Informe o ID do ticket" in err
    assert "Traceback" not in err


def test_history_lists_tickets_and_runs(env, capsys):
    env.responses(VALID_DIAGNOSIS)
    _generate("--type", "diagnosis")
    (env.root / "runs" / "C001" / "T001" / "quebrado.json").write_text("{", encoding="utf-8")
    capsys.readouterr()

    assert main.main(["history", "--user", "atendente", "--customer", "C001"]) == 0
    out = capsys.readouterr().out
    assert "=== Histórico · C001 · Maria" in out
    assert "T001  20/11/2026 21:05  [aberto]  Pix não chegou" in out
    assert "Resolução: Limite noturno" in out
    assert "Execuções (1):" in out  # o arquivo ilegível é ignorado
    assert "Diagnóstico · v3 · gerado" in out


def test_history_without_runs(env, capsys):
    assert main.main(["history", "--user", "atendente", "--customer", "C002"]) == 0
    out = capsys.readouterr().out
    assert "Tickets (1):" in out and "Execuções (0):" in out and "(nenhuma)" in out


def test_history_unknown_customer(env, capsys):
    assert main.main(["history", "--user", "atendente", "--customer", "C999"]) == 1
    assert "Perfil 'C999' não existe" in capsys.readouterr().err


# --- login e permissões (RF19–RF22) ------------------------------------------


def _as(monkeypatch, password):
    monkeypatch.setattr(main, "_ask_password", lambda _prompt="": password)


def test_wrong_password_is_rejected(env, capsys, monkeypatch):
    _as(monkeypatch, "errada")
    assert main.main(["history", "--user", "atendente", "--customer", "C001"]) == 1
    err = capsys.readouterr().err
    assert "Erro: Usuário ou senha inválidos." in err and "Traceback" not in err


def test_user_is_required_without_terminal(env, capsys):
    assert main.main(["history", "--customer", "C001"]) == 1
    assert "Informe --user" in capsys.readouterr().err


def test_customer_generates_own_content_without_customer_flag(env, capsys, monkeypatch):
    _as(monkeypatch, "maria-demo")
    env.responses(VALID_BY_TYPE["resolution_steps"], VALID_BY_TYPE["clarifying_questions"])
    assert main.main(["generate", "--user", "maria", "--ticket", "T001", "--type", "all"]) == 0
    out = capsys.readouterr().out
    assert "=== Passo a passo · ticket T001 ===" in out
    assert "=== Perguntas de esclarecimento · ticket T001 ===" in out
    assert "Diagnóstico" not in out and "Fluxograma" not in out
    # Como na web, o cliente não vê dados técnicos da execução.
    assert "JSON salvo" not in out and "v3" not in out and "cache" not in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["generate", "--ticket", "T001", "--type", "diagnosis"], "uso interno do atendente"),
        (["generate", "--ticket", "T002", "--type", "resolution_steps"], "só acessa os próprios tickets"),
        (["generate", "--ticket", "T001", "--type", "resolution_steps", "--version", "v1"], "versão padrão"),
        (["compare", "--ticket", "T001", "--type", "resolution_steps"], "comparar versões é só para atendentes"),
        (["ticket", "close", "T001", "--resolution", "Resolvido."], "fechar ticket é só para atendentes"),
        (["history", "--customer", "C002"], "só acessa os próprios tickets"),
        (["ticket", "add", "--customer", "C002", "--title", "X", "--description", "Descrição longa."], "próprios"),
    ],
)
def test_customer_restrictions(env, capsys, monkeypatch, argv, message):
    _as(monkeypatch, "maria-demo")
    env.responses()  # nenhuma chamada à API pode acontecer
    position = 2 if argv[0] == "ticket" else 1
    assert main.main([*argv[:position], "--user", "maria", *argv[position:]]) == 1
    err = capsys.readouterr().err
    assert "Erro: Acesso negado" in err and message in err
    assert env.client.calls == []


def test_customer_history_hides_internal_runs(env, capsys, monkeypatch):
    env.responses(VALID_DIAGNOSIS)
    _generate("--type", "diagnosis")  # como atendente
    _as(monkeypatch, "maria-demo")
    capsys.readouterr()

    assert main.main(["history", "--user", "maria"]) == 0
    out = capsys.readouterr().out
    assert "=== Meu histórico · Maria ===" in out and "Respostas recebidas (0):" in out
    assert "Limite noturno" not in out.split("Respostas recebidas")[1]  # o diagnóstico não aparece


def test_customer_history_shows_answers_without_technical_details(env, capsys, monkeypatch):
    _as(monkeypatch, "maria-demo")
    env.responses(VALID_BY_TYPE["resolution_steps"])
    main.main(["generate", "--user", "maria", "--ticket", "T001", "--type", "resolution_steps"])
    env.responses()
    main.main(["generate", "--user", "maria", "--ticket", "T001", "--type", "resolution_steps"])  # do cache
    capsys.readouterr()

    assert main.main(["history", "--user", "maria"]) == 0
    out = capsys.readouterr().out
    assert "  nº 2 (T001)  20/11/2026 21:05  [aberto]  Pix não chegou" in out
    assert "  nº 1 (T005)" in out
    assert "Respostas recebidas (1):" in out  # a resposta repetida do cache aparece uma vez
    assert "Ticket nº 2 (T001) · Passo a passo ---" in out and "Passo 1: Abra o app" in out
    assert not any(word in out for word in ("runs", ".json", "cache", "v3", "Rubrica", "Execuções"))


def test_customer_interactive_menu_offers_only_allowed_types(env, capsys, monkeypatch):
    _as(monkeypatch, "maria-demo")
    env.answers("1", "1")  # ticket T001, primeiro tipo (passo a passo); sem menu de cliente nem de versão
    env.responses(VALID_BY_TYPE["resolution_steps"])
    assert main.main(["generate", "--user", "maria"]) == 0
    out = capsys.readouterr().out
    assert "Escolha o cliente" not in out and "Escolha a versão" not in out
    assert "1) Passo a passo" in out and "Todos os 2 tipos" in out and "Diagnóstico" not in out


# --- v3: ticket fora do escopo -----------------------------------------------


def test_out_of_scope_ticket_is_refused_and_cached(env, capsys, monkeypatch):
    from tests.fakes import OUT_OF_SCOPE_BY_TYPE, OUT_OF_SCOPE_MESSAGE

    main.main(
        ["ticket", "add", "--user", "atendente", "--customer", "C004", "--title", "Gato", "--description",
         "Meu gato comeu pedra e eu não sei o que fazer."]
    )  # fmt: skip
    env.responses(
        *(OUT_OF_SCOPE_BY_TYPE[t] for t in ("diagnosis", "resolution_steps", "clarifying_questions", "flow_summary"))
    )
    assert main.main(["generate", "--user", "atendente", "--ticket", "T007", "--type", "all"]) == 0
    out = capsys.readouterr().out
    assert out.count("Ticket fora do escopo do suporte:") == 4
    assert OUT_OF_SCOPE_MESSAGE in out and "Passo 1" not in out

    # A cliente recebe a mesma recusa, do cache, sem nova chamada à API.
    monkeypatch.setattr(main, "_ask_password", lambda _prompt="": "renata-demo")
    env.responses()
    assert main.main(["generate", "--user", "renata", "--ticket", "T007", "--type", "resolution_steps"]) == 0
    out = capsys.readouterr().out
    assert OUT_OF_SCOPE_MESSAGE in out and env.client.calls == []
