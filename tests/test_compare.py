"""Testes da comparação entre versões e da rubrica (RF11, RF12)."""

import json

import pytest
from pydantic import ValidationError

from app.compare import CompareError, load_rubric_file, run_comparison, save_evaluations
from app.models import Run, Score
from app.storage import load_profiles, load_tickets
from tests.fakes import AGENT, VALID_DIAGNOSIS, FakeClient

PROFILES = load_profiles()
TICKETS = load_tickets(PROFILES)
T001 = next(t for t in TICKETS if t.id == "T001")
CRITERIA = ("clarity", "customer_fit", "actionability")


def _scores(*values):
    return {key: Score(score=v, justification=f"motivo {key}") for key, v in zip(CRITERIA, values, strict=True)}


def _compare(tmp_path, versions=("v1", "v2")):
    client = FakeClient(*[VALID_DIAGNOSIS] * len(versions))
    result = run_comparison(AGENT, PROFILES["C001"], T001, TICKETS, "diagnosis", versions, client, runs_dir=tmp_path)
    return result, client


def test_same_scenario_only_template_changes(tmp_path):
    comparison, client = _compare(tmp_path)
    runs = [r.run for r in comparison.results]
    assert comparison.versions == ["v1", "v2"]
    assert len(client.calls) == 2
    assert client.calls[0]["user"] != client.calls[1]["user"]
    assert runs[0].config == runs[1].config and runs[0].model == runs[1].model
    assert runs[0].context_ticket_ids == runs[1].context_ticket_ids == ["T005"]


def test_save_evaluations_links_runs(tmp_path):
    comparison, _ = _compare(tmp_path)
    evaluations = save_evaluations(AGENT, comparison, {"v1": _scores(2, 1, 2), "v2": _scores(4, 5, 4)})

    assert evaluations["v1"].total == 5 and evaluations["v2"].total == 13
    saved = [Run.model_validate_json(r.path.read_text(encoding="utf-8")) for r in comparison.results]
    assert {s.evaluation.comparison_id for s in saved} == {comparison.comparison_id}
    assert saved[1].evaluation.customer_fit.score == 5
    assert saved[0].output == comparison.results[0].run.output  # só a avaliação mudou


def test_save_evaluations_requires_every_version(tmp_path):
    comparison, _ = _compare(tmp_path)
    with pytest.raises(CompareError, match=r"Faltam as notas da\(s\) versão\(ões\): v2"):
        save_evaluations(AGENT, comparison, {"v1": _scores(3, 3, 3)})


@pytest.mark.parametrize("versions, message", [(("v2",), "pelo menos 2"), (("v1", "v1"), "repetidas")])
def test_invalid_versions(tmp_path, versions, message):
    with pytest.raises(CompareError, match=message):
        run_comparison(AGENT, PROFILES["C001"], T001, TICKETS, "diagnosis", versions, FakeClient(), runs_dir=tmp_path)


@pytest.mark.parametrize(
    "data",
    [{"score": 0, "justification": "x"}, {"score": 6, "justification": "x"}, {"score": 3, "justification": "  "}],
)
def test_score_validation(data):
    with pytest.raises(ValidationError):
        Score.model_validate(data)


def _write_rubric(tmp_path, data):
    path = tmp_path / "rubrica.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _full(version_values):
    return {
        version: {key: {"score": v, "justification": "ok"} for key, v in zip(CRITERIA, values, strict=True)}
        for version, values in version_values.items()
    }


def test_load_rubric_file(tmp_path):
    scores = load_rubric_file(_write_rubric(tmp_path, _full({"v1": (1, 2, 3), "v2": (5, 5, 5)})), ["v1", "v2"])
    assert scores["v1"]["actionability"].score == 3


@pytest.mark.parametrize(
    "data, message",
    [
        (_full({"v1": (1, 2, 3)}), "não tem notas para a versão v2"),
        ({"v1": {"clarity": {"score": 1, "justification": "x"}}, "v2": {}}, "não tem o critério 'customer_fit'"),
        (_full({"v1": (1, 2, 3), "v2": (5, 0, 5)}), "Nota inválida em v2.customer_fit"),
        ([1, 2], "objeto JSON"),
    ],
)
def test_invalid_rubric_file(tmp_path, data, message):
    with pytest.raises(CompareError, match=message):
        load_rubric_file(_write_rubric(tmp_path, data), ["v1", "v2"])


def test_rubric_file_missing(tmp_path):
    with pytest.raises(CompareError, match="não encontrado"):
        load_rubric_file(tmp_path / "nao_existe.json", ["v1", "v2"])
