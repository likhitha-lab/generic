# Deployment Gemini Audit — `400 INVALID_ARGUMENT: API key not valid`

**No code was changed for this audit, per instructions.** Every code path
below was traced to confirm or rule it out; the remaining candidates are
external to the codebase (the actual live secret value) and need one of the
verification commands in §10 run against the real deployment to pick between
them.

## 1. How `GEMINI_API_KEY` is loaded

`backend/app/core/config.py:16`:
```python
GEMINI_API_KEY: str = ""
```
Plain `pydantic-settings` field. Locally it's populated from
`backend/.env` (`model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", ...)`,
`config.py:132`); in any other environment (Cloud Run included) from the
process environment variable of the same name. No transformation,
stripping, or validation is applied to the raw string in either case.

## 2. Is Cloud Run actually receiving the variable?

Yes — and this can be proven from the symptom itself, not just inspection.
`gemini_client.py:111-115`:
```python
if not settings.GEMINI_API_KEY:
    raise GeminiUnavailableError(
        "GEMINI_API_KEY is not set. Configure it in backend/.env (local) "
        "or as a Cloud Run secret (production)."
    )
```
This is a distinct, own-code error message ("GEMINI_API_KEY is not set") that
would appear in the logs instead of Gemini's own `400 INVALID_ARGUMENT / API
key not valid` if the variable were empty or missing. Since the logs show
Gemini's error, not this one, `settings.GEMINI_API_KEY` is definitely
**non-empty** on Cloud Run — the variable is present. What's live is a
*wrong-looking-valid-to-the-code* value, not a missing one.

## 3. Is Secret Manager being used correctly?

Yes. `.github/workflows/deploy.yml:149`:
```
--set-secrets "GEMINI_API_KEY=gemini-api-key:latest,JWT_SECRET_KEY=jwt-secret-key:latest,DATABASE_URL=database-url:latest,DEFAULT_ADMIN_PASSWORD=default-admin-password:latest"
```
Correct `ENV_VAR=secret-name:version` syntax, matches the field name exactly.
Confirmed working at the IAM level too: `DATABASE_URL` and `JWT_SECRET_KEY`
are resolved via this exact same mechanism, on the exact same service
account, and the app demonstrably boots, serves `/api/auth/*`, and connects
to Postgres successfully — so `roles/secretmanager.secretAccessor` is
correctly bound and Secret Manager resolution itself works. Only the
*content* of the `gemini-api-key` secret specifically is in question.

## 4. `GOOGLE_API_KEY` vs `GEMINI_API_KEY` mismatch?

No — ruled out by reading the installed SDK source directly
(`google/genai/_api_client.py:230-233`):
```python
env_api_key = os.environ.get('GOOGLE_API_KEY', None)
...
self.api_key = api_key or env_api_key
```
The explicit `api_key` constructor argument always wins over the
`GOOGLE_API_KEY` environment variable whenever it's non-empty. This app's
only client-construction call site,
`gemini_client.py:116` — `genai.Client(api_key=settings.GEMINI_API_KEY)` —
always passes a non-empty value (guarded by the check in §2). So whether or
not a `GOOGLE_API_KEY` env var exists anywhere is irrelevant here; it's dead
weight either way, never consulted.

## 5. Is the google-genai SDK reading the wrong variable?

No — same evidence as #4. The SDK's own environment-variable fallback
(`GOOGLE_API_KEY`) is never reached because this codebase always supplies
`api_key=` explicitly. There is exactly one `genai.Client(` call site in the
entire backend (grepped `app/` — only `gemini_client.py:116`), so there's no
second, inconsistent init path to worry about either.

## 6. Is Vertex AI mode accidentally enabled?

No. Also confirmed from SDK source (`_api_client.py:196-202`):
```python
self.vertexai = vertexai
if self.vertexai is None:
    if os.environ.get('GOOGLE_GENAI_USE_VERTEXAI', '0').lower() in ['true', '1']:
        self.vertexai = True
```
`gemini_client.py:116` never passes `vertexai=`, so this only flips to `True`
if the env var `GOOGLE_GENAI_USE_VERTEXAI` is set to `"true"`/`"1"`. Grepped
the entire repo (`config.py`, `deploy.yml`, `.env.example`,
`DEPLOYMENT_GCP.md`, `scripts/`) — this variable is set nowhere. Vertex mode
is not enabled, locally or in the deploy pipeline. (If it *were* on, the
error would look different anyway — a project/location config error, not
`API key not valid`, since Vertex mode in API-key/express mode still uses the
key but against a different endpoint/response shape — moot here since it's
confirmed off.)

## 7. Whitespace / hidden characters in the key — **most likely root cause**

Cannot be confirmed from the codebase alone (would require reading the live
Secret Manager value, which this audit has no access to) — but it is the
single most likely explanation once #1-6 are all ruled out, and it produces
exactly this symptom: Gemini's API validates key *format* strictly, and a key
string with a trailing newline or space (which still "looks right" if you
eyeball it, or even if you `cat` it in a terminal where the trailing newline
is invisible) is rejected as `API key not valid` rather than any
whitespace-specific error.

This project's own secret-provisioning script,
`scripts/setup-gcp-secrets.sh:40`, does this correctly:
```bash
printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
```
`printf '%s'` writes no trailing newline. **If the live secret was instead
created or rotated by any other method** — `echo "$KEY" | gcloud secrets
versions add ...` (bash `echo` appends `\n` by default), pasting into the GCP
Console's "Add secret version" text box (clipboard content ending in a
newline, or the UI itself appending one), or a heredoc/interactive paste —
a trailing newline is a one-character difference invisible to casual
inspection but fatal to key validation. This exactly matches "works
locally, fails on Cloud Run": the local `.env` value was hand-typed/pasted
once directly into a plain `KEY=value` line (format visibly checked by
running the app locally against it), while the Secret Manager copy is a
second, independently-created copy of "the same" key that nothing keeps
byte-for-byte in sync with the first.

## 8. Is client initialization correct?

Yes. `gemini_client.py:101-116`:
```python
@lru_cache
def _get_client() -> genai.Client:
    ...
    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailableError(...)
    return genai.Client(api_key=settings.GEMINI_API_KEY)
```
Single call site, `@lru_cache`'d (one client for the process lifetime, not
re-created per request), explicit `api_key=` kwarg, no `vertexai`/`project`/
`location` arguments that could conflict with it. Nothing here is
Cloud-Run-specific or environment-dependent beyond the value of the setting
itself.

## 9. Do Cloud Run env vars differ from local — why does it work locally but not on Cloud Run?

Yes, by design, and that divergence is the whole story here. Locally,
`GEMINI_API_KEY` is a plaintext line in `backend/.env`, hand-edited and
implicitly "format-checked" every time the app is run against it locally.
On Cloud Run, the same-named setting resolves from a completely separate
artifact — the `gemini-api-key` Secret Manager secret — populated through a
different process (a script run once, or a manual `gcloud`/Console action)
at a different time. Nothing in this codebase or pipeline diffs the two
values or re-validates the secret's content against a known-good format at
deploy time. They are two independent copies of "the same" credential with
no sync mechanism, which is exactly the structural gap that lets them
silently diverge — a stale/rotated/mistyped/whitespace-corrupted copy in
Secret Manager, with a perfectly fine one in local `.env`, produces exactly
"works locally, 400s on Cloud Run" with zero code difference between the two
environments.

## 10. Verification commands (run these to confirm which specific cause it is)

```bash
# 1. Byte-exact length + hex dump of the live secret - reveals a trailing
#    \n (0x0a) or space (0x20) immediately if present.
gcloud secrets versions access latest --secret=gemini-api-key | wc -c
gcloud secrets versions access latest --secret=gemini-api-key | xxd | tail -3

# 2. Compare against the expected length of a real Gemini API key
#    (typically 39 chars, "AIzaSy..." prefix, no whitespace) - if wc -c
#    reports one more byte than that, there's a trailing newline.

# 3. Confirm which secret VERSION the currently-running revision actually
#    has baked in (Cloud Run resolves `:latest` at revision-creation time,
#    not live - a version added to Secret Manager after the last deploy
#    is NOT picked up until the next deploy):
gcloud run services describe backend --region asia-south1 \
  --format="value(spec.template.spec.containers[0].env)" | grep -A2 GEMINI_API_KEY
gcloud secrets versions list gemini-api-key

# 4. Sanity-test the exact live value directly against Google's API,
#    outside this app entirely, to confirm/deny it's a key-content issue:
KEY=$(gcloud secrets versions access latest --secret=gemini-api-key)
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=${KEY}" | head -20
```
If command 4 itself returns `API key not valid`, the key's content is
confirmed bad (whitespace, wrong/revoked key, or wrong project) — independent
of this app's code entirely. If it succeeds, the problem is instead a stale
secret *version* baked into the current revision (command 3) — redeploying
(or `gcloud run services update backend --region asia-south1
--update-secrets GEMINI_API_KEY=gemini-api-key:latest`) picks up the current
version.

## Recommended fix (not yet applied — pending confirmation above)

If §10's commands confirm whitespace/newline is the specific defect:
recreate the secret version with `printf '%s'` (matching
`scripts/setup-gcp-secrets.sh`'s existing, correct pattern), not `echo`:
```bash
printf '%s' "<the real key, no trailing newline>" | \
  gcloud secrets versions add gemini-api-key --data-file=-
```
then redeploy (or `gcloud run services update backend --region asia-south1
--update-secrets GEMINI_API_KEY=gemini-api-key:latest`) to pick up the new
version.

As a defensive, minimal, non-business-logic hardening (optional, not
implemented in this audit per "do not change code until confirmed"): strip
the loaded value once in `config.py` —
`GEMINI_API_KEY: str = Field(default="", ...)` with a
`@field_validator` that calls `.strip()` — so a future whitespace slip in
Secret Manager degrades gracefully instead of reproducing this exact outage
again. This is a one-line, purely-defensive change with no behavioral effect
on a correctly-formatted key; happy to add it once you confirm this is
indeed the root cause.

## Summary

| # | Question | Finding |
|---|---|---|
| 1 | How is `GEMINI_API_KEY` loaded | `config.py:16`, plain pydantic-settings field, no transformation |
| 2 | Is Cloud Run receiving it | Yes — confirmed non-empty by the *absence* of this app's own "not set" error |
| 3 | Secret Manager used correctly | Yes — same mechanism as `DATABASE_URL`/`JWT_SECRET_KEY`, both demonstrably working |
| 4 | `GOOGLE_API_KEY` vs `GEMINI_API_KEY` mismatch | No — explicit `api_key=` kwarg always wins, `GOOGLE_API_KEY` never consulted |
| 5 | SDK reading wrong variable | No — same reason as #4, single call site |
| 6 | Vertex AI mode accidentally on | No — `GOOGLE_GENAI_USE_VERTEXAI` unset everywhere in the repo |
| 7 | Whitespace/hidden characters | **Cannot confirm from code; most likely root cause** — see §10 to verify |
| 8 | Client initialization correct | Yes — `gemini_client.py:101-116`, single site, correctly guarded |
| 9 | Cloud Run env vars differ from local | Yes, structurally — two independently-maintained copies of the same credential, no sync/validation between them |
