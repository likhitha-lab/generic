# Deployment Guide — Google Cloud Run

**Nothing in this project has been deployed to GCP.** This is the runbook for when a real GCP project and billing account are available. Everything through "Build & verify Docker images locally" has already been done and verified as part of writing this guide — the rest requires real GCP access.

An Azure App Service / Static Web Apps path also still exists in this repo (`DEPLOYMENT.md`, `infra/`, `.github/workflows/backend-cicd.yml`, `.github/workflows/frontend-cicd.yml`) — this guide is additive, not a replacement. Nothing Azure-specific was removed.

---

## 1. Project analysis — what already existed vs. what was missing

| Item | Found | State |
|---|---|---|
| `backend/requirements.txt` | Yes | **Broken** — file was truncated mid-line, missing `azure-storage-blob`/`azure-identity` entirely despite `storage/azure_blob.py` and `core/keyvault.py` importing them. Fixed, plus GCP deps added. |
| `docker-compose.yml` | Yes | **Broken** — truncated mid-value (`VITE_API_URL: http://localhost:8`). Fixed. |
| `frontend/.env.example` | Yes | **Broken** — truncated mid-sentence. Fixed. |
| `backend/Dockerfile` | **Missing** | `docker-compose.yml` referenced `build: context: ./backend` with no Dockerfile present — would have failed on `docker compose up --build`. Created. |
| `frontend/Dockerfile` | **Missing** | Same issue for `./frontend`. Created. |
| `frontend/nginx.conf` | Yes | Static, hardcoded `listen 8080` — fine as a default but not Cloud-Run-`$PORT`-aware. Replaced with `nginx.conf.template` (envsubst-rendered at container start). |
| `cloudbuild.yaml`, `Procfile` | Not found | N/A — this guide uses `docker build` + `gcloud run deploy` directly instead (see §9), which needs neither. |
| Storage config | `app/storage/` (local + Azure Blob) | GCS backend added (`app/storage/gcs.py`), wired into `factory.py` via `STORAGE_BACKEND=gcs`. |
| Database config | `app/database/session.py` | SQLite removed entirely - PostgreSQL only, local and production, via `docker-compose.yml`'s `db` service locally or Neon PostgreSQL in production (connection pooling tuned for Cloud Run — see §5). |
| CORS config | `app/main.py` | Already environment-driven (`ALLOWED_ORIGINS`, comma-separated) — no code change needed, just set it correctly in production (see §7). |
| Gunicorn | listed in `requirements.txt`, referenced in a comment | Never actually used anywhere — no Dockerfile/Procfile invoked it. Now the backend Dockerfile's `CMD`. |
| Uvicorn | used for local dev (`uvicorn app.main:app --reload`) | Unchanged for local dev; production uses gunicorn managing uvicorn workers (see §2). |

## 2. Backend — what was implemented

- **`backend/Dockerfile`** (new): multi-stage, non-root user, `$PORT`-aware `CMD`, gunicorn + `UvicornWorker`. Built and run-tested locally (see §"Build & verify" below) — caught and fixed two real bugs in the process:
  1. The non-root user's home directory didn't match where `pip install --user` packages were copied to, so `gunicorn` wasn't importable at runtime.
  2. `app/database/init_db.py`'s role/admin seeding used check-then-insert with no race protection — with 2 gunicorn workers both running the FastAPI startup lifespan concurrently, one worker's `INSERT` lost a race and crashed with `IntegrityError`. Fixed by catching `IntegrityError` and rolling back (idempotent — whichever worker wins the race leaves the DB in the desired state).
