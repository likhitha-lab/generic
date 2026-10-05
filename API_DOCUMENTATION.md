# API Documentation

Interactive OpenAPI docs are always available at `/docs` (Swagger UI) and `/redoc` on a running backend. This document is a narrative companion covering auth flow, error shapes, and the reasoning behind a few non-obvious design choices.

Base path for every route below is `/api` (e.g. the full path for `POST /auth/login` is `POST /api/auth/login`).

## Authentication

All endpoints except `/auth/register`, `/auth/login`, `/auth/refresh`, `/health`, and `/` require a `Bearer` access token:

```
Authorization: Bearer <access_token>
```

Tokens are JWTs (HS256, signed with `JWT_SECRET_KEY`) issued in pairs:

- **Access token** — 60 minutes by default (`ACCESS_TOKEN_EXPIRE_MINUTES`), sent on every request.
- **Refresh token** — 7 days by default (`REFRESH_TOKEN_EXPIRE_DAYS`), used only to obtain a new pair via `/auth/refresh`.

Each token carries a `type` claim (`"access"` or `"refresh"`) so a refresh token can't be used as an access token even if leaked to an endpoint expecting one. The frontend (`src/api/client.ts`) handles refresh automatically: on any `401`, it calls `/auth/refresh` once and retries the original request before giving up and logging the user out.

### `POST /auth/register`

Rate limited (`AUTH_RATE_LIMIT`, default 5/minute per IP).

Request:
```json
{ "email": "jane@example.com", "password": "at-least-8-chars", "full_name": "Jane Doe" }
```

Response `201`:
```json
{ "id": 4, "email": "jane@example.com", "full_name": "Jane Doe", "role": "user", "is_active": true, "created_at": "2026-07-06T12:00:00Z" }
```

Always creates a `user`-role account. `409` if the email is already registered.

### `POST /auth/login`

Rate limited.

Request: `{ "email": "...", "password": "..." }`

Response `200`:
```json
{ "access_token": "...", "refresh_token": "...", "token_type": "bearer" }
```

`401` for wrong email *or* wrong password (deliberately identical message, to avoid leaking which emails are registered). `403` if the account has been deactivated by an admin.

### `POST /auth/refresh`

Request: `{ "refresh_token": "..." }` → same response shape as login. `401` if the token is invalid, expired, or not actually a refresh token.

### `GET /auth/me`

