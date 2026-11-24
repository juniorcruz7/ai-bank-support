"""Login e permissões (RF19–RF22, RNF13, RNF14).

Senhas só como hash PBKDF2-SHA256 com salt (biblioteca padrão). As regras de acesso
ficam aqui e são chamadas pela camada de serviço, para valer igual na CLI e na web.
Gerar o hash de uma senha nova: python -m app.auth
"""

import base64
import functools
import getpass
import hashlib
import hmac
import secrets

from app.models import User
from app.prompt_builder import CONTENT_TYPES, DEFAULT_VERSION

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000
SALT_BYTES = 16
CUSTOMER_CONTENT_TYPES = ("resolution_steps", "clarifying_questions")
LOGIN_FAILED = "Usuário ou senha inválidos."

# Credenciais fictícias do pop-up "Ver contas de teste" da tela de login da web.
# Um teste garante que batem com data/users.json.
DEMO_ACCOUNTS = (
    ("atendente", "atendente-demo"),
    ("maria", "maria-demo"),
    ("lucas", "lucas-demo"),
    ("carlos", "carlos-demo"),
    ("renata", "renata-demo"),
    ("ana", "ana-demo"),
)


class AuthError(Exception):
    """Login recusado; a mensagem não diz se o erro foi no usuário ou na senha."""


class AccessDeniedError(Exception):
    """Ação não permitida para a conta; a mensagem é legível e não expõe dados de outros clientes."""


# --- senhas ------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None) -> str:
    """Formato: pbkdf2_sha256$<iterações>$<salt base64>$<hash base64>."""
    if not password:
        raise ValueError("senha vazia")
    salt = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Compara em tempo constante; hash malformado conta como senha errada."""
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except ValueError:
        return False


@functools.cache
def _dummy_hash() -> str:
    # Usado quando o usuário não existe: o tempo de resposta fica parecido com o de senha errada.
    return hash_password(secrets.token_hex(8))


def authenticate(users: dict[str, User], username: str, password: str) -> User:
    user = users.get(username.strip().lower())
    if user is None:
        verify_password(password, _dummy_hash())
        raise AuthError(LOGIN_FAILED)
    if not verify_password(password, user.password_hash):
        raise AuthError(LOGIN_FAILED)
    return user


# --- permissões (tabela de Contas e permissões) -------------------------------


def is_agent(user: User) -> bool:
    return user.role == "agent"


def allowed_content_types(user: User) -> tuple[str, ...]:
    return CONTENT_TYPES if is_agent(user) else CUSTOMER_CONTENT_TYPES


def require_agent(user: User, action: str) -> None:
    if not is_agent(user):
        raise AccessDeniedError(f"Acesso negado: {action} é só para atendentes.")


def require_customer_access(user: User, customer_id: str) -> None:
    """Cliente só acessa o próprio cadastro; atendente acessa todos."""
    if not is_agent(user) and user.customer_id != customer_id:
        raise AccessDeniedError(f"Acesso negado: a conta {user.username!r} só acessa os próprios tickets.")


def require_content_type(user: User, content_type: str) -> None:
    if content_type not in allowed_content_types(user):
        raise AccessDeniedError(
            f"Acesso negado: {content_type} é de uso interno do atendente. "
            f"Disponível para clientes: {', '.join(CUSTOMER_CONTENT_TYPES)}."
        )


def require_version(user: User, version: str) -> None:
    if not is_agent(user) and version != DEFAULT_VERSION:
        raise AccessDeniedError(f"Acesso negado: contas de cliente usam a versão padrão do prompt ({DEFAULT_VERSION}).")


if __name__ == "__main__":
    first = getpass.getpass("Nova senha: ")
    if first != getpass.getpass("Repita a senha: "):
        raise SystemExit("As senhas não conferem.")
    print(hash_password(first))
