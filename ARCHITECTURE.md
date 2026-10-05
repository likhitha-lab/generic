# Architecture

## System overview

```
                              ┌─────────────────────────┐
                              │   Azure Static Web Apps  │
                              │   (React 19 + Vite SPA)  │
                              └────────────┬─────────────┘
                                           │ HTTPS + JWT Bearer
                                           ▼
                              ┌─────────────────────────┐
                              │   Azure App Service      │
                              │   (FastAPI, Linux)       │
                              │   ┌───────────────────┐  │
                              │   │ auth / RBAC        │  │
                              │   │ routers            │  │
                              │   │ services           │  │
                              │   │ storage abstraction │  │
                              │   └───────────────────┘  │
                              └───┬───────────┬───────┬───┘
                                  │           │       │
                     managed identity   JDBC/ODBC   HTTPS
                                  │           │       │
                                  ▼           ▼       ▼
                       ┌──────────────┐ ┌───────────┐ ┌──────────────────┐
                       │ Azure Key    │ │ Azure SQL │ │ Azure Blob        │
                       │ Vault        │ │ Database  │ │ Storage           │
                       │ (secrets)    │ │ (metadata │ │ (uploads, JSON,   │
                       │              │ │ + JSON)   │ │  PDF, DOCX)       │
                       └──────────────┘ └───────────┘ └──────────────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐        ┌────────────────┐
                       │ Application Insights  │        │ Google Gemini   │
                       │ (logs, metrics,       │        │ API (content    │
                       │  request tracing)     │        │ generation)     │
                       └──────────────────────┘        └────────────────┘
```

Deployment of every one of these Azure resources is defined as Bicep (`infra/`) but **has not been executed** — see DEPLOYMENT.md for what running it actually involves. Locally, the same codebase runs with zero Azure dependencies: SQLite instead of Azure SQL, filesystem instead of Blob Storage, plain environment variables instead of Key Vault. This is a deliberate storage-abstraction pattern, not two different codebases — see "Local vs. Azure" below.

## Request flow: generating a resume

1. User logs in (`POST /api/auth/login`) → gets a JWT access + refresh token pair, stored in `localStorage` by the frontend.
2. User submits the manual-entry form or uploads a PDF/DOCX → `POST /api/resumes/generate` or `/api/resumes/upload`, with the access token on `Authorization: Bearer`.
3. `app/auth/dependencies.py` decodes the JWT, loads the `User` row, and injects it into the route.
4. `app/services/resume_service.py` orchestrates: (upload only) extract text via `pdfplumber`/`python-docx` → build a Gemini prompt → call Gemini → normalize/validate the JSON response against `ResumeContent` → render PDF (ReportLab) and DOCX (python-docx) in memory.
5. The service persists: a new `Resume` row (if this is the first generation) or reuses the existing one, plus a new `ResumeVersion` row, plus the original file (if uploaded)/JSON/PDF/DOCX bytes via the storage abstraction (`app/storage/`) — Blob Storage in Azure, local filesystem in dev.
6. Response returns `ResumeDetailOut` (resume + version metadata, not file bytes).
7. Frontend fetches the version's JSON (`GET .../json`) to render the on-screen preview, and calls the download endpoint on demand to fetch the actual PDF/DOCX bytes as a `Blob` and trigger a browser download.

Every step from 3 onward is logged to `audit_logs` (login, resume create, download, regenerate, delete) via `app/utils/audit.py`.

## Backend folder structure

```
backend/app/
├── auth/            hashing.py (bcrypt), jwt.py (create/decode tokens), dependencies.py (get_current_user)
├── core/            config.py (env-driven Settings), keyvault.py (optional secret loading), limiter.py, http.py (StreamingResponse helper)
├── database/        base.py (declarative base), session.py (engine/session factory), init_db.py (create tables + seed roles/admin)
├── middleware/       rbac.py (require_role/require_admin dependency factories)
├── models/          SQLAlchemy ORM: Role, User, Resume, ResumeVersion, AuditLog
├── schemas/          Pydantic request/response models, grouped by feature (auth, resume, resume_history, admin)
├── routers/          auth.py, resumes.py, admin.py — one file per API surface
├── services/         gemini_client.py, prompts.py, extraction.py, normalization.py, file_generator.py, resume_service.py (orchestration)
├── storage/          base.py (ABC), local.py, azure_blob.py, factory.py (picks impl via STORAGE_BACKEND)
├── utils/            audit.py (log_action), files.py (blob path helpers)
└── main.py           app factory: lifespan (keyvault + init_db), CORS, router registration
```

This mirrors the requirement for an "enterprise structure with separate folders for auth, routers, services, storage, database, models, schemas, middleware, utils" directly — each concern has exactly one home, and routers stay thin (they call into `services/` for anything beyond request validation and response shaping).