- **Health endpoint**: already existed (`GET /health`) — used directly as the Cloud Run health signal, no change needed.
- **`PORT` env var**: gunicorn `CMD` binds to `0.0.0.0:${PORT:-8080}` — Cloud Run always sets `PORT`; the `:-8080` fallback just makes `docker run` work without it set.
- **Logging**: `app/main.py` already calls `logging.basicConfig(level=logging.INFO)`; gunicorn's `--access-logfile - --error-logfile -` sends both to stdout/stderr, which is exactly what Cloud Run's Logging integration scrapes automatically — no separate log shipping needed.
- **Graceful startup**: `lifespan()` already runs `init_db()` before serving; now race-safe under multiple workers/instances (see bug #2 above).
- **Security headers**: added a small middleware (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security`) — none existed before.

## 3. Frontend — what was implemented

- **`frontend/Dockerfile`** (new): multi-stage (Node build → nginx serve). `VITE_API_URL` is a build `ARG` (Vite inlines `VITE_`-prefixed vars at build time, not read at container runtime — this is why it can't be a normal Cloud Run env var).
- **`frontend/nginx.conf.template`** (replaces the old static `nginx.conf`): uses nginx's built-in template-rendering entrypoint (`envsubst` over `*.template` files) so the container listens on whatever `$PORT` Cloud Run injects, not a value baked in at build time. `NGINX_ENVSUBST_FILTER='^PORT$'` is set so envsubst only touches `$PORT` and leaves nginx's own `$uri`/`$host`-style variables alone (an easy and otherwise-silent way to corrupt an nginx config).
- Build optimization: multi-stage means the ~200MB+ of `node_modules` and the Node toolchain never end up in the shipped image — only `dist/` (the built static assets) and nginx itself.
- Verified: built the image, ran it with a **non-default** `PORT=9090` to prove the templating actually works (not just coincidentally matching nginx's old hardcoded 8080), got `HTTP 200` serving the real built `index.html`.

## 4. Google Cloud Storage

`app/storage/` already used a clean `BlobStorage` ABC (`save`/`read`/`delete`) with `local` and `azure` implementations selected by `STORAGE_BACKEND` — no application code outside `storage/` ever touches a backend directly. Added a third implementation:

- **`app/storage/gcs.py`** (new): implements the same interface using `google-cloud-storage`. On Cloud Run, needs zero explicit credentials — Application Default Credentials resolve to the Cloud Run service's own runtime service account automatically via the metadata server. Locally, either run `gcloud auth application-default login` once, or set `GOOGLE_APPLICATION_CREDENTIALS` to a downloaded key file path (read explicitly in code, since a path set only in a local `.env` — parsed by `pydantic-settings`, not exported to the OS environment — would otherwise be invisible to the SDK's own env-var lookup).
- **`app/storage/factory.py`**: added a `STORAGE_BACKEND=gcs` branch requiring `GCS_BUCKET_NAME`.
- Signed URLs: not implemented — every resume file currently flows through the backend as base64-encoded bytes in JSON responses (see `file_generator.py`'s module docstring), never a direct client→bucket link, so there's no code path that needs one today. If you later want the frontend to download directly from GCS instead of via the API, `blob.generate_signed_url(...)` is the addition point in `gcs.py` — not added speculatively here.

## 5. Database

**SQLite has been removed entirely** - PostgreSQL is now the only supported database, local and production alike. It was originally the local-dev default (zero setup, zero cloud dependency) with Postgres as a production option, but that split was retired because SQLite was **unsuitable for Cloud Run specifically**, for three reasons that a later production incident on this exact app confirmed weren't hypothetical:

1. **No persistent disk.** Cloud Run's container filesystem is ephemeral and wiped on every new revision, scale-to-zero cold start, or instance replacement. A SQLite file written to `/app/local_data/app.db` vanishes the moment that instance is recycled — every user/resume would be gone at random.
2. **No shared state across instances.** Cloud Run can (and, under any real load, will) run multiple container instances simultaneously. Each would have its own independent SQLite file — user A's request hitting instance 1 and user B's hitting instance 2 would see two different databases with no relationship to each other.
3. **No concurrent-writer story.** SQLite's file-level locking isn't designed for multiple processes across multiple machines writing at once, which is exactly Cloud Run's normal operating mode (not an edge case) - this is what actually caused a production `sqlite3.OperationalError: table roles already exists` crash loop: 2 gunicorn workers both ran `init_db()`'s `Base.metadata.create_all()` concurrently at startup, both saw the schema didn't exist yet, and whichever committed second crashed.

Local dev now runs a real Postgres via `docker-compose.yml`'s `db` service (`docker compose up` - zero manual install, matches production's engine exactly instead of a different one with different locking/dialect behavior). `app/database/session.py` no longer branches on dialect at all - one unconditional `create_engine(...)` with explicit pool sizing:

```python
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,        # default 5
    max_overflow=settings.DB_MAX_OVERFLOW,  # default 2
    pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,  # default 1800
)
```

Why small pools specifically: Cloud Run can run up to `--max-instances` container instances concurrently, **each with its own independent connection pool** — this isn't one shared pool. A pool sized for "one big always-on server" (e.g. 20+ connections) multiplied across 10 instances would blow past the database's own connection limit under load. `pool_recycle` guards against a managed Postgres provider silently dropping a connection that's been idle too long, which otherwise shows up as sporadic, hard-to-reproduce "server closed the connection unexpectedly" errors.

**Connection string** (Neon PostgreSQL — a fully external, serverless Postgres provider, not a GCP resource; no proxy socket or GCP-side networking involved, just a normal TCP connection over TLS). Get the exact string from the Neon console's connection details for your project/branch:

```
postgresql+psycopg://<user>:<password>@<your-neon-host>/<db>?sslmode=require
```

`psycopg[binary]` (psycopg 3, precompiled wheel) was added to `requirements.txt` for this — no separate native driver install needed in the image.

**Concurrent-startup idempotency, verified against real Postgres** (`app/database/init_db.py`): with `gunicorn --workers 2`, both workers run `init_db()` concurrently, and `Base.metadata.create_all()`'s check-then-create isn't atomic across processes. `init_db()` catches this and continues rather than crashing - the exception type that actually surfaces on Postgres, confirmed by reproducing the race live in `docker compose`, is `IntegrityError` (a `UniqueViolation` on the internal `pg_type` catalog entry every `CREATE TABLE` implicitly creates), **not** `ProgrammingError`/`DuplicateTable` as the SQLite-based reasoning this code started from assumed. All three (`IntegrityError`, `OperationalError`, `ProgrammingError`) are caught and filtered to only swallow messages containing "already exists" - anything else still crashes startup loudly.

## 6. Environment variables

See `backend/.env.example` and `frontend/.env.example` (both fixed from truncation and extended). Full backend list:

```
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
ALLOWED_ORIGINS=
FRONTEND_URL=
BACKEND_URL=
RATE_LIMIT=10/minute
AUTH_RATE_LIMIT=5/minute
DATABASE_URL=
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=2
DB_POOL_RECYCLE_SECONDS=1800
JWT_SECRET_KEY=
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7
DEFAULT_ADMIN_EMAIL=
DEFAULT_ADMIN_PASSWORD=
STORAGE_BACKEND=gcs
LOCAL_STORAGE_DIR=./local_data/blobs
GCS_BUCKET_NAME=
GOOGLE_CLOUD_PROJECT=
GOOGLE_APPLICATION_CREDENTIALS=
```

Frontend: `VITE_API_URL` only (see §3 — it's a build arg, not a runtime var, on Cloud Run).

## 7. CORS

No code change was needed — `app/main.py` already reads an explicit comma-separated allowlist:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,  # from ALLOWED_ORIGINS env var
    allow_credentials=True,
    ...
)
```

Just set it correctly in production:

```
ALLOWED_ORIGINS=https://resumebuilder-frontend-xxxxx-uc.a.run.app
```

Never `*` — `allow_credentials=True` combined with a wildcard origin is rejected by browsers anyway (and would be a real vulnerability if it weren't). If you add a custom domain later, add it to this comma-separated list, not by loosening the wildcard.

## 8. Docker

Both Dockerfiles were built and run-tested locally against a real running container (not just reviewed) — see the bugs caught and fixed in §2/§3. Multi-stage in both: backend copies only the installed Python packages + app code into a slim runtime stage; frontend copies only the built `dist/` output into an nginx image, never `node_modules` or the Node toolchain.

---

## 9. One-time GCP project setup

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

export PROJECT_ID="YOUR_PROJECT_ID"
export REGION="us-central1"
export REPO="resumebuilder"

# APIs this deployment needs
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com

# Artifact Registry repo for both images
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="ResumeBuilder backend + frontend images"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
```

### Runtime service account (what the deployed Cloud Run service runs as)

```bash
gcloud iam service-accounts create resumebuilder-runtime \
  --display-name="ResumeBuilder Cloud Run runtime"

export RUNTIME_SA="resumebuilder-runtime@${PROJECT_ID}.iam.gserviceaccount.com"

# Read/write its own GCS bucket
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/storage.objectAdmin"

# No database-specific IAM role needed here - the database is Neon
# PostgreSQL, external to GCP, reached over its own connection string
# (from Secret Manager below) rather than a GCP-managed proxy.

# Read the secrets referenced by --set-secrets at deploy time
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor"
```

### Deploy service account (what GitHub Actions / your shell runs `gcloud run deploy` as)

```bash
gcloud iam service-accounts create resumebuilder-deployer \
  --display-name="ResumeBuilder CI/CD deployer"

export DEPLOY_SA="resumebuilder-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}" --role="roles/run.admin"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}" --role="roles/iam.serviceAccountUser"
```

## 10. IAM roles — what and why

| Role | Bound to | Why |
|---|---|---|
| `roles/run.admin` | Deploy SA | Create/update Cloud Run services and revisions (`gcloud run deploy`). |
| `roles/artifactregistry.writer` | Deploy SA | Push built Docker images to the Artifact Registry repo. |
| `roles/iam.serviceAccountUser` | Deploy SA | Deploying a Cloud Run revision that *runs as* another service account (the runtime SA) requires this on the deploy SA — otherwise deploy fails with a permission error impersonating the runtime identity. |
| `roles/storage.objectAdmin` | Runtime SA | The app itself reads/writes/deletes resume PDFs/DOCX/JSON in the GCS bucket at request time. |
| `roles/secretmanager.secretAccessor` | Runtime SA | Lets Cloud Run resolve `--set-secrets` (JWT secret, Gemini key, DB URL) into env vars at container start, without them ever appearing in `gcloud run deploy` output or Cloud Run's own config as plaintext. |
| `roles/cloudbuild.builds.builder` (only if you use `gcloud builds submit` instead of local `docker build`) | Deploy SA / Cloud Build SA | Not used by the workflows in this repo (they `docker build` directly), but listed since the task asked for it — needed only if you switch to Cloud Build-triggered builds. |

Do **not** grant `roles/owner` or `roles/editor` to either service account — both of the roles above are scoped to exactly what each identity needs and nothing else.

## 11. GCS bucket

```bash
export BUCKET_NAME="${PROJECT_ID}-resumebuilder"

gcloud storage buckets create "gs://${BUCKET_NAME}" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --uniform-bucket-level-access \
  --public-access-prevention

# Grant the runtime SA object-level access on just this bucket (already
# granted project-wide in §9 - this is the narrower, bucket-scoped
# equivalent if you'd rather not grant it at the project level)
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/storage.objectAdmin"
```

`--uniform-bucket-level-access` + `--public-access-prevention`: every object in the bucket inherits the bucket's IAM policy (no legacy per-object ACLs to accidentally leave public), and the bucket can never be made public even by a future misconfigured IAM binding — appropriate given every object here is a user's personal resume.

## 12. Database (Neon PostgreSQL)

The database is **Neon PostgreSQL** — a fully external, serverless
Postgres provider, not a GCP resource. Nothing to provision with `gcloud`
here:

1. Create a project (and, optionally, a branch) in the
   [Neon console](https://console.neon.tech).
2. Copy the connection string it gives you — a normal
   `postgresql://user:password@host/db?sslmode=require` URL, reachable
   over plain TCP/TLS from anywhere, including Cloud Run, with no proxy,
   socket, or extra networking setup required.
3. Rewrite it for this app's driver (`psycopg` 3, not the default
   `psycopg2` most tools assume) and keep it for §13:
   ```
   postgresql+psycopg://<user>:<password>@<your-neon-host>/<db>?sslmode=require
   ```

