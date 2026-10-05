"""Role-Based Access Control.

Implemented as FastAPI dependencies rather than ASGI middleware, since the
role required differs per-route (some routes need any authenticated user,
some need admin specifically) - a dependency factory expresses that far more
directly than a single global middleware with internal path matching would,
and it shows up correctly in the generated OpenAPI docs.
"""
from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.models.role import Role
from app.models.user import User


def require_role(*allowed_roles: str):
    """Usage: `Depends(require_role(Role.ADMIN))` or
    `Depends(require_role(Role.ADMIN, Role.USER))`."""

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role_name not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}",
            )
        return user

    return _check


require_admin = require_role(Role.ADMIN)
require_any_user = require_role(Role.ADMIN, Role.USER)
