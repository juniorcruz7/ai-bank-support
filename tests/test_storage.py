"""Testes de carga e validação de perfis e tickets (RF01, RNF05)."""

import json

import pytest

from app.prompt_builder import get_customer_history
from app.storage import (
    TICKETS_PATH,
    DataError,
    add_ticket,
    close_ticket,
    get_profile,
    load_profiles,
    load_tickets,
)

PROFILE = {
    "id": "C001",
    "name": "P1",
    "technical_level": "leigo",
    "communication_style": "direto",
    "channel": "chat",
}


def _profiles(n: int) -> list[dict]:
    return [{**PROFILE, "id": f"C{i:03d}", "name": f"P{i}"} for i in range(1, n + 1)]


def _ticket(ticket_id: str = "T001", customer_id: str = "C001") -> dict:
    return {
        "id": ticket_id,
        "customer_id": customer_id,
        "created_at": "2026-11-20T21:05:00-03:00",
        "title": "Título",
        "description": "Descrição do problema",
        "status": "aberto",
        "resolution": None,
    }


def _write(tmp_path, name: str, data) -> object:
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


# --- dados reais -----------------------------------------------------------


def test_real_data_loads():
    profiles = load_profiles()
    tickets = load_tickets(profiles)
    assert len(profiles) == 5
    assert len(tickets) == 6
    assert {p.technical_level for p in profiles.values()} == {"leigo", "intermediario", "avancado"}
    assert {p.communication_style for p in profiles.values()} == {"direto", "detalhado", "didatico"}
    assert {p.channel for p in profiles.values()} == {"chat", "email", "telefone"}
    assert sum(t.status == "resolvido" for t in tickets) == 2


# --- perfis ----------------------------------------------------------------


def test_get_profile_missing_lists_available():
    profiles = load_profiles()
    with pytest.raises(DataError, match="Perfil 'C999' não existe. Disponíveis: C001"):
        get_profile(profiles, "C999")


def test_invalid_level_message_shows_allowed_values(tmp_path):
    data = _profiles(3)
    data[1]["technical_level"] = "expert"
    with pytest.raises(DataError, match=r"Perfil 'C002'.*technical_level.*'expert'.*leigo"):
        load_profiles(_write(tmp_path, "profiles.json", data))


@pytest.mark.parametrize("count", [2, 6])
def test_profile_count_out_of_range(tmp_path, count):
    with pytest.raises(DataError, match="de 3 a 5 perfis"):
        load_profiles(_write(tmp_path, "profiles.json", _profiles(count)))


def test_duplicate_profile_id(tmp_path):
    data = _profiles(3)
    data[2]["id"] = "C001"
    with pytest.raises(DataError, match="ID duplicado"):
        load_profiles(_write(tmp_path, "profiles.json", data))


def test_missing_file(tmp_path):
    with pytest.raises(DataError, match="não encontrado"):
        load_profiles(tmp_path / "nao_existe.json")


def test_invalid_json(tmp_path):
    with pytest.raises(DataError, match="JSON inválido"):
        load_profiles(_write(tmp_path, "profiles.json", "[{ quebrado"))


def test_json_not_a_list(tmp_path):
    with pytest.raises(DataError, match="lista"):
        load_profiles(_write(tmp_path, "profiles.json", {"id": "C001"}))


# --- tickets ---------------------------------------------------------------


def test_orphan_ticket(tmp_path):
    profiles = load_profiles(_write(tmp_path, "profiles.json", _profiles(3)))
    path = _write(tmp_path, "tickets.json", [_ticket(customer_id="fantasma")])
    with pytest.raises(DataError, match="cliente inexistente: 'fantasma'"):
        load_tickets(profiles, path)


def test_duplicate_ticket_id(tmp_path):
    profiles = load_profiles(_write(tmp_path, "profiles.json", _profiles(3)))
    path = _write(tmp_path, "tickets.json", [_ticket(), _ticket()])
    with pytest.raises(DataError, match="ID duplicado"):
        load_tickets(profiles, path)