## 13. Secrets

```bash
export JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
export DEFAULT_ADMIN_PASSWORD=$(openssl rand -base64 18)
export DATABASE_URL="postgresql+psycopg://<user>:<password>@<your-neon-host>/<db>?sslmode=require"  # from §12

printf '%s' "$JWT_SECRET_KEY"          | gcloud secrets create jwt-secret-key --data-file=-
printf '%s' "<your Gemini API key>"    | gcloud secrets create gemini-api-key --data-file=-
printf '%s' "$DATABASE_URL"            | gcloud secrets create database-url --data-file=-
printf '%s' "$DEFAULT_ADMIN_PASSWORD"  | gcloud secrets create default-admin-password --data-file=-
```

## 14. Build & deploy

```bash
# --- Backend ---
cd backend
docker build -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:v1" .
docker push "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:v1"

gcloud run deploy resumebuilder-backend \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:v1" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --concurrency 40 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 10 \
  --service-account "$RUNTIME_SA" \
  --set-env-vars "GEMINI_MODEL=gemini-2.5-flash,STORAGE_BACKEND=gcs,GCS_BUCKET_NAME=${BUCKET_NAME},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,JWT_SECRET_KEY=jwt-secret-key:latest,DATABASE_URL=database-url:latest,DEFAULT_ADMIN_PASSWORD=default-admin-password:latest"

export BACKEND_URL=$(gcloud run services describe resumebuilder-backend --region "$REGION" --format='value(status.url)')

# --- Frontend (needs BACKEND_URL baked in at build time - see §3) ---
cd ../frontend
docker build --build-arg VITE_API_URL="$BACKEND_URL" \
  -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/frontend:v1" .
docker push "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/frontend:v1"

gcloud run deploy resumebuilder-frontend \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/frontend:v1" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 256Mi \
  --cpu 1 \
  --concurrency 80 \
  --timeout 60 \
  --min-instances 0 \
  --max-instances 5

export FRONTEND_URL=$(gcloud run services describe resumebuilder-frontend --region "$REGION" --format='value(status.url)')

# --- Now that FRONTEND_URL is known, lock down CORS to it and redeploy backend ---
gcloud run services update resumebuilder-backend --region "$REGION" \
  --update-env-vars "ALLOWED_ORIGINS=${FRONTEND_URL},FRONTEND_URL=${FRONTEND_URL},BACKEND_URL=${BACKEND_URL}"
```

