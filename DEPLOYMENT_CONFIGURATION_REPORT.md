# Deployment Configuration Report — Upload 500 + Login CORS Failure

Scope: deployment configuration only. No business logic, resume generation
logic, or UI changed.

## Issue 1 — Upload returns HTTP 500 (`GCS_BUCKET_NAME requires...`)

**Why it fails:** `app/storage/factory.py:24` correctly raises `RuntimeError`
when `STORAGE_BACKEND=gcs` and `GCS_BUCKET_NAME` is empty. The bug isn't in
that check — it's *when* it runs. `get_storage()` is a FastAPI
`Depends(get_storage)` on the upload/download/admin routes
(`app/routers/resumes.py`, `app/routers/admin.py`) — it is only evaluated
lazily, the first time a request actually hits one of those endpoints. The
container boots fine, `/health` passes, Cloud Run marks the revision healthy —
and the misconfiguration only surfaces later as a 500 on someone's first
upload. That silence is exactly what let this ship undetected.

**Root cause of the empty value itself:** `.github/workflows/deploy.yml`'s
backend deploy step sets
`GCS_BUCKET_NAME=${{ secrets.GCS_BUCKET_NAME }}`. If the GitHub repository
secret `GCS_BUCKET_NAME` is not set (or was never added), this interpolates to
an empty string — Cloud Run receives `GCS_BUCKET_NAME=` (present, but blank),
which is exactly the falsy value the `if not settings.GCS_BUCKET_NAME` check
catches. **Verify this secret exists**: repo Settings → Secrets and variables
→ Actions → confirm `GCS_BUCKET_NAME` is listed with the real bucket name
(`resume_bucket08` per `DEPLOYMENT_GCP.md`).

## Issue 2 — Login/Register CORS preflight rejected (400 `Disallowed CORS origin`)

**Why it fails:** `app/core/config.py:28` defines `ALLOWED_ORIGINS` (not
`CORS_ORIGINS` — grepped the entire repo, no code anywhere reads
`CORS_ORIGINS`). Whatever set the live Cloud Run env var used the wrong name;
pydantic's `SettingsConfigDict(..., extra="ignore")` silently drops unknown
keys instead of erroring, so `ALLOWED_ORIGINS` falls back to its default,
`"http://localhost:5173"` — which never matches a deployed frontend origin.

**A second, independent contributor found during this audit:** the backend
deploy step in `deploy.yml` only ever set
`GEMINI_MODEL,STORAGE_BACKEND,GCS_BUCKET_NAME,GOOGLE_CLOUD_PROJECT` via
`--set-env-vars` — a flag that **replaces the full env-var set** for the new
revision, not merges it. `ALLOWED_ORIGINS` was added only by a later, separate
step ("Lock backend CORS to the deployed frontend URL", using
`--update-env-vars`, which merges). So every backend deploy silently wiped
`ALLOWED_ORIGINS` back to the code default, and it was only correct again
once the *entire* pipeline — including the frontend build/deploy in between —
finished without a hitch. Any transient failure anywhere in that window (a
flaky build, a quota hit, this exact GCS misconfiguration now making the
backend fail its own deploy) left CORS broken until the next fully clean run.

## Code changes made (minimal, deployment-config only)

1. **`backend/app/core/config.py`** — `allowed_origins_list` now also accepts
   a JSON array (`["https://a.example","https://b.example"]`) in addition to
   the existing comma-separated string, since some deploy tooling/consoles
   serialize list-valued env vars as JSON. Comma-separated remains the
   primary, documented format and behaves identically to before; JSON is
   purely additive. Malformed input (e.g. `[` present but not valid JSON)
   falls back to the original comma-split behavor rather than crashing.

2. **`backend/app/main.py`** — `get_storage()` is now called once, eagerly,
   in the startup `lifespan` when `STORAGE_BACKEND != "local"`. A missing
   `GCS_BUCKET_NAME` (or a missing Azure connection string) now fails the
   container at **startup** — visible immediately in Cloud Run's own
   deploy/revision logs — instead of silently deploying "healthy" and only
   failing on a real user's first upload. Startup log line extended to show
   `STORAGE_BACKEND`, `GCS_BUCKET_NAME` (or `(unset)`), the DB dialect
   (unchanged, still never the connection string itself), and the fully
   parsed CORS origin list — none of these are secrets (a bucket name and
   public frontend URLs), so nothing sensitive is newly exposed in logs.

3. **`.github/workflows/deploy.yml`** — backend deploy step changed from
   `--set-env-vars` to `--update-env-vars` for the same four keys. This is a
   pure resilience fix: it merges into whatever the previous revision had
   instead of replacing it wholesale, so `ALLOWED_ORIGINS` (set by the later
   "Lock CORS" step) survives every subsequent backend-only redeploy instead
   of being wiped and needing the full pipeline to re-run cleanly to come
   back. No behavior change on a service's very first deploy (nothing to
   merge with yet).

**Tradeoff to be aware of:** change #2 means a backend deploy with a genuinely
missing `GCS_BUCKET_NAME` secret will now **fail its own Cloud Run health
check** (revision never becomes serving) instead of deploying "successfully"
while silently broken. This is the correct fail-fast behavior the task asked
for, but it means: until the `GCS_BUCKET_NAME` GitHub secret is confirmed set
(§ Issue 1), the next CI run may report the backend deploy step as failed
rather than the previous silent 500-on-upload. That is the fix working as
intended, not a regression — go verify the secret, don't revert this.

Verified after these changes: `pytest tests/` — **927 passed, 10 deselected**
(unchanged from before; test suite always runs with `STORAGE_BACKEND=local`,
so the new eager storage check never fires in tests, and no test asserted on
`allowed_origins_list`'s old exact return shape in a way the JSON-array
addition could break).

## Exact configuration required in Cloud Run

Environment variable **names** the backend actually reads (must match
exactly, case-sensitive by convention even though pydantic-settings itself is
case-insensitive — keep them upper-case for consistency with everything else
here):

| Required name | NOT this | Set via |
|---|---|---|
| `ALLOWED_ORIGINS` | ~~`CORS_ORIGINS`~~ | `deploy.yml`'s "Lock backend CORS" step (already correct) |
| `GCS_BUCKET_NAME` | — | `deploy.yml`, from GitHub secret `GCS_BUCKET_NAME` — **confirm this secret is actually set** |
| `STORAGE_BACKEND` | — | `deploy.yml`, hardcoded to `gcs` for production |
| `GOOGLE_CLOUD_PROJECT` | — | `deploy.yml`, from the workflow's own `$PROJECT_ID` |

`ALLOWED_ORIGINS` value in production should be:
```
http://localhost:5173,https://frontend-1069915675190.asia-south1.run.app
```
(or the JSON-array equivalent — both now work identically).

To check what's actually live right now:
```
gcloud run services describe backend --region asia-south1 \
  --format="value(spec.template.spec.containers[0].env)"
```
Look specifically for `GCS_BUCKET_NAME` (must be non-empty) and
`ALLOWED_ORIGINS` (must contain the frontend's exact URL, not `CORS_ORIGINS`).

## Remaining manual step

Confirm the `GCS_BUCKET_NAME` GitHub Actions secret is set to the real bucket
name (`resume_bucket08`). This audit could not check GitHub secret values
(no access) — it's the most likely single root cause of Issue 1 given the code
itself is correct and unchanged in this area.
