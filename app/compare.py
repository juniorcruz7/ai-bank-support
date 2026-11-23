"""Comparação entre versões de prompt e coleta da rubrica."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app import auth
from app.config import APP_TIMEZONE
from app.llm_client import LLMClient
from app.models import RUBRIC_CRITERIA, Evaluation, Profile, Score, Ticket, User
from app.prompt_builder import PROMPTS_DIR
from app.service import RunResult, run_content
from app.storage import RUNS_DIR, update_run

# RNF06: a comparação usa sempre a mesma temperatura baixa, independentemente do .env.
COMPARISON_TEMPERATURE = 0.2
DEFAULT_VERSIONS = ("v1", "v2")

VersionScores = dict[str, dict[str, Score]]  # versão -> critério -> nota


class CompareError(Exception):
    """Comparação ou rubrica inválida; a mensagem é legível para o usuário."""


@dataclass(frozen=True)
class ComparisonResult:
    comparison_id: str
    content_type: str
    results: tuple[RunResult, ...]  # uma por versão, na ordem pedida

    @property
    def versions(self) -> list[str]:
        return [r.run.prompt_version for r in self.results]


def validate_versions(versions: list[str] | tuple[str, ...]) -> list[str]:
    versions = list(versions)
    if len(versions) < 2:
        raise CompareError("Informe pelo menos 2 versões para comparar (ex.: --versions v1 v2).")
    if len(set(versions)) != len(versions):
        raise CompareError(f"Versões repetidas na comparação: {' '.join(versions)}.")
    return versions


def run_comparison(
    user: User,
    profile: Profile,
    ticket: Ticket,
    tickets: list[Ticket],
    content_type: str,
    versions: list[str] | tuple[str, ...],
    client: LLMClient,
    runs_dir: Path = RUNS_DIR,
    prompts_dir: Path = PROMPTS_DIR,
) -> ComparisonResult:
    """Roda o mesmo cenário em cada versão; só o template muda (RF11)."""
    auth.require_agent(user, "comparar versões")
    versions = validate_versions(versions)
    results = tuple(
        run_content(user, profile, ticket, tickets, content_type, v, client, runs_dir=runs_dir, prompts_dir=prompts_dir)
        for v in versions
    )
    return ComparisonResult(comparison_id=str(uuid.uuid4()), content_type=content_type, results=results)


def save_evaluations(user: User, comparison: ComparisonResult, scores: VersionScores) -> dict[str, Evaluation]:
    """Grava a rubrica no campo evaluation de cada execução comparada (RF12)."""
    auth.require_agent(user, "dar notas da rubrica")
    missing = [v for v in comparison.versions if v not in scores]
    if missing:
        raise CompareError(f"Faltam as notas da(s) versão(ões): {', '.join(missing)}.")
    evaluated_at = datetime.now(APP_TIMEZONE)
    evaluations: dict[str, Evaluation] = {}
    for result in comparison.results:
        version = result.run.prompt_version
        evaluation = Evaluation(comparison_id=comparison.comparison_id, evaluated_at=evaluated_at, **scores[version])
        update_run(result.path, result.run.model_copy(update={"evaluation": evaluation}))
        evaluations[version] = evaluation
    return evaluations


def load_rubric_file(path: Path, versions: list[str]) -> VersionScores:
    """Lê as notas de um JSON: {"v1": {"clarity": {"score": 3, "justification": "..."}, ...}, ...}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CompareError(f"Arquivo de rubrica não encontrado: {path}") from None
    except json.JSONDecodeError as exc:
        raise CompareError(f"JSON inválido na rubrica {path} (linha {exc.lineno}): {exc.msg}") from None
    if not isinstance(data, dict):
        raise CompareError("A rubrica deve ser um objeto JSON com uma chave por versão.")

    scores: VersionScores = {}
    for version in versions:
        if version not in data:
            raise CompareError(f"A rubrica não tem notas para a versão {version}.")
        scores[version] = {}
        for key, label in RUBRIC_CRITERIA.items():
            try:
                scores[version][key] = Score.model_validate(data[version][key])
            except (KeyError, TypeError):
                raise CompareError(f"A rubrica da {version} não tem o critério '{key}' ({label}).") from None
            except ValidationError as exc:
                reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
                raise CompareError(f"Nota inválida em {version}.{key} ({label}): {reason}.") from None
    return scores
