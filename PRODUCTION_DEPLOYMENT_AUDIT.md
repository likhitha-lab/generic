# Production Deployment Audit — Resume Management System

No code was changed to produce this report. Findings combine live checks run
during this audit (curl against the real Cloud Run URLs, a fresh
`pytest`/`npm run build` pass, `git log`/`git status`) with everything
established across the four prior deployment audits in this repo
(`DEPLOYMENT_READINESS_REPORT.md`, `DEPLOYMENT_CONFIGURATION_REPORT.md`,
`DEPLOYMENT_GCS_PERMISSION_REPORT.md`, `DEPLOYMENT_GEMINI_AUDIT.md`,
`ADMIN_AUTH_AUDIT.md`), cross-referenced against what's actually live right
now rather than repeated at face value.

## Top-line finding, read this first

**It cannot be confirmed from here that the last several fixes pushed to
`main` have actually reached the live Cloud Run backend.** Live evidence:

```
OPTIONS /api/auth/login  Origin: https://frontend-1069915675190.asia-south1.run.app  -> 400 "Disallowed CORS origin"
OPTIONS /api/auth/login  Origin: http://localhost:5173                                -> 200 OK
```
Only the code's literal hardcoded default (`http://localhost:5173`) is
currently allowed — not the real deployed frontend URL. This is the exact
symptom of `ALLOWED_ORIGINS` never having been set on the currently-serving
revision at all, which is what the code falls back to when the env var is
absent. Two fixes already committed to this repo were specifically meant to
prevent this (`f1c534f4`, `fb7c7dd1`) — their effect is not visible live,
which means either the CI/CD pipeline (`.github/workflows/deploy.yml`,
triggered automatically on every push to `main`) has not completed
successfully since those commits, or something else reset it after. **This
audit has no access to the GitHub Actions run history or Cloud Run's actual
current revision/env-var state** (no `gh`/`gcloud` credentials in this
environment) — checking the Actions tab for the most recent `Deploy to Cloud
Run` run is the single highest-value next step, and recontextualizes several
"already fixed" items below as **status unknown in production** rather than
confirmed resolved.

## Consolidated blocker list (all severities)

| # | Blocker | Area | Severity | Status |
|---|---|---|---|---|
| 1 | Live frontend origin rejected by CORS (see above) | Backend/Cloud | **Critical** | **Confirmed live, right now** |
| 2 | Cannot confirm recent CI/CD runs actually succeeded/deployed | Cloud | **Critical** | Unknown — check GitHub Actions |
| 3 | Leaked GCP service-account key, pushed to GitHub, rotation unconfirmed | Cloud | **Critical** | Unknown — user action required |
| 4 | `gemini-api-key` Secret Manager value possibly whitespace-corrupted or stale version | Backend/Cloud | **Critical** | Diagnosed, not yet confirmed fixed |
| 5 | Runtime SA `resume-gcs-service` naming mismatch vs. provisioned `resume-builder-runtime`; bucket IAM grants likely missing | Cloud | **High** | Diagnosed, gcloud commands provided, not yet confirmed applied |
| 6 | `GCS_BUCKET_NAME` GitHub secret existence unconfirmed | Cloud | **High** | Unknown — check repo secrets |
| 7 | Local `.env` points at the same production Neon database as Cloud Run | Backend | **Medium** | Confirmed, no isolated dev DB exists |
| 8 | No role-promotion API — admin changes require a manual DB `UPDATE` | Backend | **Medium** | By design today, worth a real fix eventually |
| 9 | JWT stored in `localStorage`, not httpOnly cookies (XSS exfiltration surface) | Frontend | **Medium** | Pre-existing design choice, not new |
| 10 | Legacy Azure CI workflows still trigger, will show failing `deploy` jobs | Cloud | **Low** | Cosmetic |
| 11 | Dead `VITE_API_KEY` env var, unused by any code | Frontend | **Low** | Harmless |
| 12 | Temporary Gemini debug-logging block still in `gemini_client.py` | Backend | **Low** | Intentionally left in until #4 is confirmed fixed |
| 13 | `.git` history still ~123MB from previously-committed `.venv`/`node_modules`/secret | Cloud/repo | **Low** | Cosmetic unless history is rewritten |

---

## Backend

**Health** — ✅ Live-verified. `GET /health` → `200 {"status":"ok"}` (first
hit timed out on a cold start, succeeded on retry — Cloud Run scaling to
zero between requests, expected on `--min-instances 0`, not a defect).

