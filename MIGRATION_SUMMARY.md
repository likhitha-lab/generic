# Migration Summary

Source projects: `ai-resume-architect` (Project 1, current GitHub repo), `ai-resume-builder-frontend-main` (Project 2), `ai-resume-builder-backend-main` (Project 3). None of the three original folders were modified — everything below was built fresh into `ResumeBuilder/`.

Full rationale for every architectural decision is in `ResumeBuilder_Architecture_Report.md` (produced before this build). This file is the concrete file-by-file accounting.

## Files copied (logic kept as-is or near-verbatim)

| New location | Origin | Notes |
|---|---|---|
| `backend/static/logo.png` | Project 3 | Unchanged |
| `backend/app/services/normalization.py` | Project 3 `main.py` (`clean_name`, `normalize_parsed`, `enforce_limits`) | Logic unchanged, moved into its own module |
| `backend/app/services/extraction.py` | Project 3 `main.py` (`extract_pdf_data`, `extract_docx_data`) | Logic unchanged, renamed for clarity |
| `backend/app/services/gemini_client.py` | Project 3 `main.py` (`call_gemini`) | Logic unchanged; client creation made lazy so `/health` doesn't crash without a key |
| `frontend/nginx.conf` | Project 2 | Unchanged |
| `frontend/src/components/upload/UploadResume.tsx` | Project 2 | Drag/drop UI and Motion animations kept; only the API call was changed |
| `frontend/src/components/preview/ResumePreview.tsx` | Project 2 | Layout kept; added a contact-info line |
| `frontend/src/App.tsx` (nav/hero/footer) | Project 2 | Structure kept; added the upload/manual tab switcher |

## Files rewritten (same purpose, restructured or fixed)

| New file(s) | Replaces | What changed and why |
|---|---|---|
| `backend/app/main.py`, `routers/generate.py`, `routers/convert.py`, `core/config.py`, `core/security.py`, `core/limiter.py`, `models/schemas.py`, `services/file_generator.py`, `services/prompts.py` | Project 3's single `main.py` (330 lines) | Split into routers/services/models/core so each concern is testable independently. Google Drive upload replaced with in-memory PDF/DOCX bytes returned as base64 (no more PII landing in the developer's personal Drive, no more service-account/Drive-API dependency). CORS changed from `allow_origins=["*"]` + `allow_credentials=True` to an explicit `ALLOWED_ORIGINS` allowlist with credentials off. Added `x-api-key` auth and per-IP rate limiting (both previously absent). The `tone` field is now actually used in the Gemini prompt (previously collected and silently ignored). Contact fields (`email`/`phone`/`location`/`links`) were added end-to-end — request schema, both Gemini prompts, both file generators, and the preview response — closing the "resume has no contact info" gap. Fixed the `/convert` temp-file leak (files are now always removed in a `finally` block). |
| `backend/requirements.txt` | Project 3's unpinned `requirements.txt` | Pinned to compatible-release versions; dropped `google-api-python-client`/`google-auth` (only needed for the removed Drive upload); added `pydantic-settings`, `slowapi` |
| `frontend/package.json` | Project 2's `package.json` | Removed unused dependencies (`express`, `dotenv`, `pdfkit`, `mammoth`, `docx`, `@google/genai`, `tsx`) that were never imported by reachable code; fixed the `dev` script, which pointed at a nonexistent `server.ts` |
| `frontend/vite.config.ts` | Project 2's `vite.config.ts` | Removed the `process.env.GEMINI_API_KEY` define block — nothing calls Gemini from the browser anymore |
| `frontend/src/components/upload/DownloadButtons.tsx` | Project 2's `DownloadButtons.tsx` | Downloads a base64 blob via `downloadBase64File()` instead of `window.open(driveLink)` — there is no Drive link anymore |

## Brand-new files (functionality that did not exist before)

