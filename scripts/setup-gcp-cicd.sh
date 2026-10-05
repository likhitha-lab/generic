#!/usr/bin/env bash
# One-time GCP setup for the GitHub Actions -> Cloud Run pipeline
# (.github/workflows/deploy.yml). Run this ONCE, yourself, locally, already
# authenticated as a user with Owner/Editor (or the specific IAM-admin +
# service-usage-admin roles) on the target project:
#
#   gcloud auth login
#   gcloud config set project developer-project-likhitha
#   bash scripts/setup-gcp-cicd.sh
#
# Auth method: this pipeline authenticates to GCP using a service-account
# JSON key stored as the GCP_CREDENTIALS GitHub secret (not Workload
# Identity Federation) - see CICD_SETUP.md for how the key itself is
# generated and added to GitHub. This script does NOT generate that key -
# key material is deliberately a manual, explicit step (see the final
# printed instructions), never something a script silently produces and
# leaves lying around on disk or in shell history.
#
# This script provisions/validates: required APIs, the Artifact Registry
# repo, the two service accounts (deploy + runtime), and their IAM role
# bindings - all of which are needed regardless of auth method. It does
# NOT create the GCS bucket or the Cloud Run services themselves - the
# bucket is created per DEPLOYMENT_GCP.md's §11, and the Cloud Run services
# are created by the first `gcloud run deploy` (see deploy.yml). The
# database is Neon PostgreSQL (external, not GCP-hosted) - its connection
# string is a Secret Manager entry (see scripts/setup-gcp-secrets.sh), not
# anything this script provisions. Safe to re-run - every command either
# uses `--quiet` or tolerates "already exists".
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-developer-project-likhitha}"
REGION="${REGION:-asia-south1}"
REPOSITORY="${REPOSITORY:-resume-builder}"

DEPLOY_SA_NAME="github-deployer"
RUNTIME_SA_NAME="resume-builder-runtime"

echo "== Project: $PROJECT_ID | Region: $REGION | Repo: $REPOSITORY =="

gcloud config set project "$PROJECT_ID" >/dev/null

echo "-- Enabling required APIs --"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com

echo "-- Validating required APIs are enabled --"
for api in run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com; do
  if gcloud services list --enabled --filter="config.name:${api}" --format='value(config.name)' | grep -q "$api"; then
    echo "   OK: $api enabled"
  else
    echo "   FAILED: $api is NOT enabled" >&2
    exit 1
  fi
done

echo "-- Artifact Registry repository ($REPOSITORY) --"
gcloud artifacts repositories describe "$REPOSITORY" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPOSITORY" \
    --repository-format=docker \
    --location="$REGION" \
    --description="ResumeBuilder backend + frontend images"

echo "-- Deploy service account (what GitHub Actions runs 'gcloud run deploy' as) --"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$DEPLOY_SA_NAME" \
    --display-name="GitHub Actions Cloud Run deployer"

echo "-- Runtime service account (what the deployed backend Cloud Run service runs as) --"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
    --display-name="ResumeBuilder Cloud Run runtime"

echo "-- Validating both service accounts exist --"
for sa in "$DEPLOY_SA" "$RUNTIME_SA"; do
  if gcloud iam service-accounts describe "$sa" >/dev/null 2>&1; then
    echo "   OK: $sa exists"
  else
    echo "   FAILED: $sa does not exist" >&2
    exit 1
  fi
done

echo "-- IAM roles: deploy SA --"
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" --role="$role" --condition=None >/dev/null
done

echo "-- IAM roles: runtime SA --"
for role in roles/storage.objectAdmin roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" --role="$role" --condition=None >/dev/null
done

echo
echo "================================================================"
echo "Done. APIs, Artifact Registry repo, both service accounts, and IAM"
echo "roles are provisioned/validated."
echo
echo "Remaining manual step - generate the deploy SA's JSON key yourself"
echo "(deliberately not automated by this script):"
echo
echo "  gcloud iam service-accounts keys create github-deployer-key.json \\"
echo "    --iam-account=${DEPLOY_SA}"
echo
echo "Then add its full file contents as the GitHub repository secret"
echo "GCP_CREDENTIALS (Settings -> Secrets and variables -> Actions -> New"
echo "repository secret), and delete github-deployer-key.json from local"
echo "disk immediately after (it is already covered by .gitignore's *.json"
echo "rule, but don't rely on that - delete it). See CICD_SETUP.md for the"
echo "full walkthrough."
echo
echo "Still needed before deploy.yml can succeed (not created by this script):"
echo "  - GCS_BUCKET_NAME secret (GCS bucket 'resume_bucket08', see DEPLOYMENT_GCP.md §11)"
echo "  - Secret Manager entries: gemini-api-key, jwt-secret-key, database-url (your Neon"
echo "    PostgreSQL connection string), default-admin-password"
echo "    (see CICD_SETUP.md, or scripts/setup-gcp-secrets.sh)"
echo "================================================================"
