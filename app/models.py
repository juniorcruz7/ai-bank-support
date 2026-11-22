"""Modelos Pydantic: Profile, Ticket, saídas por tipo e Evaluation."""

from typing import Literal

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
