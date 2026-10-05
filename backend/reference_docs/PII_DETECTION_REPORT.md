# PII Detection Layer — Implementation Report

Detection only. No masking, no replacement, no rewritten content anywhere — verified by grep,
`pii_detector.py` is not called from any pipeline stage yet (`resume_service.py`,
`extraction_pipeline.py`, `resume_optimizer.py` are all untouched). It exists as a standalone,
reusable module ready to be wired in once masking is approved.

## What was built

`app/services/pii_detector.py` — two entry points, matching the two detection tiers already laid
out in `PII_MASKING_ARCHITECTURE.md`:

- **`detect_pii_in_text(raw_text)`** — runs on raw source text, before Stage 1 extraction (i.e.
  before the first Gemini call). Reuses existing local logic rather than reimplementing it:
  `person_extractor.resolve_candidate_name` (called with `gemini_name=None` — its own non-Gemini
  signals, the header-line scans, work unmodified) for candidate name; `app/utils/contact.py`'s
  existing email/phone/LinkedIn/GitHub regexes for contact fields.
- **`detect_pii_in_structured(structured)`** — runs on the already-extracted structured dict
  (after Stage 1, before the Optimizer's own Gemini rewrite calls). Reads `experience[].company`,
  `experience[].notes`, `projects[].title`, `projects[].client` directly — exact field reads, not
  heuristic pattern matching.
- **`merge_pii_reports(*reports)`** — combines both passes into one de-duplicated report.

### Reused vs. new

- Reused as-is: `person_extractor.resolve_candidate_name`.
- Extended (not duplicated): `app/utils/contact.py` gained `extract_all_emails`/
  `extract_all_phones`/`extract_all_linkedin_urls`/`extract_all_github_urls` — the existing
  regexes only ever returned the first match (correct for their one existing caller, a single-field
  fallback); the detector needs every occurrence, so these reuse the exact same compiled patterns
  rather than a second copy of them living in the new module.
- Genuinely new: portfolio-URL detection (any `http(s)://` link that isn't already LinkedIn/GitHub
  — no existing extractor covered this), address/PAN/Aadhaar/Passport/Employee-ID/Internal-ID/
  Client detection (none existed before).

## Precision-first design decisions

Per the explicit "maximize precision, minimize false positives" requirement:

- **Company and Project names are NOT detected in `detect_pii_in_text`** (raw-text mode) at all —
  this is a deliberate scope limit, not an oversight. Free-text company/project names have no
  reliable regex shape; a suffix heuristic ("Solutions", "Systems", "Technologies") would false-fire
  on plenty of non-company text ("AWS Solutions Architect" contains "Solutions" but isn't a
  company). These two categories are only populated by `detect_pii_in_structured`, where the
  fields are already cleanly categorized by extraction — exact, not guessed.
- **PAN** is the one unlabeled/standalone pattern kept (5 letters + 4 digits + 1 letter is
  distinctive enough to rarely occur by chance in English prose). **Aadhaar, Passport, Employee
  ID, Internal ID, and Client are all label-anchored** (only detected next to an explicit
  "Aadhaar:"/"Passport:"/"Employee ID:"/"Client:"-style label) — none of these has a self-evident
  shape, so an unanchored pattern would be a real false-positive risk.
- **Unlabeled Aadhaar** is matched only in its human-readable grouped form (`1234 5678 9012`), never
  a bare contiguous 12-digit run — too generic a shape to attribute without grouping or a label.

## Bugs found and fixed during test-writing (real, not hypothetical)

1. **Client-phrase over-capture**: `Client: Globex Corporation; Employee ID: EMP12345` was
   captured as one client string including the trailing `Employee ID: ...` — the stop-pattern
   only broke on `(` or end-of-string, not `;`. Fixed by adding `;` as a stop delimiter.
2. **Phone/Aadhaar cross-match**: a 4-4-4 grouped 12-digit number (`1234 5678 9012`) satisfies
   both the Aadhaar-grouped pattern AND `contact.py`'s generic phone-candidate shape (7-15 digits).
   Fixed by excluding any phone candidate that exactly matches the Aadhaar-grouped shape from the
   detector's own phone list (does not touch `contact.py`'s shared `extract_all_phones` behavior
   for its other callers).

## Tests

`tests/test_pii_detector.py` (22 tests) + 5 new tests in `tests/test_contact_utils.py` for the
`extract_all_*` additions. Covers: every category's happy path, the two bugs above (regression
tests for both), deliberate non-detection of companies/projects in text mode, false-positive
guards (year ranges, ordinary prose not matching PAN, unlabeled 12-digit runs not matching
Aadhaar), empty/None input handling, and structured-mode career-break exclusion from companies.

**Regression:** full suite — **832 passed** (805 before this change, +27 new tests), 10
deselected (live_gemini), 0 failed.

## Known limitation (carried over from the architecture doc, not resolved here)

Company-name detection in raw-text mode remains unsolved — reliably finding an arbitrary company
name in free text before any Gemini call still needs either a local NER model or a
gazetteer/heuristic approach, exactly as flagged in `PII_MASKING_ARCHITECTURE.md`'s "Tier 2" open
question. This implementation does not attempt to close that gap; it correctly reports empty for
that case rather than guessing.

Stop after implementation, per instruction.
