"""Uma regra por linha da tabela de Contas e permissões, testada no núcleo (RF20–RF22, RNF14)."""

import pytest

from app.auth import AccessDeniedError
from app.compare import ComparisonResult, run_comparison, save_evaluations
from app.models import Score
from app.prompt_builder import DEFAULT_VERSION
from app.service import customer_history, open_ticket, resolve_ticket, run_content, visible_profiles, visible_tickets
from app.storage import TICKETS_PATH, load_profiles, load_tickets
from tests.fakes import AGENT, MARIA, VALID_BY_TYPE, VALID_DIAGNOSIS, FakeClient

PROFILES = load_profiles()
TICKETS = load_tickets(PROFILES)
BY_ID = {t.id: t for t in TICKETS}
SCORES = {k: Score(score=3, justification="ok") for k in ("clarity", "customer_fit", "actionability")}


@pytest.fixture
def tickets_path(tmp_path):
    path = tmp_path / "tickets.json"
    path.write_text(TICKETS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _generate(user, ticket_id, content_type, tmp_path, version=DEFAULT_VERSION):
    ticket = BY_ID[ticket_id]
    client = FakeClient(VALID_BY_TYPE[content_type])
    result = run_content(
        user, PROFILES[ticket.customer_id], ticket, TICKETS, content_type, version, client, runs_dir=tmp_path
    )
    return result, client


# --- Abrir ticket: cliente só para si; atendente para qualquer cliente ------------


def test_customer_opens_ticket_for_self(tickets_path):
    ticket = open_ticket(MARIA, PROFILES, "C001", "Cartão", "Meu cartão foi bloqueado.", tickets_path)
    assert ticket.customer_id == "C001"


def test_customer_cannot_open_ticket_for_someone_else(tickets_path):
    original = tickets_path.read_text(encoding="utf-8")
    with pytest.raises(AccessDeniedError, match="só acessa os próprios tickets"):
        open_ticket(MARIA, PROFILES, "C002", "Cartão", "Meu cartão foi bloqueado.", tickets_path)
    assert tickets_path.read_text(encoding="utf-8") == original


def test_agent_opens_ticket_for_any_customer(tickets_path):
    assert open_ticket(AGENT, PROFILES, "C005", "Token", "O token não aparece no app.", tickets_path).id == "T007"


# --- Passo a passo e perguntas: dos próprios tickets (cliente) / de todos --------


@pytest.mark.parametrize("content_type", ["resolution_steps", "clarifying_questions"])
def test_customer_gets_customer_facing_content_for_own_ticket(tmp_path, content_type):
    result, client = _generate(MARIA, "T001", content_type, tmp_path)
    assert result.run.content_type == content_type and len(client.calls) == 1


def test_customer_cannot_generate_for_other_customers_ticket(tmp_path):
    with pytest.raises(AccessDeniedError):
        _generate(MARIA, "T002", "resolution_steps", tmp_path)
    assert not tmp_path.joinpath("C002").exists()


@pytest.mark.parametrize("content_type", ["resolution_steps", "clarifying_questions"])
def test_agent_gets_any_customers_content(tmp_path, content_type):
    result, _ = _generate(AGENT, "T002", content_type, tmp_path)
    assert result.run.customer_id == "C002"


# --- Diagnóstico e fluxograma: só atendente --------------------------------------


@pytest.mark.parametrize("content_type", ["diagnosis", "flow_summary"])
def test_customer_cannot_get_internal_content(tmp_path, content_type):
    with pytest.raises(AccessDeniedError, match="uso interno do atendente"):
        _generate(MARIA, "T001", content_type, tmp_path)
    assert not tmp_path.joinpath("C001").exists()


@pytest.mark.parametrize("content_type", ["diagnosis", "flow_summary"])
def test_agent_gets_internal_content(tmp_path, content_type):
    assert _generate(AGENT, "T001", content_type, tmp_path)[0].run.content_type == content_type


# --- Histórico: só o próprio (cliente) / de todos ---------------------------------


def _seed_runs(tmp_path):
    """Execuções de Maria de todos os tipos, uma delas com rubrica."""
    for content_type in ("diagnosis", "resolution_steps", "flow_summary"):
        _generate(AGENT, "T001", content_type, tmp_path)
    comparison = run_comparison(
        AGENT,
        PROFILES["C001"],
        BY_ID["T001"],
        TICKETS,
        "clarifying_questions",
        ["v1", "v2"],
        FakeClient(VALID_BY_TYPE["clarifying_questions"], VALID_BY_TYPE["clarifying_questions"]),
        runs_dir=tmp_path,
    )
    save_evaluations(AGENT, comparison, {"v1": SCORES, "v2": SCORES})


def test_customer_history_hides_internal_runs_and_rubric(tmp_path):
    _seed_runs(tmp_path)
    tickets, runs = customer_history(MARIA, "C001", TICKETS, tmp_path)

    assert {t.id for t in tickets} == {"T001", "T005"}
    assert {r.content_type for _, r in runs} == {"resolution_steps", "clarifying_questions"}
    assert all(r.evaluation is None for _, r in runs)


def test_customer_cannot_read_other_customers_history(tmp_path):
    with pytest.raises(AccessDeniedError):
        customer_history(MARIA, "C003", TICKETS, tmp_path)


def test_agent_sees_full_history(tmp_path):
    _seed_runs(tmp_path)
    _, runs = customer_history(AGENT, "C001", TICKETS, tmp_path)
    assert {r.content_type for _, r in runs} == {
        "diagnosis",
        "resolution_steps",
        "flow_summary",
        "clarifying_questions",
    }
    assert sum(r.evaluation is not None for _, r in runs) == 2


def test_visible_tickets_and_profiles():
    assert {t.customer_id for t in visible_tickets(MARIA, TICKETS)} == {"C001"}
    assert list(visible_profiles(MARIA, PROFILES)) == ["C001"]
    assert visible_tickets(AGENT, TICKETS) == TICKETS
    assert visible_profiles(AGENT, PROFILES) == PROFILES


# --- Fechar ticket: só atendente ----------------------------------------------------


def test_customer_cannot_close_ticket(tickets_path):
    original = tickets_path.read_text(encoding="utf-8")
    with pytest.raises(AccessDeniedError, match="fechar ticket é só para atendentes"):
        resolve_ticket(MARIA, PROFILES, "T001", "Resolvido.", tickets_path)
    assert tickets_path.read_text(encoding="utf-8") == original


def test_agent_closes_ticket(tickets_path):
    assert resolve_ticket(AGENT, PROFILES, "T001", "Resolvido.", tickets_path).status == "resolvido"


# --- Comparar versões e dar notas: só atendente -------------------------------------


def test_customer_cannot_compare_before_calling_api(tmp_path):
    client = FakeClient()
    with pytest.raises(AccessDeniedError, match="comparar versões"):
        run_comparison(MARIA, PROFILES["C001"], BY_ID["T001"], TICKETS, "resolution_steps", ["v1", "v2"], client)
    assert client.calls == []


def test_customer_cannot_save_rubric(tmp_path):
    client = FakeClient(VALID_DIAGNOSIS, VALID_DIAGNOSIS)
    comparison: ComparisonResult = run_comparison(
        AGENT, PROFILES["C001"], BY_ID["T001"], TICKETS, "diagnosis", ["v1", "v2"], client, runs_dir=tmp_path
    )
    with pytest.raises(AccessDeniedError, match="dar notas da rubrica"):
        save_evaluations(MARIA, comparison, {"v1": SCORES, "v2": SCORES})
    assert all(r.run.evaluation is None for r in comparison.results)


# --- Escolher versão do prompt: cliente usa a padrão -----------------------------


def test_customer_uses_default_version_only(tmp_path):
    assert _generate(MARIA, "T001", "resolution_steps", tmp_path)[0].run.prompt_version == DEFAULT_VERSION
    with pytest.raises(AccessDeniedError, match="versão padrão do prompt"):
        _generate(MARIA, "T001", "resolution_steps", tmp_path, version="v1")


def test_agent_chooses_version(tmp_path):
    assert _generate(AGENT, "T001", "resolution_steps", tmp_path, version="v1")[0].run.prompt_version == "v1"