**Why the two-pass CORS update**: the frontend's URL isn't known until after its first deploy (Cloud Run assigns it), but the frontend image needs the backend's URL baked in at *its* build time. Chicken-and-egg — deploy backend first with permissive-enough CORS to not block yourself, deploy frontend once backend's URL is known, then tighten CORS to the frontend's now-known URL and redeploy the backend (env-var-only update, no image rebuild needed).

## 15. Deployment verification

```bash
# Health
curl -sf "${BACKEND_URL}/health"

# Register + login (auth)
curl -s -X POST "${BACKEND_URL}/api/auth/register" -H "Content-Type: application/json" \
  -d '{"email":"smoketest@example.com","password":"Passw0rd!23","full_name":"Smoke Test"}'
TOKEN=$(curl -s -X POST "${BACKEND_URL}/api/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"smoketest@example.com","password":"Passw0rd!23"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -sf "${BACKEND_URL}/api/auth/me" -H "Authorization: Bearer ${TOKEN}"

# Manual resume generation (exercises Gemini + PDF/DOCX generation + GCS upload)
curl -s -X POST "${BACKEND_URL}/api/generate" -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name":"Smoke Test","email":"smoketest@example.com","summary":["Backend engineer."],"skills":["Python"]}' \
  -o /tmp/generate_response.json
cat /tmp/generate_response.json | python3 -m json.tool | head -20

# Resume conversion (upload -> extraction -> Gemini -> PDF/DOCX -> GCS)
curl -s -X POST "${BACKEND_URL}/api/convert" -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@/path/to/a/real/resume.pdf" -o /tmp/convert_response.json

# Confirm files actually landed in the bucket
gcloud storage ls "gs://${BUCKET_NAME}/**" | head -20

# Frontend reachable and pointed at the right backend
curl -sf "${FRONTEND_URL}/" | grep -o '<title>[^<]*' 
```