def test_empty_ticket_message(tmp_path):
    profiles = load_profiles(_write(tmp_path, "profiles.json", _profiles(3)))
    path = _write(tmp_path, "tickets.json", [{**_ticket(), "description": "  "}])
    with pytest.raises(DataError, match="Ticket 'T001'.*descrição vazio"):
        load_tickets(profiles, path)


# --- novo ticket (RF17) ----------------------------------------------------


def _real_data_copy(tmp_path):
    profiles = load_profiles()
    path = tmp_path / "tickets.json"
    path.write_text(TICKETS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return profiles, path


def test_add_ticket_appends_with_next_id(tmp_path):
    profiles, path = _real_data_copy(tmp_path)
    before = load_tickets(profiles, path)

    ticket = add_ticket(profiles, "C002", "Boleto não compensou", "Paguei um boleto anteontem e não compensou.", path)

    after = load_tickets(profiles, path)
    assert ticket.id == "T007"
    assert ticket.status == "aberto" and ticket.resolution is None
    assert ticket.created_at.utcoffset().total_seconds() == -3 * 3600
    assert [t.id for t in after] == [*(t.id for t in before), "T007"]
    assert after[:-1] == before  # nada existente foi alterado


def test_next_id_uses_highest_number(tmp_path):
    profiles = load_profiles(_write(tmp_path, "profiles.json", _profiles(3)))
    path = _write(tmp_path, "tickets.json", [_ticket("T001"), _ticket("T010")])
    assert add_ticket(profiles, "C001", "Título", "Descrição longa o bastante", path).id == "T011"


def test_add_ticket_unknown_customer_writes_nothing(tmp_path):
    profiles, path = _real_data_copy(tmp_path)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(DataError, match="Perfil 'C999' não existe"):
        add_ticket(profiles, "C999", "Título", "Descrição longa o bastante", path)
    assert path.read_text(encoding="utf-8") == original


def test_add_ticket_invalid_text_writes_nothing(tmp_path):
    profiles, path = _real_data_copy(tmp_path)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(DataError, match="Ticket inválido: description: descrição com 5 caracteres"):
        add_ticket(profiles, "C001", "Título", "curto", path)
    assert path.read_text(encoding="utf-8") == original


# --- fechar ticket (RF18) --------------------------------------------------


def test_close_ticket_sets_status_and_resolution(tmp_path):
    profiles, path = _real_data_copy(tmp_path)
    before = load_tickets(profiles, path)

    closed = close_ticket(profiles, "T001", "  Pix estornado pelo banco de destino.  ", path)

    after = load_tickets(profiles, path)
    assert closed.status == "resolvido"
    assert closed.resolution == "Pix estornado pelo banco de destino."
    assert next(t for t in after if t.id == "T001") == closed
    assert [t for t in after if t.id != "T001"] == [t for t in before if t.id != "T001"]


def test_closed_resolution_feeds_next_ticket_context(tmp_path):
    profiles, path = _real_data_copy(tmp_path)
    close_ticket(profiles, "T001", "Pix estornado pelo banco de destino.", path)
    new = add_ticket(profiles, "C001", "Pix de novo", "Fiz outro Pix e também não chegou.", path)

    history = get_customer_history(load_tickets(profiles, path), new)
    assert history[0].id == "T001"
    assert history[0].resolution == "Pix estornado pelo banco de destino."


@pytest.mark.parametrize(
    ("ticket_id", "resolution", "message"),
    [
        ("T999", "Resolvido.", "Ticket 'T999' não existe"),
        ("T005", "Outra resolução.", "Ticket T005 já está resolvido"),
        ("T001", "   ", "Resolução inválida: resolution: resolução vazio"),
        ("T001", "x" * 2001, "resolução com 2001 caracteres; máximo 2000"),
    ],
)
def test_close_ticket_invalid_writes_nothing(tmp_path, ticket_id, resolution, message):
    profiles, path = _real_data_copy(tmp_path)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(DataError, match=message):
        close_ticket(profiles, ticket_id, resolution, path)
    assert path.read_text(encoding="utf-8") == original
