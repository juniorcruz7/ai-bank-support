"""Testes de carga e validação de perfis e tickets (RF01, RNF05)."""

import json

import pytest

from app.storage import DataError, get_profile, load_profiles, load_tickets

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
