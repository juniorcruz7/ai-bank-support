"""Geradores dos 4 tipos de conteúdo de suporte."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.llm_client import LLMClient, LLMResponse
from app.models import ClarifyingQuestions, Diagnosis, FlowSummary, ResolutionSteps
from app.prompt_builder import PROMPTS_DIR, BuiltPrompt, build_retry_note

OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "diagnosis": Diagnosis,
    "resolution_steps": ResolutionSteps,
    "clarifying_questions": ClarifyingQuestions,
    "flow_summary": FlowSummary,
}
MAX_VALIDATION_ATTEMPTS = 2  # 1 tentativa + 1 retry (RNF03)
MAX_ERRORS_SHOWN = 5


class OutputValidationError(Exception):
    """A saída do modelo continuou inválida após o retry."""


@dataclass(frozen=True)
class GenerationResult:
    output: BaseModel
    responses: tuple[LLMResponse, ...]  # uma por tentativa de validação

    @property
    def validation_attempts(self) -> int:
        return len(self.responses)


def _summarize(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors()[:MAX_ERRORS_SHOWN]:
        field = ".".join(str(part) for part in err["loc"]) or "resposta"
        lines.append(f"- {field}: {err['msg']}")
    return "\n".join(lines)


def generate(prompt: BuiltPrompt, client: LLMClient, prompts_dir: Path = PROMPTS_DIR) -> GenerationResult:
    """Chama o LLM e valida a saída; JSON inválido -> 1 retry com o erro, depois erro claro."""
    model = OUTPUT_MODELS[prompt.content_type]
    user = prompt.user
    responses: list[LLMResponse] = []
    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        response = client.generate(prompt.system, user, model)
        responses.append(response)
        try:
            output = model.model_validate_json(response.text)
        except ValidationError as exc:
            error_text = _summarize(exc)
            if attempt < MAX_VALIDATION_ATTEMPTS:
                note = build_retry_note(prompt.version, error_text, prompts_dir)
                user = f"{prompt.user}\n\n{note}" if note else prompt.user
            continue
        return GenerationResult(output=output, responses=tuple(responses))
    raise OutputValidationError(
        f"A resposta do modelo para '{prompt.content_type}' veio fora do formato "
        f"{MAX_VALIDATION_ATTEMPTS} vezes:\n{error_text}"
    )


def _generate_typed(expected: str, prompt: BuiltPrompt, client: LLMClient) -> GenerationResult:
    if prompt.content_type != expected:
        raise ValueError(f"Prompt de '{prompt.content_type}' usado para gerar '{expected}'.")
    return generate(prompt, client)


def generate_diagnosis(prompt: BuiltPrompt, client: LLMClient) -> GenerationResult:
    """RF04."""
    return _generate_typed("diagnosis", prompt, client)


def generate_resolution_steps(prompt: BuiltPrompt, client: LLMClient) -> GenerationResult:
    """RF05."""
    return _generate_typed("resolution_steps", prompt, client)


def generate_clarifying_questions(prompt: BuiltPrompt, client: LLMClient) -> GenerationResult:
    """RF06."""
    return _generate_typed("clarifying_questions", prompt, client)


def generate_flow_summary(prompt: BuiltPrompt, client: LLMClient) -> GenerationResult:
    """RF07."""
    return _generate_typed("flow_summary", prompt, client)
