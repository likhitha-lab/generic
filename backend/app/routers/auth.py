"""Authentication endpoints: register, login, refresh, current user.

Registration always creates a `user`-role account - there is no way for a
client to grant itself `admin` through this API. Admins are seeded at
startup (see database/init_db.py) or promoted directly in the database /
by another admin (see routers/admin.py), which is the standard safe pattern
for RBAC bootstrapping.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.hashing import hash_password, verify_password
from app.auth.jwt import JWTError, create_access_token, create_refresh_token, decode_token
from app.core.config import settings
from app.core.limiter import limiter
from app.database.session import get_db
from app.models.role import Role
from app.models.user import User
from app.schemas.auth import RefreshRequest, TokenResponse, UserLogin, UserOut, UserRegister
from app.utils.audit import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    # Built explicitly rather than via Pydantic's from_attributes auto-mapping:
    # User.role is a relationship (the Role object), while UserOut.role is a
    # string, so a naive attribute-name match would serialize the wrong thing.
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role_name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def register(request: Request, data: UserRegister, db: Session = Depends(get_db)) -> UserOut:
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")

    user_role = db.query(Role).filter(Role.name == Role.USER).first()
    if user_role is None:
        raise HTTPException(status_code=500, detail="Roles are not seeded - database was not initialized correctly")

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        role_id=user_role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_action(db, user_id=user.id, action="REGISTER", entity_type="user", entity_id=user.id, ip_address=request.client.host if request.client else None)
    return _user_out(user)


def _authenticate(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email).first()

    # Deliberately identical error for "no such user" and "wrong password" -
    # distinguishing them lets an attacker enumerate valid emails.
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated")
    return user


def _issue_tokens(db: Session, user: User, request: Request) -> TokenResponse:
    log_action(db, user_id=user.id, action="LOGIN", entity_type="user", entity_id=user.id, ip_address=request.client.host if request.client else None)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role_name),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def login(request: Request, data: UserLogin, db: Session = Depends(get_db)) -> TokenResponse:
    """JSON login for the actual frontend: {"email": "...", "password": "..."}."""
    user = _authenticate(db, data.email, data.password)
    return _issue_tokens(db, user, request)


@router.post("/token", response_model=TokenResponse)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def login_oauth2(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenResponse:
    """OAuth2 password-grant login (form-encoded username/password) for
    Swagger's "Authorize" dialog only - `oauth2_scheme.tokenUrl` points here.
    `form_data.username` is the account's email; OAuth2PasswordRequestForm
    calls the field "username" regardless of what it actually holds."""
    user = _authenticate(db, form_data.username, form_data.password)
    return _issue_tokens(db, user, request)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(settings.AUTH_RATE_LIMIT)
async def refresh(request: Request, data: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        payload = decode_token(data.refresh_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token")

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists or is inactive")

    return TokenResponse(
        access_token=create_access_token(user.id, user.role_name),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(current_user)
