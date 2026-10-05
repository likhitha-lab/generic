"""Writes an AuditLog row. Called from routers after any security- or
data-relevant action (login, resume create/regenerate/delete/download,
admin actions). Failures here are logged but never allowed to fail the
parent request - an audit trail is important, but it must not become a new
way for the app to break.
"""
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def log_action(
    db: Session,
    *,
    user_id: Optional[int],
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    try:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=json.dumps(details) if details is not None else None,
            ip_address=ip_address,
        )
        db.add(entry)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Failed to write audit log entry for action=%s", action)
