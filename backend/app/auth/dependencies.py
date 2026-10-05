"""FastAPI dependencies for resolving the current authenticated user from a
JWT bearer token. RBAC role-checking dependencies live in
app/middleware/rbac.py and build on top of `get_current_user` here.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.jwt import JWTError, decode_token
from app.database.session import get_db
from app.models.user import User

# tokenUrl is only used by Swagger's "Authorize" dialog, which POSTs
# form-encoded username/password here (see routers/auth.py:login_oauth2).
# The real frontend logs in via JSON at /api/auth/login instead.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if token is None:
        raise _CREDENTIALS_ERROR

    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise _CREDENTIALS_ERROR from exc

    if payload.get("type") != "access":
        raise _CREDENTIALS_ERROR

    user_id = payload.get("sub")
    if user_id is None:
        raise _CREDENTIALS_ERROR

    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR

    return user
