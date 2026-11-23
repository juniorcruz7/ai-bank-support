"""Testes de senha, login e contas (RF19, RNF13)."""

import json

import pytest

from app.auth import AuthError, authenticate, hash_password, verify_password
from app.storage import DataError, load_profiles, load_users
from tests.fakes import TEST_ACCOUNTS, make_user

PROFILES = load_profiles()


def test_hash_has_algorithm_iterations_and_random_salt():
    first = hash_password("segredo", iterations=1000)
    second = hash_password("segredo", iterations=1000)
    assert first.startswith("pbkdf2_sha256$1000$")
    assert first != second  # salt diferente a cada hash
    assert "segredo" not in first


def test_verify_password():
    stored = hash_password("segredo", iterations=1000)
    assert verify_password("segredo", stored)
    assert not verify_password("Segredo", stored)
    assert not verify_password("", stored)


@pytest.mark.parametrize("stored", ["", "texto", "md5$1$abc$def", "pbkdf2_sha256$x$abc$def", "pbkdf2_sha256$1$@@$@@"])
def test_malformed_hash_is_wrong_password(stored):
    assert not verify_password("segredo", stored)


def test_empty_password_cannot_be_hashed():
    with pytest.raises(ValueError):
        hash_password("")


def _users():
    return {u: make_user(u) for u, _, _ in TEST_ACCOUNTS}


def test_authenticate_returns_user_ignoring_case_and_spaces():
    user = authenticate(_users(), "  Maria ", "maria-demo")
    assert user.username == "maria" and user.role == "customer" and user.customer_id == "C001"


@pytest.mark.parametrize(("username", "password"), [("maria", "errada"), ("ninguem", "maria-demo"), ("", "")])
def test_failed_login_has_same_message(username, password):
    # A mensagem não diz se o usuário existe (RNF13).
    with pytest.raises(AuthError, match="^Usuário ou senha inválidos.$"):
        authenticate(_users(), username, password)


# --- data/users.json ----------------------------------------------------------


def test_real_users_file_has_demo_accounts():
    users = load_users(PROFILES)
    assert {u.role for u in users.values()} == {"customer", "agent"}
    assert {u.customer_id for u in users.values() if u.role == "customer"} == set(PROFILES)
    assert all(u.password_hash.startswith("pbkdf2_sha256$600000$") for u in users.values())
    # Senhas de demonstração documentadas no README.
    assert authenticate(users, "atendente", "atendente-demo").role == "agent"
    assert authenticate(users, "maria", "maria-demo").customer_id == "C001"


def _write_users(tmp_path, accounts):
    path = tmp_path / "users.json"
    path.write_text(json.dumps(accounts), encoding="utf-8")
    return path


ACCOUNT = {"username": "maria", "role": "customer", "customer_id": "C001", "password_hash": "pbkdf2_sha256$1$a$b"}


@pytest.mark.parametrize(
    ("accounts", "message"),
    [
        ([{**ACCOUNT, "customer_id": None}], "conta de cliente precisa de 'customer_id'"),
        ([{**ACCOUNT, "role": "agent"}], "conta de atendente não tem 'customer_id'"),
        ([{**ACCOUNT, "customer_id": "C999"}], "aponta para cliente inexistente: 'C999'"),
        ([ACCOUNT, ACCOUNT], "Usuário duplicado em users.json: 'maria'"),
        ([{**ACCOUNT, "role": "admin"}], "role: valor 'admin' inválido"),
        ([{**ACCOUNT, "username": "Maria Silva"}], "username: formato inválido"),
        ([{**ACCOUNT, "password": "texto puro"}], "password: campo não permitido"),
    ],
)
def test_invalid_users_file(tmp_path, accounts, message):
    with pytest.raises(DataError, match=message):
        load_users(PROFILES, _write_users(tmp_path, accounts))
