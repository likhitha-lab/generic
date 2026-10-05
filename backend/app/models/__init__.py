"""SQLAlchemy ORM models. Import this module wherever you need
`Base.metadata.create_all()` to see every table - it exists to force all
model modules to be imported (and thus registered on Base.metadata) exactly
once, from one place.
"""
from app.database.base import Base
from app.models.audit_log import AuditLog
from app.models.resume import Resume
from app.models.resume_version import ResumeVersion
from app.models.role import Role
from app.models.user import User

__all__ = ["Base", "Role", "User", "Resume", "ResumeVersion", "AuditLog"]