**Authentication / Registration / JWT** — ✅ Code-verified
(`app/routers/auth.py`), previously confirmed live by the user (`201`/`200`
on `/register`, `/login`, `/token`). `create_access_token`/
`create_refresh_token` (`app/auth/jwt.py`) sign with `JWT_SECRET_KEY`,
resolved via Secret Manager — same mechanism proven working since the app
demonstrably issues and validates tokens.

**Protected routes** — ✅ `app/auth/dependencies.py`'s `get_current_user` +
`app/middleware/rbac.py`'s `require_role`, applied per-route via FastAPI
`Depends`. Confirmed live: `/api/auth/me` returns `200` only with a valid
token per the user's own report.

**Admin routes** — ✅ Gated by `require_admin` (`rbac.py`). Exactly one
admin account exists and is reachable (`ADMIN_AUTH_AUDIT.md`,
`admin@example.com`, seeded via `init_db.py::_seed_admin`). No
role-promotion endpoint exists (blocker #8, Medium) — the only way to create
a second admin today is a direct database `UPDATE`.

**Resume upload** — ⚠️ Partially fixed, live status unconfirmed. Root cause
(bucket-level `storage.buckets.get` Forbidden, `roles/storage.objectAdmin`
deliberately excludes it) diagnosed and the fatal check softened to a
warning (`app/storage/gcs.py`, commit `dba59952`) — but the underlying IAM
grant to the actual runtime SA (`resume-gcs-service`) was never confirmed
applied (blocker #5), and `GCS_BUCKET_NAME`'s GitHub secret was never
confirmed to exist (blocker #6). If either is still missing, upload either
still 500s (object-level ops truly blocked) or now degrades more gracefully
(bucket-check warning only) but object upload/download would still fail if
`roles/storage.objectAdmin` itself isn't bound.

**Gemini integration** — ❌ Confirmed broken as of the last dedicated audit
(`DEPLOYMENT_GEMINI_AUDIT.md`): `400 INVALID_ARGUMENT: API key not valid`.
Every code-level cause was ruled out (wrong env var, SDK reading the wrong
key, Vertex AI mode) — narrowed to the live Secret Manager secret's actual
content (trailing whitespace/newline from an `echo`-based creation, or a
stale version baked into the current revision). Verification commands
provided in that report; not yet run/confirmed. **This blocks the core
resume-generation feature end-to-end** until resolved — Critical.

**GCS upload** — see "Resume upload" above, same root cause.

**Database** — ✅ Verified directly (read-only) this session and again in
`ADMIN_AUTH_AUDIT.md`: single Neon Postgres, reachable, 927/927 backend
tests pass locally against a SQLite test DB (tests never touch the real
Neon instance — `tests/conftest.py` forces `DATABASE_URL=sqlite:///...`,
`STORAGE_BACKEND=local` unconditionally). Confirmed local `.env` and Cloud
Run point at the same physical database (blocker #7, Medium — not a defect
in the running app, but an operational risk: local development is not
isolated from production data).

**Secret Manager** — ✅ Mechanism confirmed correct and working
(`DATABASE_URL`/`JWT_SECRET_KEY`/`DEFAULT_ADMIN_PASSWORD` all resolve
correctly — the app boots, connects to Postgres, and issues valid JWTs, all
of which depend on this). ❌ `gemini-api-key`'s specific value is suspect
(see Gemini integration above). ⚠️ `GCS_BUCKET_NAME` is a plain
`--set-env-vars`/`--update-env-vars` value sourced from a **GitHub repository
secret** (not Secret Manager) — its existence there was never confirmed.

**Cloud Run environment variables** — ⚠️ See "Top-line finding" — `ALLOWED_ORIGINS`
is live-confirmed NOT set to the production frontend URL right now, despite
two committed fixes intended to guarantee this. `STORAGE_BACKEND=gcs`
appears to be in effect (the app is healthy and serving, and the eager
startup storage check added in `fb7c7dd1` would crash the container on boot
if `GCS_BUCKET_NAME` were truly empty — since it's healthy, that specific
value is at least non-empty on whatever revision is currently live).

**Logging** — ✅ Startup log (`app/main.py` lifespan) now reports
`STORAGE_BACKEND`, `GCS_BUCKET_NAME`, DB dialect, and the parsed CORS origin
list — no secrets included. GCS operations (`app/storage/gcs.py`) log every
success/failure with path/byte-count context. Gemini calls
(`gemini_client.py`) still carry a **temporary** debug-logging block
(explicitly marked for removal once the API-key issue is confirmed fixed —
blocker #12, Low, intentional for now).

**Error handling** — ✅ Consistent typed-exception pattern throughout:
`GeminiError` subtypes (`gemini_client.py`) distinguish
timeout/truncated/invalid-response/unavailable; `StorageError`
(`app/storage/base.py`) wraps every GCS failure mode
(`Forbidden`/`NotFound`/transient-retry) with a clear message; both map to
sensible HTTP status codes at the router boundary rather than leaking raw
SDK exceptions.

## Frontend

**API URL** — ✅ `VITE_API_URL` is a Docker build `ARG`→`ENV`
(`frontend/Dockerfile`), inlined by Vite at build time
(`src/api/client.ts:10`, throws a clear startup error if unset rather than
silently defaulting). Verified this session: rebuilt the frontend image
locally and grepped the built JS bundle for the real backend hostname — confirmed
present.

**Authentication flow** — ✅ `src/api/client.ts`: request interceptor attaches
`Authorization: Bearer <token>`; response interceptor transparently retries
once on `401` via refresh-token exchange, then clears tokens and signals
logout if that also fails. Standard, correctly implemented.

**Upload flow** — ✅ `UploadResume.tsx` — drag/drop, client-side type/size
validation, loading/success/error states, `Remove` button correctly
disabled mid-upload (a previously-fixed, verified behavior). Depends on the
backend issues above to fully succeed end-to-end in production right now.

**Download flow** — ✅ `DownloadButtons.tsx` — per-format (PDF/DOCX) fetch +
blob download, loading/success states, admin variant reviewed too.

**Token storage** — ⚠️ `src/api/tokenStorage.ts` — plain `localStorage`, not
an httpOnly cookie. Standard for a JWT/SPA architecture like this one and
not a new regression, but it is inherently readable by any successful XSS on
the frontend origin (blocker #9, Medium — a design-level tradeoff to be
aware of, not a bug to fix reflexively).

**Production build** — ✅ Verified this session: `npm run lint` (`tsc
--noEmit`) clean, `npm run build` clean, Docker image builds successfully
end-to-end on this machine.

## Cloud

**Cloud Run services** — ✅ Both `backend` and `frontend` are live and
responding (`asia-south1`, verified via direct `curl` this session).

**Artifact Registry** — ✅ Provisioned per `scripts/setup-gcp-cicd.sh`
(`resume-builder` Docker repo) — images are clearly being pushed/pulled
successfully, since both services are live and serving current-looking
content (frontend title, `Dataflix AI Resume Management`, matches the latest
UI work).

**GCS bucket** — ⚠️ `resume_bucket08` — existence/config not independently
verified this session (would require GCS API access this environment
doesn't have); the app-level symptom (upload 500) previously confirmed it's
at least reachable (got as far as a permission error, not a
bucket-not-found or connection error).

**Service Account** — ❌ **Naming/provisioning mismatch, unresolved.** The
actual runtime SA (from the live GCS error) is
`resume-gcs-service@developer-project-likhitha.iam.gserviceaccount.com` —
`scripts/setup-gcp-cicd.sh` and `deploy.yml` are written around a *different*
account, `resume-builder-runtime@...`. Whichever account is genuinely
attached today very likely never received the IAM roles the setup script
grants (blocker #5, High).

**IAM permissions** — ❌ Same issue — `resume-gcs-service` needs
`roles/storage.objectAdmin` (object CRUD) and `storage.buckets.get`
(bucket-level, via `roles/storage.legacyBucketReader` or a custom role) on
`resume_bucket08` specifically. Exact `gcloud` commands are in
`DEPLOYMENT_GCS_PERMISSION_REPORT.md` §6 — not yet confirmed run.
`roles/secretmanager.secretAccessor` IS confirmed working (proven by
`DATABASE_URL`/`JWT_SECRET_KEY` resolving correctly at boot).

**Secret Manager** — see Backend section above — mechanism sound,
`gemini-api-key`'s content is the one open question (blocker #4, Critical).

**Gemini API configuration** — ❌ See "Gemini integration" above — the
single most user-visible open blocker, since it breaks the core
resume-generation feature for every user, not just an edge case.

## What "Do not implement fixes" means for next steps

Every blocker above already has a specific, previously-written fix or
verification command ready to go (referenced inline, full detail in each
linked report) — nothing here requires new investigation, only: (1)
confirming which of the "already fixed in code" items have actually reached
the live service via a successful CI/CD run, (2) running the few `gcloud`
verification commands that need real credentials this environment doesn't
have, and (3) applying the IAM grants once #5/#6 are confirmed. Say the word
and I'll work through them in severity order once you've checked the
GitHub Actions run history and confirmed which of these are still live.
