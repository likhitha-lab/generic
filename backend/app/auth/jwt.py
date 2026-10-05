"""JWT access/refresh token creation and verification.

Access tokens are short-lived and carry the user id + role (so RBAC checks
don't need a DB hit on every request). Refresh tokens are longer-lived and
carry only the user id + a `type: refresh` claim, so a refresh token can
never be mistaken for (or used as) an access token even if replayed at the
wrong endpoint.

There is no server-side revocation list in this version (documented as a
future enhancement in ARCHITECTURE.md) - keeping the auth layer fully
stateless keeps it simple and is a reasonable trade-off for a first
production cut.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = "HS256"


def _create_token(subject: str, role: Optional[str], token_type: Literal["access", "refresh"], expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if role is not None:
        payload["role"] = role
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user_id: int, role: str) -> str:
    return _create_token(
        subject=str(user_id),
        role=role,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int) -> str:
    return _create_token(
        subject=str(user_id),
        role=None,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> dict:
    """Raises jose.JWTError on any invalid/expired/malformed token."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])


__all__ = ["create_access_token", "create_refresh_token", "decode_token", "JWTError"]
