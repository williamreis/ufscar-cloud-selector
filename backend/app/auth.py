"""
Autenticação da área de gestão.

Senha única de administrador, vinda de variável de ambiente e conferida **no
servidor** — diferente do /control antigo, em que a senha vivia no bundle do
navegador e só escondia um botão.

O token é assinado (HMAC-SHA256) e carrega a própria expiração, então não há
sessão em memória para perder num restart ou num reload do uvicorn. O segredo da
assinatura deriva de ADMIN_PASSWORD quando ADMIN_TOKEN_SECRET não é definido —
consequência prática: trocar a senha invalida todos os tokens emitidos, o que é
o comportamento desejado.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

TOKEN_TTL_SECONDS = int(os.getenv("ADMIN_TOKEN_TTL", str(8 * 60 * 60)))

# Proteção contra força bruta: N tentativas erradas por IP dentro da janela e o
# IP fica em espera. Em memória de propósito — é uma barreira de custo, não um
# controle de segurança que precise sobreviver a restart.
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 5 * 60

_failed_attempts: Dict[str, Tuple[int, float]] = {}


def _admin_password() -> Optional[str]:
    pwd = os.getenv("ADMIN_PASSWORD") or ""
    return pwd or None


def is_configured() -> bool:
    return _admin_password() is not None


def _secret() -> bytes:
    explicit = os.getenv("ADMIN_TOKEN_SECRET")
    if explicit:
        return explicit.encode()
    # Sem segredo próprio, deriva da senha: estável entre restarts e trocado
    # automaticamente quando a senha muda.
    return hashlib.sha256(f"cloud-selector:{_admin_password()}".encode()).digest()


def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode().rstrip("=")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token() -> Tuple[str, int]:
    """Devolve (token, epoch de expiração)."""
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = json.dumps({"exp": expires_at}, separators=(",", ":")).encode()
    return f"{_b64(payload)}.{_sign(payload)}", expires_at


def verify_token(token: str) -> bool:
    try:
        raw_payload, _, signature = token.partition(".")
        if not raw_payload or not signature:
            return False
        payload = _unb64(raw_payload)
        if not hmac.compare_digest(_sign(payload), signature):
            return False
        return int(json.loads(payload).get("exp", 0)) > time.time()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_lockout(request: Request) -> None:
    key = _client_key(request)
    count, first_seen = _failed_attempts.get(key, (0, 0.0))
    if count >= MAX_ATTEMPTS and time.time() - first_seen < LOCKOUT_SECONDS:
        remaining = int(LOCKOUT_SECONDS - (time.time() - first_seen))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas. Tente novamente em {remaining} segundos.",
        )


def register_failure(request: Request) -> None:
    key = _client_key(request)
    count, first_seen = _failed_attempts.get(key, (0, 0.0))
    now = time.time()
    if now - first_seen > ATTEMPT_WINDOW_SECONDS:
        count, first_seen = 0, now
    _failed_attempts[key] = (count + 1, first_seen or now)


def clear_failures(request: Request) -> None:
    _failed_attempts.pop(_client_key(request), None)


def password_matches(candidate: str) -> bool:
    """Comparação em tempo constante — não vaza o tamanho da senha pelo tempo de resposta."""
    expected = _admin_password()
    if expected is None:
        return False
    return hmac.compare_digest(candidate or "", expected)


# ---------------------------------------------------------------------------
# Dependência das rotas protegidas
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    if not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Área de gestão não configurada: defina ADMIN_PASSWORD no backend/.env "
                "e reinicie o backend."
            ),
        )
    if credentials is None or not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão expirada ou inválida. Faça login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
