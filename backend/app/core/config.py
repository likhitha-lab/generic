"""Application configuration, loaded from environment variables (.env in
local dev). If AZURE_KEY_VAULT_URL is set, secrets are additionally pulled
from Key Vault at startup and override the corresponding fields here - see
core/keyvault.py. Nothing in this file requires Azure to run locally.
"""
import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # --- Gemini ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    # The resume converter now asks Gemini to preserve EVERY job/bullet/skill
    # from the source instead of capping/summarizing (see prompts.py) - a
    # long, detailed multi-page resume can need a lot of output tokens to do
    # that losslessly. Left at the SDK's own default, this silently truncates
    # (and is exactly what caused "some experience missing" - see
    # gemini_client.py's finish_reason check for how truncation is now
    # detected explicitly instead of surfacing as a vague JSON-parse error).
    GEMINI_MAX_OUTPUT_TOKENS: int = 8192

    # --- CORS - comma separated list of allowed frontend origins ---
    ALLOWED_ORIGINS: str = "http://localhost:5173"

    # --- Rate limiting, e.g. "10/minute" ---
    RATE_LIMIT: str = "10/minute"
    AUTH_RATE_LIMIT: str = "5/minute"  # stricter, to slow down credential stuffing

    # --- Branding asset used in generated PDF/DOCX files ---
    LOGO_PATH: str = str(BASE_DIR / "static" / "logo.png")

    # --- Database ---
    # PostgreSQL everywhere, local and production - no SQLite fallback.
    # SQLite was dropped entirely: Cloud Run runs multiple gunicorn workers
    # per instance and can run multiple instances concurrently, and SQLite
    # has neither a real concurrent-writer story (file-level locking) nor
    # persistent storage (Cloud Run's filesystem is ephemeral, wiped on every
    # cold start/instance recycle) - see DEPLOYMENT_GCP.md.
    #
    # Local dev default (below): the `db` service in docker-compose.yml -
    # `docker compose up` gives you a real local Postgres with zero manual
    # install. Running the backend outside Docker locally instead needs a
    # real Postgres reachable at that URL (a local install, or point this at
    # the same docker-compose `db` service, exposed on the HOST at
    # localhost:5433 - not Postgres's usual 5432, to avoid clashing with any
    # other local Postgres you might already have running).
    #
    # Neon PostgreSQL (production - a fully external, serverless Postgres
    # provider, not a GCP resource; plain TCP/TLS, no proxy/socket needed -
    # see DEPLOYMENT_GCP.md §12):
    #   postgresql+psycopg://<user>:<password>@<your-neon-host>/<db>?sslmode=require
    # Azure SQL (if deploying there instead of GCP):
    # mssql+pyodbc://user:pass@server.database.windows.net:1433/db?driver=ODBC+Driver+18+for+SQL+Server
    DATABASE_URL: str = "postgresql+psycopg://resumebuilder:resumebuilder@localhost:5433/resumebuilder"
    # Per-instance pool size. Cloud Run can run many container instances
    # concurrently, each with its OWN pool - keep these small (the database's
    # own max_connections limit is shared across every instance) rather than
    # sized for a single fixed server.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 2
    # Recycle connections periodically so a connection that's gone stale
    # (a managed Postgres provider can silently drop idle ones) is never
    # handed back out - avoids sporadic "server closed the connection" errors.
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # --- JWT auth ---
    JWT_SECRET_KEY: str = "dev-only-insecure-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Seeded admin account (created once, on first startup, if no admin
    # exists yet - see database/init_db.py). Leave blank to skip seeding. ---
    DEFAULT_ADMIN_EMAIL: str = "admin@example.com"
    DEFAULT_ADMIN_PASSWORD: str = "ChangeMe123!"

    # --- File storage ---
    # "local" (default): filesystem under backend/local_data/blobs - no cloud
    # account needed. "gcs": Google Cloud Storage (requires GCS_BUCKET_NAME;
    # auth is via Application Default Credentials - the Cloud Run service's
    # own service account, no key file needed there). "azure": Azure Blob
    # Storage (requires the two AZURE_STORAGE_* vars below).
    STORAGE_BACKEND: str = "local"
    LOCAL_STORAGE_DIR: str = str(BASE_DIR / "local_data" / "blobs")
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_CONTAINER: str = "resumes"

    # --- Google Cloud Storage (only used when STORAGE_BACKEND=gcs) ---
    GCS_BUCKET_NAME: str = ""
    # Set only for local dev against a real bucket (path to a downloaded
    # service-account key). On Cloud Run, leave this blank - Application
    # Default Credentials picks up the service account Cloud Run injects
    # automatically; baking a key file into the image would be a secret
    # leaked into every layer of it.
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_CLOUD_PROJECT: str = ""
    # How long a generate_signed_url() link stays valid for.
    SIGNED_URL_EXPIRATION_MINUTES: int = 15

    # --- Azure Key Vault (optional) ---
    # If set, core/keyvault.py fetches secrets from this vault at startup
    # using DefaultAzureCredential (managed identity in Azure, `az login` for
    # local testing against a real vault) and overrides the matching fields
    # above. Leave blank to use plain environment variables / .env instead.
    AZURE_KEY_VAULT_URL: str = ""

    # --- Application Insights (optional) ---
    APPLICATIONINSIGHTS_CONNECTION_STRING: str = ""

    # --- Public URLs (used for CORS and in log/startup messages only - never
    # read by application logic, so leaving them blank never breaks anything) ---
    FRONTEND_URL: str = ""
    BACKEND_URL: str = ""

    # --- Resume upload/convert pipeline debug logging (off by default) ---
    # When true, create_uploaded_resume/regenerate_resume log the raw
    # extracted text, the full Gemini prompt, Gemini's raw response, and the
    # final normalized JSON for every upload/convert - for diagnosing
    # extraction/parsing bugs against a real problem resume without
    # guessing. Verbose and logs resume content, so it's opt-in, not
    # always-on.
    DEBUG_RESUME_PIPELINE: bool = False

    # Absolute path, not ".env" - a relative path resolves against the process's
    # CWD, not this file's location, so it silently fails to load (falling back
    # to insecure defaults, e.g. JWT_SECRET_KEY) whenever the app is launched
    # from anywhere other than backend/ (IDE run buttons, --app-dir, etc.).
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def allowed_origins_list(self) -> list[str]:
        raw = self.ALLOWED_ORIGINS.strip()
        # Accept a JSON array too (e.g. '["https://a.example","https://b.example"]') -
        # some deploy tooling/consoles serialize a list env var that way instead of
        # a plain comma-separated string. Comma-separated remains the primary,
        # documented format; JSON is an additional accepted shape, not a
        # replacement for it.
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
