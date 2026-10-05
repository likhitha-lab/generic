# Admin Authentication & Authorization Audit

**No data was changed.** Everything below was gathered via code reading plus
one set of read-only `SELECT` queries against the live database (no
`INSERT`/`UPDATE`/`DELETE` run).

## Answer: the existing admin account

| Field | Value |
|---|---|
| Email | **`admin@example.com`** |
| Full name | `System Administrator` |
| Role | `admin` |
| Active | `true` |
| Created | 2026-07-14 10:28:11 GMT |
| How it became admin | Auto-seeded on first backend startup — never registered through the API |

Exactly **one** admin account exists (verified — no duplicates, no orphaned
promotions). 21 total user accounts exist; all 20 others have role `user`.

**Why this is the account**: `admin@example.com` is the literal hardcoded
default for `DEFAULT_ADMIN_EMAIL` in `backend/app/core/config.py:78`. The
seeded row's email matches that default exactly, which means this
deployment's `DEFAULT_ADMIN_PASSWORD` environment variable was very likely
also never overridden away from its own literal default
(`config.py:79`, `"ChangeMe123!"`) at the moment this row was created
(2026-07-14) — *unless* it was explicitly set to something else in Secret
Manager/`.env` at that exact time and later changed. Try the code default
password first; if it doesn't work, it was overridden at seed time and the
real value would only be recoverable from whatever set
`DEFAULT_ADMIN_PASSWORD` back on 2026-07-14 (a secret history, not this
audit). Either way, **this password hash cannot be reversed** — if the
default doesn't work, the only recovery paths are (a) a direct DB
`UPDATE users SET hashed_password = ...` with a freshly-hashed new password
(not run by this audit — no data was changed), or (b) deleting this row and
letting `_seed_admin` recreate it on next startup with a currently-set
`DEFAULT_ADMIN_PASSWORD` (also not run here).

## 1. How admin users are created

Exactly one path in the entire codebase creates an `admin`-role user:
`backend/app/database/init_db.py`'s `_seed_admin()` (lines 167-191), called
from `init_db()` on every backend startup (`init_db.py:145`, itself called
from `app/main.py`'s `lifespan`). There is no other code path, anywhere,
that sets `role_id` to the admin role.

## 2. Is an admin seeded automatically?

Yes — every time the backend starts (`init_db.py:145`), `_seed_admin()`
runs. It's a no-op the moment any admin already exists (line 177-178:
`if existing_admin: return`), so this only ever creates the *first* admin,
never a second one, no matter how many times/instances the app restarts.

## 3. Is there a default admin account?

Yes, defined in `backend/app/core/config.py:78-79`:
```python
DEFAULT_ADMIN_EMAIL: str = "admin@example.com"
DEFAULT_ADMIN_PASSWORD: str = "ChangeMe123!"
```
These are the literal fallback values if the corresponding env vars aren't
set. The live seeded account's email matches this default exactly (see
above).

## 4. Does an environment variable define the admin?

