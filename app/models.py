"""Modelos Pydantic: Profile, Ticket, saídas por tipo e Evaluation."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

TechnicalLevel = Literal["leigo", "intermediario", "avancado"]
CommunicationStyle = Literal["direto", "detalhado", "didatico"]
Channel = Literal["chat", "email", "telefone"]
TicketStatus = Literal["aberto", "resolvido"]

MAX_TITLE_LENGTH = 120
MIN_DESCRIPTION_LENGTH = 10
MAX_DESCRIPTION_LENGTH = 2000


def _clean_text(value: str, field: str, max_length: int, min_length: int = 1) -> str:
    """Remove espaços das pontas e valida texto não vazio e dentro dos limites."""
    value = value.strip()
    if not value:
        raise ValueError(f"{field} vazio")
    if len(value) < min_length:
        raise ValueError(f"{field} com {len(value)} caracteres; mínimo {min_length}")
    if len(value) > max_length:
        raise ValueError(f"{field} com {len(value)} caracteres; máximo {max_length}")
    return value


class Profile(BaseModel):
    """Perfil de cliente que orienta o tom e o formato das respostas."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^C\d{3,}$")
    name: str = Field(min_length=1)
    technical_level: TechnicalLevel
    communication_style: CommunicationStyle
    channel: Channel


class Ticket(BaseModel):
    """Ticket de suporte aberto por um cliente."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^T\d{3,}$")
    customer_id: str = Field(min_length=1)
    created_at: AwareDatetime
    title: str
    description: str
    status: TicketStatus = "aberto"
    resolution: str | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _clean_text(value, "título", MAX_TITLE_LENGTH)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _clean_text(value, "descrição", MAX_DESCRIPTION_LENGTH, MIN_DESCRIPTION_LENGTH)

    @model_validator(mode="after")
    def _resolved_needs_resolution(self) -> "Ticket":
        if self.status == "resolvido" and not (self.resolution and self.resolution.strip()):
            raise ValueError("ticket resolvido precisa de 'resolution'")
        return self


# --- Saídas do LLM -----------------------------------------------------------
# O schema é só estrutural (vale igual para v1 e v2). Restrições de quantidade e
# estilo ficam no prompt da v2. As checagens abaixo ficam em validadores, fora do
# JSON schema enviado ao Gemini, para manter o schema simples e compatível.

Probability = Literal["alta", "media", "baixa"]
NodeType = Literal["decision", "action"]
ROOT_NODE_ID = "n1"


def _non_empty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("texto vazio")
    return value


def _non_empty_items(items: list[str]) -> list[str]:
    return [_non_empty(item) for item in items]


def _at_least_one(items: list) -> list:
    if not items:
        raise ValueError("lista vazia; é preciso ao menos 1 item")
    return items


class Hypothesis(BaseModel):
    description: str
    probability: Probability

    _check_description = field_validator("description")(_non_empty)


class Diagnosis(BaseModel):
    """RF04: hipóteses, evidências, dados faltantes e conclusão provável."""

    hypotheses: list[Hypothesis]
    evidence: list[str]
    missing_info: list[str]
    likely_conclusion: str

    _check_hypotheses = field_validator("hypotheses")(_at_least_one)
    _check_lists = field_validator("evidence", "missing_info")(_non_empty_items)
    _check_conclusion = field_validator("likely_conclusion")(_non_empty)


class Step(BaseModel):
    order: int
    action: str
    expected_result: str
    if_fails: str

    _check_texts = field_validator("action", "expected_result", "if_fails")(_non_empty)

    @field_validator("order")
    @classmethod
    def _positive_order(cls, value: int) -> int:
        if value < 1:
            raise ValueError("order deve ser 1 ou maior")
        return value


class ResolutionSteps(BaseModel):
    """RF05: passo a passo para o cliente."""

    steps: list[Step]
    escalation_note: str

    _check_steps = field_validator("steps")(_at_least_one)
    _check_note = field_validator("escalation_note")(_non_empty)


class Question(BaseModel):
    question: str
    why_it_matters: str
    priority: int

    _check_texts = field_validator("question", "why_it_matters")(_non_empty)

    @field_validator("priority")
    @classmethod
    def _priority_range(cls, value: int) -> int:
        if not 1 <= value <= 3:
            raise ValueError("priority deve ser 1, 2 ou 3")
        return value


class ClarifyingQuestions(BaseModel):
    """RF06: perguntas de esclarecimento priorizadas."""

    questions: list[Question]

    _check_questions = field_validator("questions")(_at_least_one)


class FlowNode(BaseModel):
    id: str
    type: NodeType
    text: str
    yes: str | None = None
    no: str | None = None

    _check_texts = field_validator("id", "text")(_non_empty)


class DecisionTree(BaseModel):
    nodes: list[FlowNode]

    @model_validator(mode="after")
    def _valid_tree(self) -> "DecisionTree":
        by_id: dict[str, FlowNode] = {}
        for node in self.nodes:
            if node.id in by_id:
                raise ValueError(f"id de nó duplicado: {node.id!r}")
            by_id[node.id] = node
        if ROOT_NODE_ID not in by_id:
            raise ValueError(f"nó raiz {ROOT_NODE_ID!r} ausente")

        for node in self.nodes:
            if node.type == "decision":
                for branch in ("yes", "no"):
                    target = getattr(node, branch)
                    if not target:
                        raise ValueError(f"nó de decisão {node.id!r} sem '{branch}'")
                    if target not in by_id:
                        raise ValueError(f"nó {node.id!r} aponta para nó inexistente {target!r}")
            elif node.yes or node.no:
                raise ValueError(f"nó de ação {node.id!r} não pode ter 'yes' nem 'no'")

        # Busca em profundidade a partir da raiz: detecta ciclo e nós soltos.
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in done:
                return
            if node_id in visiting:
                raise ValueError(f"ciclo na árvore passando por {node_id!r}")
            visiting.add(node_id)
            node = by_id[node_id]
            for target in (node.yes, node.no):
                if target:
                    visit(target)
            visiting.discard(node_id)
            done.add(node_id)

        visit(ROOT_NODE_ID)
        unreachable = [n.id for n in self.nodes if n.id not in done]
        if unreachable:
            raise ValueError(f"nós inalcançáveis a partir de {ROOT_NODE_ID!r}: {', '.join(unreachable)}")
        return self


class FlowSummary(BaseModel):
    """RF07: árvore de decisão; o desenho em ASCII é feito pelo código."""

    decision_tree: DecisionTree


# --- Registro de execução (runs/) -------------------------------------------

ContentType = Literal["diagnosis", "resolution_steps", "clarifying_questions", "flow_summary"]


class RunTicket(BaseModel):
    id: str
    title: str
    text: str


class RunUsage(BaseModel):
    """Custo da chamada ao LLM (RNF11); ausente quando a saída veio do cache."""

    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    llm_calls: int  # 2 quando houve retry por saída inválida
    http_attempts: int  # inclui retentativas por 429/5xx/timeout


class Run(BaseModel):
    """Uma execução gravada em runs/<customer_id>/<ticket_id>/ (RF09)."""

    run_id: str
    timestamp: AwareDatetime
    customer_id: str
    ticket: RunTicket
    content_type: ContentType
    prompt_version: str
    provider: str
    model: str
    config: dict[str, Any]
    context_ticket_ids: list[str]
    template_hash: str
    cache_key: str
    cache_hit: bool
    output: dict[str, Any]
    usage: RunUsage | None = None
    evaluation: dict[str, Any] | None = None  # preenchido na comparação (fase 7)