## Frontend folder structure

```
frontend/src/
├── api/              client.ts (axios instance + auto-refresh interceptor), auth.ts, resumes.ts, admin.ts, tokenStorage.ts
├── context/          AuthContext.tsx (user state, login/register/logout)
├── components/
│   ├── layout/       NavBar, ProtectedRoute (route guard, optionally admin-only)
│   ├── upload/       UploadResume, DownloadButtons
│   ├── form/         ResumeForm (manual entry)
│   └── preview/      ResumePreview
├── pages/            LoginPage, RegisterPage, BuilderPage, HistoryPage, AdminPage
├── types/            auth.ts, history.ts, admin.ts, resume.ts — mirror the backend's Pydantic schemas
└── App.tsx / main.tsx  router shell (react-router-dom) + provider wiring
```

## Security model

- **Authentication**: JWT (HS256), access (60 min) + refresh (7 days) tokens. Chosen over Azure Entra ID for this iteration because the system needs its own `users`/`roles` tables regardless (for RBAC, resume ownership, and audit trails), and a self-issued JWT keeps local development fully credential-free — no Azure AD app registration needed to run the app on a laptop. Entra ID (via MSAL + `azure-identity`) is a documented drop-in upgrade path if enterprise SSO becomes a requirement (see DEPLOYMENT.md's "Future work").
- **RBAC**: two roles, `admin` and `user`, enforced by FastAPI dependency factories (`require_role(*roles)` / `require_admin`), applied at the router level for admin routes and per-resource ownership checks for user routes.
- **Password storage**: bcrypt via passlib, never logged or returned in any response.
- **Secrets**: every credential (JWT signing key, Gemini API key, DB connection string, storage connection string, seeded admin password) is read from environment variables locally and from Azure Key Vault in production (via the App Service's system-assigned managed identity + `DefaultAzureCredential` — no Key Vault credentials are themselves stored anywhere). Nothing is hardcoded; `.env.example` files document every variable without real values.
- **Rate limiting**: `slowapi`, stricter on auth endpoints than resume-generation endpoints.
- **CORS**: explicit origin allowlist (`ALLOWED_ORIGINS`), not a wildcard, with credentials enabled only because the frontend needs to send the `Authorization` header cross-origin.
- **Input validation**: Pydantic schemas on every request body; upload endpoint restricts extension (`.pdf`/`.docx`) and size (5MB).
- **Transport**: `httpsOnly: true` on the App Service Bicep module; TLS 1.2 minimum on App Service, Storage, and SQL.

## Local vs. Azure: the storage abstraction

`app/storage/base.py` defines an abstract `BlobStorage` interface (`save`, `read`, `delete`). Two implementations exist: `LocalBlobStorage` (writes under `backend/local_data/blobs/`) and `AzureBlobStorage` (uses `azure-storage-blob`'s `BlobServiceClient`). `app/storage/factory.py` picks one based on `STORAGE_BACKEND` (`"local"` or `"azure"`). Application code never imports either implementation directly — only the abstraction — so switching environments is a one-line env var change, not a code change. The same pattern applies to the database (`DATABASE_URL` scheme picks the SQLAlchemy dialect) and to secrets (`AZURE_KEY_VAULT_URL` set or unset).

## Monitoring

`APPLICATIONINSIGHTS_CONNECTION_STRING`, when set, is intended for the standard OpenCensus/OpenTelemetry Python integration for FastAPI (auto-instrumentation is not currently wired into `main.py` — see DEPLOYMENT.md's "Future work" for the exact package and two lines of code needed). The Bicep template provisions the Application Insights resource and its Log Analytics workspace regardless, so the connection string is ready to use as soon as that instrumentation is added.

## Why not Azure Entra ID, Cosmos DB, or Functions?

These were considered and explicitly not chosen for this iteration:

- **Entra ID** over custom JWT: would remove the need to store password hashes, but requires an Azure AD tenant + app registration to run locally, which conflicts with the "must run locally without Azure credentials" requirement. Documented as a future upgrade.
- **Cosmos DB** over Azure SQL: the data is genuinely relational (users → resumes → versions, with joins for admin search/stats) — a document database would mean either denormalizing (duplicating user info onto every resume) or doing joins in application code. Azure SQL's serverless tier also auto-pauses when idle, which keeps a dev/demo environment's cost near zero, matching Blob Storage + App Service's own consumption-based pricing.
- **Azure Functions** over App Service: the backend is a single cohesive FastAPI app with shared middleware (auth, CORS, rate limiting) across every route; splitting it into per-endpoint functions would add deployment complexity without a corresponding benefit at this scale.
