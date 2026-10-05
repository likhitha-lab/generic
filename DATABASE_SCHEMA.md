# Database Schema

Defined via SQLAlchemy 2.0 ORM models in `backend/app/models/`. The same models work against SQLite (local dev, zero setup) or Azure SQL (`mssql+pyodbc`) — the dialect is picked entirely by `DATABASE_URL`, nothing in the models or queries is database-specific. Tables are created automatically at startup by `backend/app/database/init_db.py` (`Base.metadata.create_all()`); there is no separate migration tool wired up yet (see "Future work" below).

## Entity-relationship overview

```
roles                users                  resumes                resume_versions        audit_logs
┌──────────┐   1   ┌──────────────┐   1   ┌──────────────────┐  1 ┌────────────────────┐  ┌──────────────┐
│ id (PK)  │───┐   │ id (PK)      │───┐   │ id (PK)          │──┐ │ id (PK)             │  │ id (PK)      │
│ name     │   │   │ email        │   │   │ user_id (FK)     │  │ │ resume_id (FK)      │  │ user_id (FK) │
└──────────┘   │   │ hashed_pw    │   │   │ title            │  └─│ version_number      │  │ action       │
               │   │ full_name    │   │   │ source_type      │    │ tone                │  │ entity_type  │
               │   │ is_active    │   │   │ source_input     │    │ content_json        │  │ entity_id    │
               └──▶│ role_id (FK) │   └───▶│ is_deleted       │    │ original_blob_path  │  │ details      │
                   │ created_at   │        │ deleted_at       │    │ json_blob_path      │  │ ip_address   │
                   └──────────────┘        │ created_at       │    │ pdf_blob_path       │  │ created_at   │
                                            │ updated_at       │    │ docx_blob_path      │  └──────────────┘
                                            └──────────────────┘    │ created_by_id (FK)  │
                                                                     │ created_at          │
                                                                     └────────────────────┘
```

One `Role` has many `User`s. One `User` has many `Resume`s. One `Resume` has many `ResumeVersion`s (this is the versioning mechanism — see below). `AuditLog` references `User` but stands alone; it is never joined against for normal app reads, only for the admin activity feed.

## Tables

### `roles`

Lookup table so role names are consistent and new roles can be added without a migration on every user row.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | VARCHAR(32), unique | `"admin"` or `"user"` — see `Role.ADMIN` / `Role.USER` constants |

Seeded once at startup (`init_db.py`) if not already present.

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `email` | VARCHAR(255), unique, indexed | Login identifier |
| `hashed_password` | VARCHAR(255) | bcrypt hash via passlib — plaintext is never stored or logged |
| `full_name` | VARCHAR(255) | |
| `is_active` | BOOLEAN, default true | Deactivated users can no longer log in (`403` on `/auth/login`); admin-only toggle |
| `role_id` | INTEGER FK → `roles.id` | |
| `created_at` | DATETIME (UTC) | |

Registration (`POST /api/auth/register`) always assigns the `user` role — there is no client-controllable way to self-promote to `admin`. The only admin account created automatically is the one seeded from `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` at first startup, and only if no admin exists yet.

### `resumes`

The parent entity for one resume a user is building/maintaining. A user can have many of these (e.g. "Software Engineer Resume", "PM Resume").

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER FK → `users.id`, indexed | Owner |
| `title` | VARCHAR(255) | Derived from the candidate's name at creation time |
| `source_type` | VARCHAR(16) | `"upload"` or `"manual"` |
| `source_input` | TEXT | The raw material needed to regenerate this resume later: extracted text for uploads, the submitted form JSON for manual entries. Stored so `/regenerate` never needs the client to resend the original input |
| `is_deleted` | BOOLEAN, default false | Soft delete — see below |
| `deleted_at` | DATETIME, nullable | Set when soft-deleted |
| `created_at` / `updated_at` | DATETIME (UTC) | `updated_at` bumps on every new version |

**Soft delete, not hard delete.** `DELETE /api/resumes/{id}` and the admin equivalent set `is_deleted=true` / `deleted_at=now()` rather than removing the row. All read queries filter `is_deleted.is_(False)`. This preserves the audit trail and blob files, and means a delete can be reversed by a direct DB update if ever needed — the tradeoff is that storage isn't automatically reclaimed (a background cleanup job is a documented future improvement, not implemented here).