Returns the current user (same shape as register's response). Useful for restoring session state on page load.

## Resumes (own resumes only)

Every route below requires a valid access token and only ever operates on resumes owned by that token's user — attempting to access another user's resume returns `404` (not `403`), so ownership can't be probed by status code.

### `POST /resumes/generate`

Rate limited (`RATE_LIMIT`, default 10/minute). Calls Gemini. Builds a resume from manually entered structured data.

Request body (`ResumeRequest`):
```json
{
  "name": "Jane Doe",
  "email": "jane@example.com",
  "phone": "+1 555 000 1234",
  "location": "San Francisco, CA",
  "links": "linkedin.com/in/janedoe",
  "tone": "Experienced",
  "skills": ["React", "TypeScript"],
  "tools": ["Docker", "Git"],
  "certifications": ["AWS Certified Solutions Architect"],
  "education": [{ "degree": "B.S. Computer Science", "institution": "UT Austin", "year": "2019" }],
  "experience": [{ "company": "Acme", "role": "Engineer", "duration": "2020-Present", "points": ["Shipped X", "Led Y"] }],
  "projects": [{ "title": "Side Project", "description": "...", "tools": "Next.js" }]
}
```

Response `201` (`ResumeDetailOut` — see shape below). Creates a new `Resume` + its first `ResumeVersion`.

### `POST /resumes/upload`

Rate limited. Calls Gemini. `multipart/form-data` with a `file` field (PDF or DOCX, 5MB max). Extracts text server-side, then generates the same way as `/generate`.

`400` for unsupported file types, `413` over the size limit, `422` if text extraction fails (e.g. a scanned/image-only PDF with no extractable text).

### `GET /resumes`

Lists the caller's non-deleted resumes, most recently updated first. Returns `ResumeSummaryOut[]`:
```json
[{
  "id": 12, "title": "Jane Doe", "source_type": "manual",
  "created_at": "...", "updated_at": "...",
  "latest_version": { "id": 30, "version_number": 2, "tone": "Experienced", "created_at": "...", "created_by_id": 4 },
  "version_count": 2
}]
```

### `GET /resumes/{resume_id}`

Full detail including every version (`ResumeDetailOut` = `ResumeSummaryOut` + `versions: ResumeVersionOut[]`).

### `GET /resumes/{resume_id}/versions/{version_id}/json`

Returns the structured content generated for that specific version — `ResumeVersionOut` fields plus `content` (the full `ResumeContent` object: summary, skills, experience, education, projects, certifications, etc).

### `GET /resumes/{resume_id}/versions/{version_id}/download?format=pdf|docx`

Streams the actual file (`application/pdf` or the DOCX content type) with `Content-Disposition: attachment; filename="Title_v2.pdf"`. No Gemini call — this reads bytes already generated and persisted at creation/regeneration time.

### `POST /resumes/{resume_id}/regenerate`

Rate limited. Calls Gemini. Body: `{ "tone": "Executive" }` (tone optional — omit to reuse the resume's existing tone). Reconstructs the original input (uploaded text or manual form data, from `resumes.source_input`) and re-runs generation, creating a new `ResumeVersion` under the same `Resume` (`version_number` incremented). Returns the updated `ResumeDetailOut` with all versions, old and new.

### `DELETE /resumes/{resume_id}`

`204` on success. Soft-deletes the resume (see DATABASE_SCHEMA.md) — it disappears from `GET /resumes` immediately but the row and its blobs are retained.

## Admin (requires the `admin` role)

Every route below is gated by `require_admin` at the router level — a `user`-role token gets `403` on all of them, regardless of whose data is being accessed.

### `GET /admin/users`

All users, most recently created first, each annotated with `resume_count` (their non-deleted resume count).

### `PATCH /admin/users/{user_id}/deactivate`

Sets `is_active=false`. That user's existing tokens still decode successfully (JWTs aren't revocable without a blocklist, which isn't implemented) but `/auth/login` will reject them going forward, and a documented enhancement would be to also check `is_active` in `get_current_user` for immediate effect — currently deactivation takes effect on next login/refresh cycle.

### `GET /admin/resumes?search=`

Every non-deleted resume across all users, with `owner_id`/`owner_email` attached. `search` matches (case-insensitive) against resume title or owner email.

### `GET /admin/resumes/{resume_id}/versions/{version_id}/download?format=pdf|docx`

Same as the user-facing download route, but with no ownership check — any admin can download any user's file.

### `DELETE /admin/resumes/{resume_id}`

Soft-deletes any user's resume.

### `GET /admin/stats`

```json
{
  "total_users": 42, "total_admins": 2, "total_regular_users": 40,
  "total_resumes": 130, "total_versions": 210,
  "resumes_created_last_7_days": 18, "resumes_created_today": 3,
  "recent_actions": [{ "action": "RESUME_CREATE", "entity_type": "resume", "entity_id": 130, "user_id": 40, "created_at": "..." }]
}
```

`recent_actions` is the last 20 rows from `audit_logs`, most recent first.

## Meta

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check (used by App Service / docker-compose) |
| `GET` | `/` | Service identity + link to `/docs` |
| `GET` | `/docs` | Swagger UI |

## Error shape

All errors follow FastAPI's default `HTTPException` shape:

```json
{ "detail": "Human-readable message" }
```

Validation errors (`422`) use FastAPI/Pydantic's default shape with a `detail` array describing each invalid field.

## Rate limiting

Enforced per client IP via `slowapi`:

- Auth endpoints (`register`, `login`, `refresh`): `AUTH_RATE_LIMIT`, default `5/minute` — deliberately stricter to slow down credential stuffing.
- Resume generation endpoints (`generate`, `upload`, `regenerate`): `RATE_LIMIT`, default `10/minute` — these are the endpoints that call the (paid) Gemini API.

Exceeding the limit returns `429` with a `Retry-After` header.
