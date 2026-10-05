"""FastAPI application factory / entrypoint.

Startup sequence: load any Key Vault-backed secrets, then initialize the
database (create tables + seed roles/admin), then start serving. Nothing in
this sequence requires Azure - both steps are no-ops/local-only unless the
corresponding AZURE_* / DATABASE_URL settings point at real Azure resources.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.keyvault import load_secrets_from_keyvault
from app.core.limiter import limiter
from app.database.init_db import init_db
from app.routers import admin, auth, resumes
from app.storage.factory import get_storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_secrets_from_keyvault(settings)
    init_db()

    # Fail fast at startup instead of on a user's first upload request:
    # get_storage() is normally resolved lazily (FastAPI Depends, only hit
    # when an endpoint using it is called) - for "local" it never raises, but
    # for "gcs"/"azure" a missing bucket/connection-string env var raises
    # RuntimeError. Calling it once here surfaces that misconfiguration
    # immediately in the deploy's own startup logs.
    if settings.STORAGE_BACKEND != "local":
        get_storage()

    # No secrets logged here - GCS_BUCKET_NAME and the CORS origin list are
    # both non-sensitive identifiers (a bucket name, public frontend URLs),
    # unlike DATABASE_URL/JWT_SECRET_KEY/GEMINI_API_KEY which stay out of logs
    # entirely (only DATABASE_URL's scheme is logged, never the credentials).
    logger.info(
        "Startup complete. Storage backend=%s, GCS bucket=%s, database=%s, CORS origins=%s",
        settings.STORAGE_BACKEND,
        settings.GCS_BUCKET_NAME or "(unset)",
        settings.DATABASE_URL.split("://")[0],
        settings.allowed_origins_list,
    )
    yield


app = FastAPI(
    title="AI Resume Management System API",
    version="3.0.0",
    description=(
        "Authenticated, role-based resume generation and management system. "
        "Generates ATS-friendly resumes (PDF + DOCX) via Gemini, versions every "
        "generation, and stores files in Blob Storage with metadata in a SQL database."
    ),
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def add_security_headers(request, call_next):
    # Cloud Run terminates TLS at its own load balancer, so the app never
    # needs to redirect http->https itself - but the response headers below
    # are the app's own responsibility regardless of what's in front of it.
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

# Wide-open CORS + credentials is a known-bad combination for a public API -
# this reads an explicit allowlist from ALLOWED_ORIGINS. Credentials are
# enabled (unlike the previous anonymous-only version) because the frontend
# needs to send the Authorization header on cross-origin requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(resumes.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Used by Azure App Service / docker-compose health checks."""
    return {"status": "ok"}


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "ai-resume-management-backend", "docs": "/docs"}
