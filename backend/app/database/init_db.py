"""Creates tables and seeds baseline data on startup.

For a real production rollout you'd use Alembic migrations instead of
`create_all` (see DEPLOYMENT.md) - `create_all` is fine for BRAND NEW
tables because it's non-destructive (never alters or drops existing
tables), and keeps local setup to zero extra steps, which matters given
this must run without any Azure access. It is NOT sufficient on its own
for a column added to an ALREADY-EXISTING table's model (e.g.
ResumeVersion.visibility_json, added after `resume_versions` already
existed in every deployed database) - `create_all` sees the table is
already there and does nothing further, so the column never gets added
and every INSERT/SELECT against it fails with `UndefinedColumn`. See
_sync_added_columns below for the (Alembic-free) fix for that specific,
narrow case.
"""
import json
import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.auth.hashing import hash_password
from app.core.config import settings
from app.database.base import Base
from app.database.session import SessionLocal, engine
from app.models import AuditLog, Resume, ResumeVersion, Role, User  # noqa: F401 (registers models)

logger = logging.getLogger(__name__)

# Default recruiter-visibility preferences - see resume_service._DEFAULT_
# VISIBILITY (the single source of truth the app already reads at
# request time whenever visibility_json is NULL). Duplicated here ONLY as
# a literal value to backfill into existing rows' data, not as a second
# behavioral definition - resume_service's NULL-means-defaults handling
# is untouched and still what actually governs rendering.
_DEFAULT_VISIBILITY_JSON = json.dumps({
    "show_email": True, "show_phone": True, "show_linkedin": True,
    "show_address": True, "show_employment_dates": True,
})

# (table, column, DDL type) for every column added to an already-existing
# table after that table first shipped. Add an entry here, never a
# retroactive edit to an existing entry, whenever this happens again.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("resume_versions", "visibility_json", "TEXT"),
)


def _is_already_exists_error(exc: Exception) -> bool:
    """True only for the specific "already exists" race this function exists
    to tolerate. Deliberately a substring check on the message, not a blanket
    except-and-ignore: any OTHER IntegrityError/OperationalError/
    ProgrammingError (bad DATABASE_URL, missing database, no permission,
    a real constraint violation, a real schema conflict) must still crash
    startup loudly, not get silently swallowed alongside the one specific
    race we know about."""
    return "already exists" in str(exc).lower()


def _sync_added_columns() -> None:
    """Alembic-free, idempotent fix for the specific gap `create_all` leaves
    open: a column added to an already-existing table's model. For each
    (table, column, type) in _ADDED_COLUMNS, adds the column via a plain
    `ALTER TABLE ... ADD COLUMN` (dialect-portable - works against both
    Postgres and the SQLite used in tests) only if the table already
    exists AND the column is genuinely missing, then backfills existing
    NULL rows with the documented default so pre-existing records hold
    the real default value, not just an implicit one. A fresh database
    never hits this at all - create_all already created the column as
    part of the table the first time. Safe to run on every startup, and
    safe under concurrent workers (a DuplicateColumn race is treated the
    same as the create_all race above - the end state either worker
    wanted is already satisfied by whichever one won)."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table, column, ddl_type in _ADDED_COLUMNS:
        if table not in existing_tables:
            continue  # brand new DB - create_all already built this column in
        existing_columns = {c["name"] for c in inspector.get_columns(table)}
        if column in existing_columns:
            continue  # already synced (either always was, or a prior startup added it)

        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
        except (IntegrityError, OperationalError, ProgrammingError) as exc:
            if not _is_already_exists_error(exc):
                raise
            logger.info(
                "Column %s.%s already added by another worker/instance - continuing startup.",
                table, column,
            )
        else:
            logger.info("Schema sync: added missing column %s.%s (%s).", table, column, ddl_type)

        if column == "visibility_json":
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"UPDATE {table} SET {column} = :default WHERE {column} IS NULL"
                    ),
                    {"default": _DEFAULT_VISIBILITY_JSON},
                )
            logger.info(
                "Schema sync: backfilled default visibility preferences into existing %s rows.", table,
            )


def init_db() -> None:
    # `Base.metadata.create_all` is NOT atomic across processes: SQLAlchemy
    # implements `checkfirst=True` (the default) as "query the catalog for
    # this table, then CREATE TABLE if it wasn't found" - two separate
    # statements, not one. A production process manager runs multiple worker
    # processes (see Dockerfile's `gunicorn --workers 2`), and Cloud Run can
    # run multiple container instances - each independently runs this exact
    # startup path, concurrently, against the same database. Two workers can
    # both check "does `roles` exist?", both see "no" (neither has committed
    # a CREATE yet), and both issue CREATE TABLE. Verified live against a
    # real concurrent-worker startup against Postgres: the failure that
    # actually surfaces is NOT `ProgrammingError` (`relation already exists`,
    # SQLSTATE 42P07, DuplicateTable) as the SQLite-based reasoning here
    # originally assumed - Postgres's race instead landed on a
    # UniqueViolation ("duplicate key ... already exists") against the
    # internal pg_type catalog entry every CREATE TABLE implicitly creates,
    # which SQLAlchemy wraps as `IntegrityError`, not `ProgrammingError`.
    # Catching all three is what makes this actually correct on Postgres
    # rather than merely correct in theory. The end state either worker was
    # trying to reach (schema exists) is already satisfied by whichever one
    # won, so any of these three is safe to treat as success.
    try:
        Base.metadata.create_all(bind=engine)
    except (IntegrityError, OperationalError, ProgrammingError) as exc:
        if not _is_already_exists_error(exc):
            raise
        logger.info("Schema already created by another worker/instance - continuing startup.")

    _sync_added_columns()

    db = SessionLocal()
    try:
        try:
            _seed_roles(db)
            _seed_admin(db)
            db.commit()
        except IntegrityError:
            # Same race, one level down: the check-then-insert in
            # _seed_roles/_seed_admin can have two workers both see "no admin
            # role yet" and both try to insert it, and whichever commits
            # second hits the unique constraint. Same reasoning as above -
            # back off instead of crashing, the desired row already exists.
            db.rollback()
            logger.info("Roles/admin already seeded by another worker/instance - continuing startup.")
    finally:
        db.close()


def _seed_roles(db: Session) -> None:
    existing = {r.name for r in db.query(Role).all()}
    for name in (Role.ADMIN, Role.USER):
        if name not in existing:
            db.add(Role(name=name))
    db.flush()


def _seed_admin(db: Session) -> None:
    """Admin accounts are never created via public self-registration (that
    would let anyone grant themselves admin). Instead, exactly one admin is
    seeded from environment variables on first startup, if one doesn't
    already exist - a standard pattern for bootstrapping RBAC systems.
    """
    admin_role = db.query(Role).filter(Role.name == Role.ADMIN).first()
    existing_admin = (
        db.query(User).join(Role).filter(Role.name == Role.ADMIN).first()
    )
    if existing_admin:
        return

    if not settings.DEFAULT_ADMIN_EMAIL or not settings.DEFAULT_ADMIN_PASSWORD:
        return

    admin = User(
        email=settings.DEFAULT_ADMIN_EMAIL,
        hashed_password=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        full_name="System Administrator",
        role_id=admin_role.id,
        is_active=True,
    )
    db.add(admin)
