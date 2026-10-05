#!/usr/bin/env bash
# One-time Secret Manager provisioning for the app's actual secret values -
# these are the values deploy.yml's `--set-secrets` flag resolves into
# Cloud Run env vars at container start. Never put these in GitHub secrets,
# a .env file that gets committed, or the workflow file itself.
#
# Run once, locally, already authenticated (`gcloud auth login`):
#
#   export GEMINI_API_KEY="<your real Gemini key>"
#   export DATABASE_URL="postgresql+psycopg://<user>:<password>@<your-neon-host>/<db>?sslmode=require"
#   bash scripts/setup-gcp-secrets.sh
#
# DATABASE_URL is a Neon PostgreSQL connection string (external to GCP, no
# managed proxy/socket involved) - get the exact string from the Neon
# console's connection details for your project/branch.
#
# JWT_SECRET_KEY and DEFAULT_ADMIN_PASSWORD are generated for you if not
# already exported - copy DEFAULT_ADMIN_PASSWORD from this script's own
# output, it is not stored anywhere else.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-developer-project-likhitha}"
gcloud config set project "$PROJECT_ID" >/dev/null

: "${GEMINI_API_KEY:?Set GEMINI_API_KEY before running this script}"
: "${DATABASE_URL:?Set DATABASE_URL before running this script (your Neon PostgreSQL connection string, postgresql+psycopg://...)}"

JWT_SECRET_KEY="${JWT_SECRET_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')}"
DEFAULT_ADMIN_PASSWORD="${DEFAULT_ADMIN_PASSWORD:-$(openssl rand -base64 18)}"

create_or_update_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=-
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=-
  fi
}

create_or_update_secret gemini-api-key "$GEMINI_API_KEY"
create_or_update_secret database-url "$DATABASE_URL"
create_or_update_secret jwt-secret-key "$JWT_SECRET_KEY"
create_or_update_secret default-admin-password "$DEFAULT_ADMIN_PASSWORD"

echo "================================================================"
echo "Secrets created/updated: gemini-api-key, database-url, jwt-secret-key, default-admin-password"
echo "Generated DEFAULT_ADMIN_PASSWORD (save this now, it is not printed again): $DEFAULT_ADMIN_PASSWORD"
echo "================================================================"
