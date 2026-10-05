"""Database engine + session factory.

PostgreSQL only - no SQLite branch. `DATABASE_URL` selects which Postgres:
  - Local dev: the `db` service in docker-compose.yml (or any local Postgres
    reachable at that URL).
  - Production (GCP): a Neon PostgreSQL connection string - a fully
    external, serverless Postgres provider, not a GCP resource; plain
    TCP/TLS, no proxy/socket needed - see DEPLOYMENT_GCP.md §12:
    postgresql+psycopg://<user>:<password>@<your-neon-host>/<db>?sslmode=require
  - Production (Azure), if deploying there instead: an Azure SQL connection
    string, e.g.
    mssql+pyodbc://<user>:<password>@<server>.database.windows.net:1433/<db>?driver=ODBC+Driver+18+for+SQL+Server
    (requires the `pyodbc` package and the msodbcsql18 driver installed in
    the deployment environment).

Nothing here assumes which of those is active; SQLAlchemy's dialect is
entirely determined by the URL scheme.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Cloud Run can run many container instances concurrently, each holding its
# OWN pool - a pool sized for "one big server" would multiply out to far more
# connections than the database's own max_connections allows under load.
# pool_recycle guards against a managed Postgres provider (Neon, or Azure's
# equivalent) silently dropping a connection that's been idle too long -
# without it that shows up as random "server closed the connection
# unexpectedly" errors under low traffic.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """FastAPI dependency that yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