| File | Purpose |
|---|---|
| `frontend/src/components/form/ResumeForm.tsx` | Manual resume-entry form with multi-entry education/experience/projects, contact fields, and a tone selector. The only prior attempt at this (Project 1's `ResumeForm.tsx`) supported a single entry per section and was never wired to a shared design system — this is a ground-up rebuild in Project 2's Tailwind style |
| `frontend/src/api/client.ts` | Single source of truth for backend calls (env-driven `VITE_API_URL`), replacing three inconsistent, mostly-dead call sites across the original projects |
| `frontend/src/lib/downloadFile.ts` | Converts a base64 payload into a real browser download |
| `frontend/src/types/resume.ts` | Shared TypeScript types mirroring the backend's Pydantic schemas |
| `backend/app/services/prompts.py` | Prompt templates, now parameterized by `tone` and extended to request contact fields during `/convert` |
| `backend/app/core/config.py`, `core/security.py`, `core/limiter.py` | Settings, API-key auth dependency, rate limiter |
| `deployment/deploy-backend.sh`, `deploy-frontend.sh`, `cloudbuild-frontend.yaml` | Cloud Run deploy scripts (none of the three projects had any deploy automation) |
| `docker-compose.yml` (root) | Local dev orchestration for both services together |
| `README.md`, `MIGRATION_SUMMARY.md` | This documentation |

## Files deleted (not carried into ResumeBuilder)

- **All of Project 1** (`ai-resume-architect`): `project/`, `frontend-server/`, `frontend_final/`, and the empty `project/backend/`. Reasons: broken Python Dockerfile inside a Node folder, dependencies only resolvable inside AI Studio's runtime, three different hardcoded backend URLs across the repo, and a proxy (`frontend-server/server.js`) that checks an `x-api-key` header the real backend never validated.
- **`backend/test_gemini.py`** — contained a hardcoded, live-looking Gemini API key. Not carried over under any circumstance. **You should rotate this key in Google AI Studio / Cloud Console regardless of this migration**, in case it was ever pushed to a public or shared repo.
- **`frontend/src/services/geminiService.ts`** — dead client-side Gemini integration (never imported), and exactly the anti-pattern of calling Gemini from the browser that you asked to eliminate.
- **`frontend/src/api.ts`** (old version) — dead, unused axios module; superseded by the real upload flow that bypassed it. Replaced by `src/api/client.ts`.
- **`frontend/__pycache__/main.cpython-312.pyc`** — orphaned compiled-Python artifact with no matching source anywhere in the frontend repo.
- **`upload_to_drive()`** and its Google Drive/service-account dependency — replaced by direct base64 file responses (see rewrite table above).

## Remaining TODOs (things I could not do or that need your input)

1. **Rotate the exposed Gemini API key** from the original `test_gemini.py`. I can't do this myself — it requires access to your Google AI Studio / Cloud Console account.
2. **Create the two Cloud Run secrets** (`gemini-api-key`, `resumebuilder-api-key`) and actually run `deployment/deploy-backend.sh` / `deploy-frontend.sh`. I don't have your GCP credentials, so deployment itself needs to happen on your machine — the scripts and steps are in the README.
3. **Delete `frontend/node_modules`** before committing this project. I ran a build validation that installed dependencies directly in this folder, and a handful of npm's temporary staging directories got stuck due to a filesystem-caching quirk in this environment (harmless — `node_modules` is git-ignored either way, and a normal `npm install` on your machine will just overwrite it cleanly).
4. **No automated tests exist** — none existed in any of the three original projects either. I'd recommend starting with backend unit tests for `services/normalization.py` and `services/file_generator.py` (pure functions, easy to test) before adding anything else.
5. **Rate limiting is in-memory** (slowapi's default). Fine for a single Cloud Run instance; if you ever scale to multiple instances, limits won't be shared across them without a backing store like Redis.
6. **No persistent storage** — the app is fully stateless today, matching the original behavior minus the Drive upload. If you later want "resume history," that's a new feature requiring auth and a database, not just a wiring change.
7. **Docker images were not built/run inside this sandbox** (no Docker available here) — I validated the backend by importing it directly and the frontend by running `tsc --noEmit` and `vite build` natively. Run `docker compose up --build` once locally before your first real deploy as a final sanity check.

## Validation performed

- Backend: installed `requirements.txt` into a clean virtualenv, imported `app.main`, and confirmed all expected routes register (`/api/generate`, `/api/convert`, `/health`, `/`).
- Frontend: ran `npm install`, `tsc --noEmit` (zero errors), and `vite build` (succeeded, output in `dist/`).
