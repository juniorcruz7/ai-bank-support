"""Modelos Pydantic: Profile, Ticket, saídas por tipo e Evaluation."""

from typing import Any, ClassVar, Literal

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

    @field_validator("resolution")
    @classmethod
    def _validate_resolution(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value, "resolução", MAX_DESCRIPTION_LENGTH)

    @model_validator(mode="after")
    def _resolved_needs_resolution(self) -> "Ticket":
        if self.status == "resolvido" and not (self.resolution and self.resolution.strip()):
            raise ValueError("ticket resolvido precisa de 'resolution'")
        return self


Role = Literal["customer", "agent"]


class User(BaseModel):
    """Conta de acesso (RF19): cliente aponta para um perfil; atendente não."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=r"^[a-z0-9_.-]{3,32}$")
    role: Role
    customer_id: str | None = None
    password_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _customer_link(self) -> "User":
        if self.role == "customer" and not self.customer_id:
            raise ValueError("conta de cliente precisa de 'customer_id'")
        if self.role == "agent" and self.customer_id is not None:
            raise ValueError("conta de atendente não tem 'customer_id'")
        return self


# --- Saídas do LLM -----------------------------------------------------------
# O schema é só estrutural (vale igual para todas as versões). Restrições de quantidade
# e estilo ficam no prompt. As checagens abaixo ficam em validadores, fora do JSON
# schema enviado ao Gemini, para manter o schema simples e compatível.
#
# `out_of_scope`: o ticket não é um pedido de suporte do banco que dê para entender
# (assunto sem relação com o banco, texto sem sentido). O campo existe no schema de
# todas as versões, mas só o prompt da v3 instrui o modelo a usá-lo. Preenchido, ele
# substitui o conteúdo: as listas ficam vazias e as regras de "ao menos 1 item" não valem.

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


def _require(condition: bool, field: str, message: str = "lista vazia; é preciso ao menos 1 item") -> None:
    if not condition:
        raise ValueError(f"{field}: {message}")


class ScopedOutput(BaseModel):
    """Base das 4 saídas: permite recusar um ticket fora do escopo em vez de inventar conteúdo."""

    out_of_scope: str | None = None
    # Valores do conteúdo quando o ticket está fora do escopo (cada saída define os seus).
    EMPTY_CONTENT: ClassVar[dict[str, Any]] = {}

    @model_validator(mode="before")
    @classmethod
    def _clear_content_when_out_of_scope(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        message = data.get("out_of_scope")
        if isinstance(message, str) and message.strip():
            return {**data, **cls.EMPTY_CONTENT, "out_of_scope": message.strip()}
        return {**data, "out_of_scope": None}  # ausente, nulo ou vazio = ticket no escopo

    @property
    def is_out_of_scope(self) -> bool:
        return self.out_of_scope is not None


class Hypothesis(BaseModel):
    description: str
    probability: Probability

    _check_description = field_validator("description")(_non_empty)


class Diagnosis(ScopedOutput):
    """RF04: hipóteses, evidências, dados faltantes e conclusão provável."""

    hypotheses: list[Hypothesis]
    evidence: list[str]
    missing_info: list[str]
    likely_conclusion: str

    EMPTY_CONTENT: ClassVar[dict[str, Any]] = {
        "hypotheses": [],
        "evidence": [],
        "missing_info": [],
        "likely_conclusion": "",
    }
    _check_lists = field_validator("evidence", "missing_info")(_non_empty_items)

    @model_validator(mode="after")
    def _require_content(self) -> "Diagnosis":
        if not self.is_out_of_scope:
            _require(bool(self.hypotheses), "hypotheses")
            self.likely_conclusion = self.likely_conclusion.strip()
            _require(bool(self.likely_conclusion), "likely_conclusion", "texto vazio")
        return self


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


class ResolutionSteps(ScopedOutput):
    """RF05: passo a passo para o cliente."""

    steps: list[Step]
    escalation_note: str

    EMPTY_CONTENT: ClassVar[dict[str, Any]] = {"steps": [], "escalation_note": ""}

    @model_validator(mode="after")
    def _require_content(self) -> "ResolutionSteps":
        if not self.is_out_of_scope:
            _require(bool(self.steps), "steps")
            self.escalation_note = self.escalation_note.strip()
            _require(bool(self.escalation_note), "escalation_note", "texto vazio")
        return self


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


class ClarifyingQuestions(ScopedOutput):
    """RF06: perguntas de esclarecimento priorizadas."""

    questions: list[Question]

    EMPTY_CONTENT: ClassVar[dict[str, Any]] = {"questions": []}

    @model_validator(mode="after")
    def _require_content(self) -> "ClarifyingQuestions":
        if not self.is_out_of_scope:
            _require(bool(self.questions), "questions")
        return self


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


class FlowSummary(ScopedOutput):
    """RF07: árvore de decisão; o desenho em ASCII é feito pelo código."""

    decision_tree: DecisionTree | None = None

    EMPTY_CONTENT: ClassVar[dict[str, Any]] = {"decision_tree": None}

    @model_validator(mode="after")
    def _require_content(self) -> "FlowSummary":
        if not self.is_out_of_scope:
            _require(self.decision_tree is not None, "decision_tree", "árvore ausente")
        return self


# --- Rubrica da comparação (RF12) ------------------------------------------

RUBRIC_CRITERIA = {
    "clarity": "Clareza",
    "customer_fit": "Adequação ao cliente",
    "actionability": "Acionabilidade",
}


class Score(BaseModel):
    score: int
    justification: str

    _check_justification = field_validator("justification")(_non_empty)

    @field_validator("score")
    @classmethod
    def _score_range(cls, value: int) -> int:
        if not 1 <= value <= 5:
            raise ValueError("a nota deve ser um inteiro de 1 a 5")
        return value


class Evaluation(BaseModel):
    """Notas de uma versão numa comparação; o comparison_id liga as execuções comparadas."""

    comparison_id: str
    clarity: Score
    customer_fit: Score
    actionability: Score
    evaluated_at: AwareDatetime

    @property
    def total(self) -> int:
        return sum(getattr(self, key).score for key in RUBRIC_CRITERIA)


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
    evaluation: Evaluation | None = None  # preenchido pelo modo comparação