### `resume_versions`

One generated snapshot of a `Resume`. **This is the versioning mechanism**: every call to generate, upload, or regenerate creates a new row here with `version_number = previous max + 1` under the *same* `Resume`, rather than mutating an existing row or creating a new top-level resume. Nothing is ever overwritten, so the full generation history is always available.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `resume_id` | INTEGER FK → `resumes.id`, indexed | |
| `version_number` | INTEGER | 1, 2, 3, ... per resume |
| `tone` | VARCHAR(32) | e.g. "Experienced", "Executive" — whatever tone was requested for this generation |
| `content_json` | TEXT | The full structured resume content (matches `ResumeContent` in `app/schemas/resume.py`), duplicated here from blob storage so JSON/history reads are a single DB query, not a storage round trip |
| `original_blob_path` | VARCHAR(512), nullable | Path to the originally uploaded file, if `source_type="upload"` |
| `json_blob_path` | VARCHAR(512) | Path to the JSON blob (redundant with `content_json` but kept for parity/auditability) |
| `pdf_blob_path` | VARCHAR(512) | |
| `docx_blob_path` | VARCHAR(512) | |
| `created_by_id` | INTEGER FK → `users.id` | Normally equals the resume's owner; kept separate from `resumes.user_id` so an admin-triggered regeneration (future feature) would be attributable |
| `created_at` | DATETIME (UTC) | |

Blob paths follow the pattern `users/{user_id}/resumes/{resume_id}/versions/{version_number}/{filename}` (see `app/utils/files.py`), so every file's location is derivable and collision-free without needing a naming registry.

### `audit_logs`

Append-only record of security- and data-relevant actions (register, login, resume create/download/regenerate/delete, admin actions). Deliberately a single wide table rather than normalized further — audit logs are written far more than queried, and the query pattern is almost always "recent activity" or "everything for this entity", both served fine by the indexes below.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER FK → `users.id`, nullable | Null for admin actions taken against another user's resource (see `routers/admin.py`, which logs `resume_owner_id` in `details` instead) |
| `action` | VARCHAR(64) | e.g. `REGISTER`, `LOGIN`, `RESUME_CREATE`, `RESUME_DOWNLOAD`, `RESUME_REGENERATE`, `RESUME_DELETE`, `ADMIN_DOWNLOAD`, `ADMIN_DELETE_RESUME` |
| `entity_type` | VARCHAR(32), nullable, indexed | e.g. `"resume"`, `"resume_version"`, `"user"` |
| `entity_id` | INTEGER, nullable, indexed | |
| `details` | TEXT (JSON), nullable | Free-form context, e.g. `{"format": "pdf"}` |
| `ip_address` | VARCHAR(64), nullable | From the request's client host |
| `created_at` | DATETIME (UTC) | |

Audit log writes are wrapped so a failure to log never breaks the parent request (`app/utils/audit.py` catches and rolls back independently).

## Why this shape

- **Resume vs. ResumeVersion is the crux of the schema.** Every other design considered (e.g. a single mutable `resumes` table with a `content_json` column overwritten each time) would lose history on regenerate, which the requirements explicitly call out as unacceptable ("every generation creates a new version, preserving history").
- **Role as a lookup table, not a string column or enum on `users`,** so RBAC can be extended (e.g. adding a `reviewer` role) without a schema migration touching every existing user row — just an insert into `roles`.
- **`source_input` on `Resume`, not `ResumeVersion`.** The original input (uploaded text or manual form data) doesn't change between regenerations of the same resume, so it lives once on the parent rather than being duplicated into every version.
- **Soft delete everywhere** user-facing data can be removed, so audit history stays intact and accidental deletes are recoverable at the database level.

## Future work (not implemented)

- A real migration tool (Alembic) — currently `create_all()` only handles net-new tables, not altering existing ones. Fine for this project's current single-version schema; would need to be added before the schema changes again post-deployment.
- A background job to hard-delete blob files for resumes that have been soft-deleted past some retention window.
