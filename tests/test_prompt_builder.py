"""Testes do montador de prompt (RF03, RF08, RF16, RNF07)."""

import re
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Ticket
from app.prompt_builder import (
    CONTENT_TYPES,
    NO_HISTORY_TEXT,
    PROMPTS_DIR,
    PromptError,
    build_prompt,
    get_customer_history,
    list_versions,
)
from app.storage import load_profiles, load_tickets

PROFILES = load_profiles()
TICKETS = load_tickets(PROFILES)
TICKETS_BY_ID = {t.id: t for t in TICKETS}
LEFTOVER_PLACEHOLDER = re.compile(r"\$[A-Za-z_{]")


def _build(ticket_id="T001", content_type="diagnosis", version="v2", **kwargs):
    ticket = TICKETS_BY_ID[ticket_id]
    return build_prompt(PROFILES[ticket.customer_id], ticket, TICKETS, content_type, version, **kwargs)


def _ticket(ticket_id: str, customer_id: str, days_ago: int) -> Ticket:
    return Ticket(
        id=ticket_id,
        customer_id=customer_id,
        created_at=datetime(2026, 11, 20, tzinfo=timezone(timedelta(hours=-3))) - timedelta(days=days_ago),
        title=f"Título {ticket_id}",
        description="Descrição do problema",
        status="resolvido",
        resolution="Resolvido.",
    )


# --- versões e montagem ----------------------------------------------------


def test_versions_available():
    assert list_versions() == ["v1", "v2"]


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("content_type", CONTENT_TYPES)
def test_every_version_and_type_builds(version, content_type):
    prompt = _build(content_type=content_type, version=version)
    assert prompt.version == version
    assert prompt.content_type == content_type
    assert prompt.system and prompt.user
    assert not LEFTOVER_PLACEHOLDER.search(prompt.system + prompt.user)
    assert "<ticket>" in prompt.user and "<historico_cliente>" in prompt.user


def test_ticket_text_with_dollar_sign_is_kept():
    # A descrição do T001 tem "R$ 350": valores substituídos não são reinterpretados.
    assert "R$ 350" in _build().user


def test_v1_and_v2_differ():
    v1, v2 = _build(version="v1"), _build(version="v2")
    assert v1.system != v2.system
    assert v1.user != v2.user


# --- histórico do cliente (RF16) -------------------------------------------


def test_t001_history_includes_t005_with_resolution():
    prompt = _build("T001")
    assert prompt.context_ticket_ids == ("T005",)
    assert "T005" in prompt.user and "Limite noturno de R$ 200" in prompt.user


def test_history_ignores_other_customers_and_current_ticket():
    history = get_customer_history(TICKETS, TICKETS_BY_ID["T001"])
    assert all(t.customer_id == "C001" for t in history)
    assert "T001" not in [t.id for t in history]


def test_history_only_previous_tickets():
    # T006 (02/09) é anterior ao T004 (06/11); o inverso não vale.
    assert [t.id for t in get_customer_history(TICKETS, TICKETS_BY_ID["T004"])] == ["T006"]
    assert get_customer_history(TICKETS, TICKETS_BY_ID["T006"]) == []


def test_history_limit_and_order():
    current = _ticket("T100", "C001", days_ago=0)
    old = [_ticket(f"T10{i}", "C001", days_ago=i) for i in range(1, 6)]
    history = get_customer_history([current, *reversed(old)], current)
    assert [t.id for t in history] == ["T101", "T102", "T103"]


def test_no_history_text():
    prompt = _build("T002")
    assert prompt.context_ticket_ids == ()
    assert NO_HISTORY_TEXT in prompt.user


# --- regras de perfil (v2) -------------------------------------------------


@pytest.mark.parametrize("content_type", ["resolution_steps", "clarifying_questions"])
def test_v2_customer_facing_types_get_profile_rules(content_type):
    user = _build("T001", content_type, "v2").user
    assert "Nível técnico (leigo)" in user
    assert "Canal (telefone)" in user and "no máximo 5 passos" in user


@pytest.mark.parametrize("content_type", ["diagnosis", "flow_summary"])
def test_v2_internal_types_have_no_profile_rules(content_type):
    assert "Nível técnico (leigo)" not in _build("T001", content_type, "v2").user


def test_v1_has_raw_profile_values_only():
    user = _build("T001", "resolution_steps", "v1").user
    assert "nível técnico: leigo" in user
    assert "Nível técnico (leigo)" not in user


# --- erros -----------------------------------------------------------------


def test_unknown_version_lists_available():
    with pytest.raises(PromptError, match="Versão 'v9' não existe. Disponíveis: v1, v2"):
        _build(version="v9")


def test_unknown_content_type():
    with pytest.raises(PromptError, match="Tipo 'resumo' inválido"):
        _build(content_type="resumo")


def test_ticket_from_other_customer_is_rejected():
    with pytest.raises(PromptError, match="pertence a C001"):
        build_prompt(PROFILES["C002"], TICKETS_BY_ID["T001"], TICKETS, "diagnosis", "v2")


def test_unknown_placeholder_in_template(tmp_path):
    shutil.copytree(PROMPTS_DIR / "v2", tmp_path / "v2")
    (tmp_path / "v2" / "diagnosis.md").write_text("Oi $nome_errado", encoding="utf-8")
    with pytest.raises(PromptError, match=r"\$nome_errado"):
        _build(prompts_dir=tmp_path)


def test_missing_template_file(tmp_path):
    shutil.copytree(PROMPTS_DIR / "v2", tmp_path / "v2")
    (tmp_path / "v2" / "flow_summary.md").unlink()
    with pytest.raises(PromptError, match="Template ausente"):
        _build(content_type="flow_summary", prompts_dir=tmp_path)


# --- RNF07: nova versão sem mexer no Python --------------------------------


def test_new_version_is_just_a_new_folder(tmp_path):
    shutil.copytree(PROMPTS_DIR / "v2", tmp_path / "v3")
    (tmp_path / "v3" / "system.md").write_text("Persona da v3.", encoding="utf-8")
    prompt = _build(version="v3", prompts_dir=tmp_path)
    assert prompt.version == "v3"
    assert prompt.system == "Persona da v3."
