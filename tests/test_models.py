"""Testes de validação dos modelos Profile e Ticket (RF01, RNF05)."""

import pytest
from pydantic import ValidationError

from app.models import MAX_DESCRIPTION_LENGTH, Profile, Ticket

VALID_PROFILE = {
    "id": "dona_maria",
    "name": "Maria",
    "technical_level": "leigo",
    "communication_style": "didatico",
    "channel": "telefone",
}

VALID_TICKET = {
    "id": "T001",
    "customer_id": "dona_maria",
    "created_at": "2026-11-20T21:05:00-03:00",
    "title": "Pix não chegou",
    "description": "Fiz um Pix e não chegou.",
    "status": "aberto",
    "resolution": None,
}


def test_valid_profile():
    assert Profile.model_validate(VALID_PROFILE).channel == "telefone"


@pytest.mark.parametrize(
    "field, value",
    [
        ("technical_level", "expert"),
        ("communication_style", "formal"),
        ("channel", "whatsapp"),
    ],
)
def test_profile_rejects_value_outside_list(field, value):
    with pytest.raises(ValidationError):
        Profile.model_validate({**VALID_PROFILE, field: value})


def test_profile_rejects_extra_field():
    with pytest.raises(ValidationError):
        Profile.model_validate({**VALID_PROFILE, "cpf": "000"})


def test_valid_ticket_strips_text():
    ticket = Ticket.model_validate({**VALID_TICKET, "description": "  texto  "})
    assert ticket.description == "texto"


@pytest.mark.parametrize("description", ["", "   \n\t "])
def test_ticket_rejects_empty_description(description):
    with pytest.raises(ValidationError, match="descrição vazio"):
        Ticket.model_validate({**VALID_TICKET, "description": description})


def test_ticket_rejects_too_long_description():
    with pytest.raises(ValidationError, match=f"máximo {MAX_DESCRIPTION_LENGTH}"):
        Ticket.model_validate({**VALID_TICKET, "description": "a" * (MAX_DESCRIPTION_LENGTH + 1)})


def test_ticket_accepts_description_at_limit():
    Ticket.model_validate({**VALID_TICKET, "description": "a" * MAX_DESCRIPTION_LENGTH})


def test_ticket_rejects_invalid_status():
    with pytest.raises(ValidationError):
        Ticket.model_validate({**VALID_TICKET, "status": "fechado"})


def test_resolved_ticket_requires_resolution():
    with pytest.raises(ValidationError, match="resolution"):
        Ticket.model_validate({**VALID_TICKET, "status": "resolvido", "resolution": None})


def test_ticket_requires_timezone():
    with pytest.raises(ValidationError):
        Ticket.model_validate({**VALID_TICKET, "created_at": "2026-11-20T21:05:00"})


def test_ticket_rejects_bad_id_format():
    with pytest.raises(ValidationError):
        Ticket.model_validate({**VALID_TICKET, "id": "ticket-1"})