If `/api/generate` or `/api/convert` fail specifically (health/auth pass), check `gcloud run services logs read resumebuilder-backend --region "$REGION" --limit 50` — the two most likely first-deploy failures are `GEMINI_API_KEY` not resolving (secret binding typo) or an invalid/expired Neon connection string in `DATABASE_URL`.

## 16. CI/CD

`.github/workflows/deploy.yml` (alongside the existing Azure-targeted workflows) — test → build → push to Artifact Registry → deploy to Cloud Run, on every push to `main`, for both backend and frontend in one pipeline. Auth uses a **service-account JSON key** (`google-github-actions/auth`'s `credentials_json` input), stored as the `GCP_CREDENTIALS` GitHub repository secret.

Full one-time setup (service accounts, IAM roles, JSON key generation, all required GitHub secrets) is in **`CICD_SETUP.md`** — that document is the source of truth for CI/CD setup; this section is kept only as a pointer so §18's checklist has somewhere to link to.

Required GitHub repository secrets: `GCP_CREDENTIALS`, `GCP_RUNTIME_SERVICE_ACCOUNT`, `GCS_BUCKET_NAME`. `GCP_CREDENTIALS` is a real credential — guard it accordingly; the other two are resource identifiers, not secret values. The actual application secrets (JWT key, Gemini key, DB connection string) live in Secret Manager (§13), referenced by name, never duplicated into GitHub.

## 17. Security checklist

- [x] No secrets in the repository — `.env` is git-ignored (`backend/.gitignore`), `.env.example` files contain only placeholder values.
- [x] No hardcoded credentials in code — grepped for API keys/passwords, found none; `JWT_SECRET_KEY` has an insecure *default* (`dev-only-insecure-secret-change-me`) but it's a fallback for local dev only, never used unless the real env var is unset, and is documented as such.
- [x] JWT: `HS256`, secret loaded from Secret Manager in production, access tokens short-lived (60 min default), separate longer-lived refresh tokens, `sub` claim is a numeric user ID (not email — can't be guessed/enumerated as easily).
- [x] CORS: explicit allowlist, no wildcard, credentials-aware (§7).
- [x] Secure headers: added in this pass (§2) — `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security`.
- [x] Non-root container user (backend) — verified via `docker exec ... whoami` → `app`, not `root`.
- [x] Least-privilege IAM — two separate service accounts (deploy vs. runtime), each with only the roles listed in §10, no `Owner`/`Editor`.
- [x] Bucket hardening — uniform bucket-level access + public access prevention (§11).
- [x] Rate limiting on auth endpoints already existed (`slowapi`, `AUTH_RATE_LIMIT=5/minute`) — unchanged, still correct.
- [ ] **Not done, worth doing before real users hit this**: Alembic migrations instead of `Base.metadata.create_all` (documented as a known gap in `init_db.py`'s own docstring, unrelated to this GCP migration — flagging, not fixing here since it's out of scope for "deploy what exists").
- [ ] **Not done**: Cloud Armor / WAF in front of Cloud Run, if this is ever exposed beyond a trusted user base — Cloud Run's `--allow-unauthenticated` alone has no bot/abuse protection beyond the app's own rate limiter.

## 18. Final checklist — fresh GCP project to working deployment

1. [ ] `gcloud auth login`, `gcloud config set project`, enable APIs (§9).
2. [ ] Create Artifact Registry repo, configure `docker` auth (§9).
3. [ ] Create runtime + deploy service accounts, bind IAM roles (§9, §10).
4. [ ] Create GCS bucket (§11).
5. [ ] Create a Neon PostgreSQL project and get its connection string (§12).
6. [ ] Create Secret Manager secrets: `jwt-secret-key`, `gemini-api-key`, `database-url`, `default-admin-password` (§13).
7. [ ] Build + push + deploy backend (§14).
8. [ ] Build + push + deploy frontend with `VITE_API_URL` pointed at the backend (§14).
9. [ ] Tighten backend `ALLOWED_ORIGINS`/`FRONTEND_URL` to the real frontend URL, redeploy (§14).
10. [ ] Run every verification command in §15 — health, register, login, `/me`, `/api/generate`, `/api/convert`, confirm files in the bucket, frontend loads.
11. [ ] (Optional) Set up the CI/CD pipeline + GitHub secrets — see `CICD_SETUP.md` (§16).
12. [ ] Change `DEFAULT_ADMIN_EMAIL`/`DEFAULT_ADMIN_PASSWORD` away from any placeholder before pointing real users at this.
13. [ ] Walk the security checklist (§17) and consciously accept or close the two open items.

## 19. Storage layer migration (full GCS cutover)

`app/storage/` was refactored from a `BlobStorage` (`save`/`read`/`delete`) interface to a `StorageService` interface with the full set of operations every backend now implements: `upload_file`, `download_file`, `delete_file`, `file_exists`, `list_files`, `generate_signed_url`. All three backends (`local.py`, `gcs.py`, `azure_blob.py`) implement it identically from the caller's point of view.

**Bucket layout** (`app/utils/files.py`):

```
uploads/{user_id}/{timestamp}_{uuid}_{filename}     - original uploaded file (upload/convert flow)
generated/{user_id}/{timestamp}_{uuid}_v{n}_{name}  - PDF/DOCX/JSON from the manual Resume Generator
converted/{user_id}/{timestamp}_{uuid}_v{n}_{name}  - PDF/DOCX/JSON from the Resume Converter
temp/{user_id}/...                                  - reserved, unused today (uploads are processed
                                                        fully in-memory, no intermediate file needed)
```

**Cascade delete**: deleting a resume (`DELETE /api/resumes/{id}` and the admin equivalent) now calls `delete_resume_files()`, which removes every blob (original + every version's PDF/DOCX/JSON) before the DB soft-delete commits. Previously, deletion was DB-only and blobs accumulated in storage forever — fixed as part of this migration, verified by `tests/test_resumes_api.py::test_delete_resume_removes_underlying_blobs`.

**Signed URLs**: new `GET /api/resumes/{id}/versions/{version_id}/signed-url` endpoint returns a time-limited direct-to-bucket link (`SIGNED_URL_EXPIRATION_MINUTES`, default 15) instead of proxying bytes through the API — additive, the original bytes-streaming `/download` endpoint is unchanged so the existing frontend contract (`frontend/src/api/resumes.ts`'s `downloadVersion`) keeps working without any frontend change.

**Additional IAM requirement for signed URLs on Cloud Run**: the runtime service account needs `roles/iam.serviceAccountTokenCreator` bound **on itself** (self-impersonation) —Compute-metadata-based credentials (what Cloud Run uses) have no private key to sign a URL with locally, so `google-auth` falls back to calling the IAM `signBlob` API, which requires this grant:

```bash
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountTokenCreator"
```

Without this, `generate_signed_url()` on Cloud Run fails with a permission error even though the same service account can already read/write the bucket fine.

**Bucket CORS** (only needed if the frontend ever fetches a signed URL directly via `fetch()`/`XMLHttpRequest` from the browser, rather than a plain `<a href>` navigation or redirect — plain navigations aren't subject to CORS):

```bash
cat > /tmp/cors.json <<'EOF'
[{"origin": ["https://resumebuilder-frontend-xxxxx-uc.a.run.app"], "method": ["GET"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600}]
EOF
gcloud storage buckets update "gs://${BUCKET_NAME}" --cors-file=/tmp/cors.json
```

**File validation** (`app/routers/resumes.py`): extension (`.pdf`/`.docx`) + declared `Content-Type` + magic-byte signature (`%PDF-` / `PK\x03\x04`) + 5MB max size. All four checked before any Gemini call or storage write.

**Error handling**: `app/storage/base.py` adds `StorageError` (bucket missing, permission denied, network failure after retries) as distinct from `FileNotFoundError` (object genuinely doesn't exist) — routers map `StorageError` to `502`, `FileNotFoundError` to `404`. `gcs.py` retries `ServiceUnavailable`/`TooManyRequests` with exponential backoff (3 attempts) before giving up.

**Tests** (new — none existed before this migration): `backend/tests/`, run with:

```bash
cd backend
pip install -r requirements.txt   # now includes pytest + httpx
pytest tests/ -v
```

31 tests: `test_storage_local.py` (LocalStorageService, exercised for real against a temp dir), `test_storage_gcs.py` (GCSStorageService, with the `google-cloud-storage` SDK mocked — bucket-missing/permission-denied/not-found/retry-then-succeed/retry-exhausted all covered), `test_resumes_api.py` (full HTTP flow through the real app + real local storage + mocked Gemini — upload, generate, download, signed-url, delete-cascades-to-storage, regenerate-preserves-old-versions, cross-user isolation). All 31 pass as of this write-up.

Add a CI test job (`.github/workflows/backend-cicd-gcp.yml` already has one, currently a no-op since no `tests/` existed — it will now actually run these).