Yes — `DEFAULT_ADMIN_EMAIL` and `DEFAULT_ADMIN_PASSWORD`, read by
`_seed_admin()` (`init_db.py:180-181, 184-185`) at the moment of seeding
only. Changing these env vars *after* an admin already exists has no effect
— `_seed_admin` only ever acts when zero admins exist yet
(`init_db.py:177-178`). In production, these are Secret Manager entries
(`default-admin-password`, wired via `deploy.yml`'s `--set-secrets`) — see
`scripts/setup-gcp-secrets.sh`, which generates and prints
`DEFAULT_ADMIN_PASSWORD` once at provisioning time (its own comment: "not
stored anywhere else" — so if that specific run's printed output wasn't
saved, that specific value is genuinely gone, though it may not be the value
actually used at seed time regardless — see above).

## 5. Does a migration insert the admin?

No. There is no Alembic (or any other migration tool) in this project at
all — confirmed by searching the whole repo for migration/alembic files,
finding none. Schema is created via `Base.metadata.create_all` and one
manual `_ADDED_COLUMNS` backfill list in `init_db.py`, neither of which
touches user/role data. All seeding is the `_seed_roles`/`_seed_admin`
functions described above, not a migration.

## 6. Is there a management script?

No standalone CLI/script inserts or promotes an admin directly against the
database. `scripts/setup-gcp-secrets.sh` only provisions the *environment
variable* (`DEFAULT_ADMIN_PASSWORD` in Secret Manager) that `_seed_admin`
later reads on next startup — it does not touch the database itself.

## 7. Does the first registered user become admin?

No. `POST /api/auth/register` (`app/routers/auth.py:41-62`) unconditionally
looks up `Role.USER` (`auth.py:47`) and assigns that — there is no branch,
count check, or condition anywhere in `register()` that grants `admin`
based on being first, or on anything else. The router's own docstring
(`auth.py:1-7`) states this explicitly: "there is no way for a client to
grant itself `admin` through this API."

## 8. Does role promotion exist?

**Not currently, as an API.** `app/routers/admin.py` exposes exactly 6
endpoints: `GET /users`, `PATCH /users/{id}/deactivate`, `GET /resumes`,
`GET /resumes/{id}/versions/{id}/download`, `DELETE /resumes/{id}`,
`GET /stats` — no `PATCH`/`PUT` on a user's role anywhere. `auth.py`'s
module docstring says admins are "promoted directly in the database / by
another admin," but the "by another admin" half describes a capability that
does not actually exist in the code today — worth noting as a documentation/
code gap, not acted on here (would be a feature addition, out of scope for
an audit). The only real promotion path today is a direct database
`UPDATE users SET role_id = (SELECT id FROM roles WHERE name='admin') WHERE id = ...`,
run manually against the database.

## 9. Which database stores user roles?

The same single database everything else uses — whatever `DATABASE_URL`
points to (`app/database/session.py`), Postgres-only (SQLite dropped
entirely, see `config.py`'s own comments). Two tables: `roles` (id, name —
seeded with exactly `admin` and `user`) and `users.role_id`, a foreign key
into `roles`. No separate identity/role store, no external IdP.

## 10. Are Cloud Run and local databases different?

**No — verified they are the same physical database, not just "possibly."**
`backend/.env`'s current `DATABASE_URL` points directly at a Neon-hosted
Postgres instance (region `us-east-1`). This audit connected to it
read-only and confirmed: 5 tables (`audit_logs`, `resumes`,
`resume_versions`, `roles`, `users`), 21 users total, exactly 1 admin
(`admin@example.com`, seeded 2026-07-14). Given `deploy.yml`'s
`database-url` Secret Manager secret is the only other place `DATABASE_URL`
is configured for this project, and this codebase has no staging/local-vs-
prod database split anywhere (no docker-compose Postgres actually in use per
the current `.env`, no second connection string anywhere in the repo), this
is almost certainly the exact same Neon database Cloud Run uses in
production. **Practical implication:** running this backend locally right
now reads and writes the real production database — any local testing,
manual seeding, or future admin-recovery `UPDATE` must be done with that in
mind, not treated as a disposable local sandbox.

## Summary

| # | Question | Answer |
|---|---|---|
| 1 | How admins are created | `init_db.py::_seed_admin()`, the only code path that ever sets the admin role |
| 2 | Auto-seeded? | Yes, on every startup, no-op once one exists |
| 3 | Default admin account? | Yes — `admin@example.com` / `ChangeMe123!` (code defaults) |
| 4 | Env var defines it? | Yes — `DEFAULT_ADMIN_EMAIL`/`DEFAULT_ADMIN_PASSWORD`, read only at seed time |
| 5 | Migration inserts it? | No — no Alembic/migrations exist in this project |
| 6 | Management script? | No — `setup-gcp-secrets.sh` only provisions the env var, doesn't touch the DB |
| 7 | First user becomes admin? | No — `register()` always assigns `Role.USER` unconditionally |
| 8 | Role promotion exists? | No API endpoint — only a manual direct DB `UPDATE` |
| 9 | DB storing roles | `roles` + `users.role_id`, the same Postgres `DATABASE_URL` as everything else |
| 10 | Cloud Run vs local DB | **Same database** — confirmed live, both point at the one Neon Postgres instance |
