# Deployment Readiness Report — Google Cloud Run

Audit date: 2026-07-28. Scope: make the repository deployment-ready for Cloud Run
without changing application functionality, resume generation logic, UI, or
business logic. No source code was modified — all changes are repository
hygiene (git tracking, `.gitignore`) plus one CI/CD config line fixed in a prior
session (CORS).

## 1. Critical finding — rotate this key now

`secrets/developer-project-likhitha-876fc71362a1.json`, a **live GCP
service-account private key** (`1069915675190-compute@developer.gserviceaccount.com`,
project `developer-project-likhitha`), was committed to git and **already pushed
to GitHub** (`origin/main`). Traced to a same-day commit `326f98fc` ("Replace
project with latest ResumeBuilder") that wholesale re-added the working
directory and, in the process, deleted all three `.gitignore` files that would
normally have blocked it.

**Action required, not done by this audit (needs GCP Console access):**
1. GCP Console → IAM & Admin → Service Accounts → `1069915675190-compute@...` →
   Keys → delete key ID `876fc71362a1...`.
2. Confirm nothing in this project actually needs a key file at all — Cloud Run
   uses its runtime service account via Application Default Credentials
   automatically (`GOOGLE_APPLICATION_CREDENTIALS` should stay blank in
   production; `app/storage/gcs.py` and `config.py` already document this).
3. Optional, separate decision: the key is still recoverable from git history
   (`git show 326f98fc:secrets/...json`) even after this audit's cleanup.
   Removing it from history requires a history rewrite (`git filter-repo` /
   BFG) and a force-push — disruptive to any existing clones/forks. Key
   rotation (step 1) is the actual fix; history rewrite is optional hygiene on
   top of it. Not performed here — force-push is a destructive operation that
   needs your explicit go-ahead.

## 2. Repository hygiene — fixed

The same `326f98fc` commit that deleted the leaked key also deleted every
`.gitignore` and, with nothing left to filter it, git tracked essentially the
entire local working directory:

| Category | Files tracked before | Now |
|---|---|---|
| `backend/.venv/` (full Python venv) | 9,185 | 0 |
| `frontend/node_modules/` | 7,966 | 0 |
| `__pycache__/` / `*.pyc` | 3,684 | 0 |
| `backend/local_data/` (real generated resume PDF/DOCX/JSON) | 644 | 0 |
| `frontend/dist/` (build output) | 4 | 0 |
| `secrets/*.json` | 1 | 0 (see §1) |
| `resumebuilder.db` (root + backend) | 2 | 0 |
| **Total tracked files** | **18,162** | **225** |

Fixed by:
- Restoring `.gitignore`, `backend/.gitignore`, `frontend/.gitignore` to their
  pre-deletion content (recovered from git history at the last good commit),
  with `Thumbs.db` added to the root OS-files section.
- `git rm -r --cached` on all of the above — **untracks only, nothing deleted
  from disk.** Your local `.venv`, `node_modules`, `local_data`, and `dist`
  still exist and work exactly as before.
- Committed as `2a28e65c` ("Restore .gitignore and untrack committed
  secrets/venv/node_modules/artifacts") and pushed to `origin/main`.

`.git` history is still ~123MB (old blobs remain reachable from earlier
commits) — expected; only a history rewrite would reclaim that, which is the
same rewrite discussed in §1 and carries the same force-push tradeoff.

No `tests/`, `reference_docs/`, `static/`, or source files were touched —
verified post-cleanup: `backend/app` (86 files), `backend/tests` (43),
`backend/reference_docs` (23), `backend/static` (1 — `logo.png`),
`frontend/src` (30) all intact.

## 3. Environment variable name correction

The task brief lists **`CORS_ORIGINS`** as a required backend variable — this
is not what the code reads. `app/core/config.py:28` and
`allowed_origins_list` (line 135) read **`ALLOWED_ORIGINS`** (comma-separated).
This exact mismatch caused a production CORS outage fixed in a prior session
(see `.github/workflows/deploy.yml`'s "Lock backend CORS" step, already
corrected to use `ALLOWED_ORIGINS` with both `localhost:5173` and the deployed
frontend URL). Use `ALLOWED_ORIGINS` everywhere going forward — `CORS_ORIGINS`
is silently ignored by pydantic's `extra="ignore"` settings config, not an
error, which is exactly what made the original bug hard to spot.

## 4. `VITE_API_KEY` is dead code

`frontend/.env` sets `VITE_API_KEY`, and its own comment says it "must match
the backend's `API_KEY` env var." Neither exists: grepped the full frontend
`src/` — no reference to `VITE_API_KEY` anywhere — and the full backend `app/`
— no `API_KEY` setting (only `GEMINI_API_KEY`, which is unrelated and never
sent to the frontend). Not a blocker, just unused legacy config from an
earlier auth design. Safe to leave (harmless) or delete from `.env`/`.env.example`
— your call, not required for deployment.

## 5. Legacy Azure workflows still enabled (non-blocking noise)

`.github/workflows/backend-cicd.yml` and `frontend-cicd.yml` target Azure App
Service / Static Web Apps and trigger on the same `push: main` /
`pull_request: main` events as the real GCP pipeline (`deploy.yml`). Their
`test`/`build` jobs run fine (no Azure creds needed); their `deploy` jobs will
fail if `AZURE_*` secrets aren't configured in this repo, showing red X's in
the Actions tab that have nothing to do with Cloud Run health. Not a
deployment blocker — just recommend disabling or deleting them if Azure is no
longer a target, to keep CI signal clean. Not touched in this audit (judgment
call, not required for GCP deployment to succeed).

## 6. Cloud Run / Docker / Vite verification results

- **Backend Dockerfile**: multi-stage (`python:3.12-slim` builder → slim
  runtime), non-root user, `gunicorn` + `uvicorn` workers bound to
  `${PORT:-8080}` (Cloud Run's injected `$PORT` is respected, not hardcoded).
  **Build verified successful** end-to-end (all 60+ dependencies resolved,
  image exported) via an isolated scratch-copy build — the in-place `docker
  build` on this machine failed only due to this network's corporate
  TLS-interception breaking pip's default cert chain (confirmed by
  reproducing the same failure installing a single package with plain `pip
  install`, then succeeding with `--trusted-host`). This is a local-network
  artifact, not a Dockerfile defect, and will not reproduce on GitHub Actions'
  runners (clean network) — no Dockerfile change was made to work around it,
  since baking `--trusted-host` into a production image build would be an
  inappropriate permanent fix for a transient local condition.
- **Frontend Dockerfile**: multi-stage (`node:20-slim` builder → `nginx:1.27-alpine`
  runtime). **Build verified successful directly on this machine** (no
  network issue here — npm registry access works). Confirmed `VITE_API_URL`
  build-`ARG` is correctly threaded through to `ENV` and inlined into the
  built JS bundle (grepped the built bundle inside the image for the backend
  hostname — found it).
- **nginx**: `nginx.conf.template` + `NGINX_ENVSUBST_FILTER='^PORT$'` correctly
  restricts nginx's own template envsubst to just `$PORT`, avoiding corruption
  of nginx's native `$uri`/`$host` variables. SPA fallback
  (`try_files $uri $uri/ /index.html`) present — client-side routing works on
  refresh/deep-link.
- **Vite config**: no hardcoded API URLs; `VITE_API_URL` read via
  `import.meta.env` at build time only (`frontend/src/api/client.ts` even
  throws a clear startup error if it's unset, rather than silently falling
  back to a wrong host).
- **Backend startup**: `app/main.py` lifespan loads Key Vault secrets
  (Azure-only, no-op on GCP), runs `init_db()`, logs storage backend + DB
  dialect. `/health` endpoint present for Cloud Run health checks. CORS,
  security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, HSTS) all present and unconditional.
- **Database**: PostgreSQL-only (no SQLite branch), pool sized per-instance
  correctly for Cloud Run's multi-instance model (`DB_POOL_SIZE=5`,
  `pool_pre_ping`, `pool_recycle` against Neon's idle-connection drops).
- **GCS storage**: `app/storage/gcs.py` uses Application Default Credentials
  when `GOOGLE_APPLICATION_CREDENTIALS` is blank (the correct production
  posture on Cloud Run) — never bakes a key into the image.
- **Hardcoded localhost / Windows paths**: `frontend/src` — zero hardcoded
  URLs (grepped clean). `backend/app` — only expected dev-default fallback
  values in `config.py` (`ALLOWED_ORIGINS` default, `DATABASE_URL` default),
  both always overridden by real env vars/secrets in production. One
  Windows-style path exists intentionally: `file_generator.py`'s
  `_CALIBRI_SEARCH_DIRS` includes `C:\Windows\Fonts` as one of five
  cross-platform font-lookup candidates (also lists two Linux paths and one
  macOS path) with a documented Helvetica fallback if none match — harmless
  on Linux, `os.path.exists` just returns `False` for that candidate and it
  falls through to the next.

## 7. Tests / build results (run this session)

| Check | Result |
|---|---|
| `pytest tests/` (backend) | **927 passed**, 10 deselected (live-Gemini marker, opt-in) |
| `npm run lint` (frontend, `tsc --noEmit`) | **clean** |
| `npm run build` (frontend, `vite build`) | **clean**, `dist/` produced |
| `docker build` — backend | **succeeds** (verified via scratch copy, see §6) |
| `docker build` — frontend | **succeeds** directly, `VITE_API_URL` confirmed inlined |

## 8. Required Google Cloud resources

- **Cloud Run services**: `backend`, `frontend` (region `asia-south1`, per
  existing `deploy.yml`).
- **Artifact Registry**: Docker repository `resume-builder` in `asia-south1`
  (provisioned by `scripts/setup-gcp-cicd.sh`).
- **Secret Manager**: see §9 below.
- **Cloud Storage**: one bucket (`resume_bucket08` in current config) with
  `Resume_uploads/` and `Resume_output/` top-level folders (see
  `app/utils/files.py`, `DEPLOYMENT_GCP.md` §11).
- **Service accounts**:
  - `github-deployer@<project>.iam.gserviceaccount.com` — what GitHub Actions
    runs `gcloud run deploy` as.
  - `resume-builder-runtime@<project>.iam.gserviceaccount.com` — what the
    deployed backend Cloud Run service runs as.
- **IAM roles**:
  | Role | Bound to | Why |
  |---|---|---|
  | `roles/run.admin` | deploy SA | create/update Cloud Run revisions |
  | `roles/artifactregistry.writer` | deploy SA | push images |
  | `roles/iam.serviceAccountUser` | deploy SA | deploy a revision that runs as the runtime SA |
  | `roles/storage.objectAdmin` | runtime SA | read/write/delete resume files in GCS |
  | `roles/secretmanager.secretAccessor` | runtime SA | resolve `--set-secrets` at container start |
  | `roles/iam.serviceAccountTokenCreator` (on itself) | runtime SA | required for `generate_signed_url()` on Cloud Run (no local private key to sign with) |

  Neither service account should hold `roles/owner`/`roles/editor` — both
  scoped to exactly the above.
- **Neon PostgreSQL**: external, not a GCP resource — connection string lives
  in Secret Manager (`database-url`), not provisioned by any script here.

## 9. Required Secret Manager secrets

| Secret name | Injected as | Source |
|---|---|---|
| `gemini-api-key` | `GEMINI_API_KEY` | your real Gemini API key |
| `jwt-secret-key` | `JWT_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `database-url` | `DATABASE_URL` | Neon connection string, `postgresql+psycopg://...` |
| `default-admin-password` | `DEFAULT_ADMIN_PASSWORD` | generated, printed once by `scripts/setup-gcp-secrets.sh` |

Provisioned by `scripts/setup-gcp-secrets.sh` (already correct, already
reviewed — no changes made). Non-secret config (`GEMINI_MODEL`,
`STORAGE_BACKEND`, `GCS_BUCKET_NAME`, `GOOGLE_CLOUD_PROJECT`) is set as plain
`--set-env-vars`, not Secret Manager, in `deploy.yml` — correct, these aren't
sensitive.

GitHub repository secrets required (distinct from GCP Secret Manager):
`GCP_CREDENTIALS` (the deploy SA's key — genuinely sensitive), plus
`GCP_RUNTIME_SERVICE_ACCOUNT` and `GCS_BUCKET_NAME` (resource identifiers, not
secret values).

## 10. Exact deployment commands

This repo's actual deployment mechanism is CI/CD (`.github/workflows/deploy.yml`),
triggered automatically on every push to `main` — not manual `gcloud`
commands. It runs, in order: backend tests → frontend lint/build → build+push
backend image → deploy backend → build+push frontend image (with the live
backend URL baked in as `VITE_API_URL`) → deploy frontend → lock backend CORS
to the deployed frontend URL.

One-time setup (run once, locally, already authenticated):
```
gcloud auth login
gcloud config set project developer-project-likhitha
bash scripts/setup-gcp-cicd.sh          # APIs, Artifact Registry, both SAs, IAM roles
GEMINI_API_KEY="<real key>" DATABASE_URL="<neon connection string>" \
  bash scripts/setup-gcp-secrets.sh     # Secret Manager entries
```

Manual equivalent of what CI does (only if you need to deploy outside CI):
```
# Backend
docker build -t asia-south1-docker.pkg.dev/developer-project-likhitha/resume-builder/backend:manual backend/
docker push asia-south1-docker.pkg.dev/developer-project-likhitha/resume-builder/backend:manual
gcloud run deploy backend \
  --image asia-south1-docker.pkg.dev/developer-project-likhitha/resume-builder/backend:manual \
  --region asia-south1 --platform managed --allow-unauthenticated \
  --memory 512Mi --cpu 1 --concurrency 40 --timeout 300 \
  --service-account resume-builder-runtime@developer-project-likhitha.iam.gserviceaccount.com \
  --set-env-vars "GEMINI_MODEL=gemini-2.5-flash,STORAGE_BACKEND=gcs,GCS_BUCKET_NAME=resume_bucket08,GOOGLE_CLOUD_PROJECT=developer-project-likhitha" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,JWT_SECRET_KEY=jwt-secret-key:latest,DATABASE_URL=database-url:latest,DEFAULT_ADMIN_PASSWORD=default-admin-password:latest"

# Frontend (needs the backend URL from the step above as VITE_API_URL)
BACKEND_URL=$(gcloud run services describe backend --region asia-south1 --format='value(status.url)')
docker build --build-arg VITE_API_URL="$BACKEND_URL" \
  -t asia-south1-docker.pkg.dev/developer-project-likhitha/resume-builder/frontend:manual frontend/
docker push asia-south1-docker.pkg.dev/developer-project-likhitha/resume-builder/frontend:manual
gcloud run deploy frontend \
  --image asia-south1-docker.pkg.dev/developer-project-likhitha/resume-builder/frontend:manual \
  --region asia-south1 --platform managed --allow-unauthenticated \
  --memory 256Mi --cpu 1 --concurrency 80 --timeout 60

# Re-lock CORS to the frontend's own URL (comma-in-value requires gcloud's custom delimiter)
FRONTEND_URL=$(gcloud run services describe frontend --region asia-south1 --format='value(status.url)')
gcloud run services update backend --region asia-south1 \
  --update-env-vars "^##^ALLOWED_ORIGINS=http://localhost:5173,$FRONTEND_URL##FRONTEND_URL=$FRONTEND_URL##BACKEND_URL=$BACKEND_URL"
```

## 11. Deployment checklist

- [x] Backend Dockerfile builds successfully (verified)
- [x] Frontend Dockerfile builds successfully (verified)
- [x] `VITE_API_URL` correctly injected at build time (verified inlined in bundle)
- [x] PORT handled correctly in both services (backend gunicorn, frontend nginx envsubst)
- [x] CORS allows both `localhost:5173` and the deployed frontend URL (fixed prior session)
- [x] No hardcoded localhost/production URLs in frontend source
- [x] No unintended Windows-path dependency (the one hit is an intentional, harmless fallback)
- [x] `.gitignore` restored; secrets/venv/node_modules/build output/local db files untracked
- [x] Backend test suite passes (927/927 non-live tests)
- [x] Frontend lint + build pass
- [ ] **Rotate the leaked service-account key in GCP IAM** (§1 — manual, not done here)
- [ ] Decide whether to rewrite git history to fully purge the leaked key (§1 — optional, destructive, needs your go-ahead)
- [ ] Confirm Secret Manager has real values for all 4 secrets in §9 (`setup-gcp-secrets.sh` if not)
- [ ] Confirm GCS bucket `resume_bucket08` exists with `Resume_uploads/`/`Resume_output/` folders
- [ ] Confirm GitHub repo secrets `GCP_CREDENTIALS`, `GCP_RUNTIME_SERVICE_ACCOUNT`, `GCS_BUCKET_NAME` are set
- [ ] Optional: disable/remove the legacy Azure workflows (§5) to keep CI signal clean
- [ ] Optional: remove the dead `VITE_API_KEY` from `frontend/.env`/`.env.example` (§4)
- [ ] After next CI run completes, smoke-test register → login → upload → generate → download in the browser against the live URLs

## 12. Risks

- **Highest**: the leaked service-account key (§1) — until rotated, anyone
  with read access to this GitHub repo's history has valid GCP credentials
  scoped to whatever the Compute default SA can do in this project.
- **Medium**: git history still contains the key and the old `.venv`/
  `node_modules` blobs; `.git` is ~123MB. Not a functional risk, but a
  standing exposure until/unless history is rewritten (your call — destructive,
  requires force-push).
- **Low**: legacy Azure CI workflows will keep showing failed `deploy` jobs
  in the Actions tab (§5) — cosmetic, not a functional blocker.
- **Low**: full container runtime (against a real Neon DB / real GCS bucket)
  was not exercised end-to-end in this sandbox — only build-time verification
  was possible here. First real Cloud Run deploy (or `docker-compose up`
  locally) is the actual live-runtime test.
