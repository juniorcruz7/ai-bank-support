"""Testes da chave de cache e da busca em runs/ (RNF02, RF10)."""

import shutil

import pytest

from app.cache import compute_cache_key, find_cached_run
from app.prompt_builder import PROMPTS_DIR, build_prompt, get_customer_history
from app.storage import load_profiles, load_tickets

PROFILES = load_profiles()
TICKETS = load_tickets(PROFILES)
BY_ID = {t.id: t for t in TICKETS}
CONFIG = {"temperature": 0.2, "max_tokens": 2048, "thinking_budget": 0}


def _key(
    profile=None,
    ticket=None,
    tickets=TICKETS,
    content_type="diagnosis",
    version="v2",
    provider="gemini",
    model="gemini-2.5-flash",
    config=CONFIG,
    prompts_dir=PROMPTS_DIR,
):
    ticket = ticket or BY_ID["T001"]
    profile = profile or PROFILES[ticket.customer_id]
    prompt = build_prompt(profile, ticket, tickets, content_type, version, prompts_dir)
    history = get_customer_history(tickets, ticket)
    return compute_cache_key(profile, ticket, prompt, history, provider, model, config)


BASE_KEY = _key()


def test_key_is_sha256_and_deterministic():
    assert len(BASE_KEY) == 64 and int(BASE_KEY, 16) >= 0
    assert _key() == BASE_KEY


def test_whitespace_only_changes_keep_key():
    t001 = BY_ID["T001"]
    spaced = t001.model_copy(update={"description": t001.description.replace(" ", "   \n ")})
    assert _key(ticket=spaced) == BASE_KEY


@pytest.mark.parametrize(
    "change",
    [
        {"content_type": "flow_summary"},
        {"version": "v1"},
        {"provider": "outro"},
        {"model": "gemini-outro"},
        {"config": {**CONFIG, "temperature": 0.7}},
        {"config": {**CONFIG, "max_tokens": 4096}},
    ],
)
def test_each_component_changes_key(change):
    assert _key(**change) != BASE_KEY


def test_profile_change_changes_key():
    profile = PROFILES["C001"].model_copy(update={"channel": "chat"})
    assert _key(profile=profile) != BASE_KEY


def test_ticket_text_change_changes_key():
    ticket = BY_ID["T001"].model_copy(update={"description": BY_ID["T001"].description + " Urgente."})
    assert _key(ticket=ticket) != BASE_KEY


def test_history_content_change_changes_key():
    # Mudar a resolução do T005 (histórico do T001) muda a chave.
    tickets = [t.model_copy(update={"resolution": "Outra resolução."}) if t.id == "T005" else t for t in TICKETS]
    assert _key(tickets=tickets) != BASE_KEY


def test_template_edit_changes_only_that_type(tmp_path):
    shutil.copytree(PROMPTS_DIR / "v2", tmp_path / "v2")
    flow_before = _key(content_type="flow_summary", prompts_dir=tmp_path)
    diagnosis = tmp_path / "v2" / "diagnosis.md"
    diagnosis.write_text(diagnosis.read_text(encoding="utf-8") + "\nSeja breve.", encoding="utf-8")

    assert _key(prompts_dir=tmp_path) != BASE_KEY
    assert _key(content_type="flow_summary", prompts_dir=tmp_path) == flow_before


def test_find_cached_run_missing_folder(tmp_path):
    assert find_cached_run("C001", BASE_KEY, tmp_path) is None
