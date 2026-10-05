# PII Pipeline Integration Report

## First task: Gemini call-site audit (integration map)

Grepped the whole codebase for every call to `call_gemini`:

| File | Call sites | Purpose |
|---|---|---|
| `extraction_pipeline.py` | 6 (`build_contact_prompt`, `build_summary_prompt`, `build_skills_section_prompt`, `build_experience_prompt`, `build_projects_prompt`, `build_extras_prompt`) | Stage 1 structured extraction |
| `resume_optimizer.py` | 3 (`build_skills_tools_optimize_prompt`, `build_projects_optimize_prompt`, `build_achievements_prompt`) | Resume refinement/optimization |
| `experience_refiner.py` | 1 (`build_experience_optimize_prompt`) | Experience bullet refinement |
| `summary_generator.py` | 1 | Summary generation |
| `section_recovery.py` | 4 (`build_skills_section_prompt`, `build_experience_prompt`, `build_projects_prompt`, `build_extras_prompt`) | Missing-section recovery retry |
| `resume_service.py` | 2 (`build_generate_prompt`, upload + regenerate paths) | Manual resume generation |

**17 call sites across 6 files — all of them call the exact same function, `gemini_client.call_gemini(prompt) -> dict`.** No second, parallel path to the Gemini API exists anywhere in the codebase (confirmed: `_send_and_parse`, the one function that actually reaches the network, is only ever called from within `call_gemini` itself). This one chokepoint is what makes a single, non-duplicated integration possible.

## Second task: integration

**Change made:** wrapped masking inside `gemini_client.call_gemini()` itself — the one function every caller already goes through. Zero changes to any of the 6 caller files above; each still calls `call_gemini(prompt)` exactly as before and gets a `dict` back exactly as before.

```
prompt string in
  → detect_pii_in_text(prompt)        [pii_detector.py, reused as-is]
  → build_mapping(pii_report)         [pii_masker.py, reused as-is]
  → mask_text(prompt, mapping)        [pii_masker.py, reused as-is]
  → _send_and_parse(masked_prompt)    [existing, unchanged - the actual network call]
  → (retry with same masking, if the first response was malformed JSON)
  → unmask_structured(result, mapping)  [pii_masker.py, reused as-is]
dict out
```

Every Gemini call — extraction, refinement, optimization, summary generation, section recovery,
manual generation — is protected identically, because they all pass through this one function.
No masking logic exists anywhere except inside `pii_detector.py`/`pii_masker.py`, both reused
without modification (only 3 new import lines and ~10 lines inside `call_gemini`'s body).

**Mapping scope:** built fresh from each individual prompt's own content, consumed entirely within
that one `call_gemini` call, then discarded — never shared or persisted across separate calls
(the 6 concurrent Stage 1 extraction calls, for instance, each get their own independent mapping).
This is safe because unmasking happens inside the SAME call before the result is ever returned —
no caller ever needs cross-call placeholder consistency, only within-one-round-trip correctness.

## Rules checked

- **Gemini never receives original sensitive values:** masking happens before `_send_and_parse`
  on every path, including the malformed-JSON retry (which reuses the already-masked prompt, not
  the original).
- **Rest of the app keeps using original values:** unmasking happens before `call_gemini` returns
  — no caller, and nothing downstream of it (normalization, entity linking, rendering), ever sees
  a placeholder.
- **Business logic unchanged:** all 6 caller files are untouched.
- **Prompts unchanged except where placeholders must be supported:** `prompts.py`'s instructional
  wording is untouched; only the *content* embedded in a prompt (real values → placeholders)
  changes, and only at request time, never in the template itself.
- **Rendering unchanged:** `file_generator.py` untouched.
- **Backward compatible:** `call_gemini`'s signature and return type are identical to before.
- **No duplicated masking logic:** one call site (`gemini_client.py`) is the only place
  `pii_detector`/`pii_masker` get invoked for this purpose.

## Validation

- ✓ Every Gemini call is protected — structurally guaranteed, not call-site-by-call-site: there is
  only one function that reaches the network, and it's the one that was wrapped.
- ✓ No placeholder leaks into the final resume, **under normal conditions** — unmasking runs
  before every return path. **Residual, honestly-disclosed risk:** if Gemini ever returns a
  *malformed* placeholder (wrong case, truncated, reformatted) that doesn't exactly match a key in
  the mapping, `unmask_structured` won't recognize it and that malformed token could survive into
  the final resume. This is the same failure mode already flagged in
  `PII_MASKING_ARCHITECTURE.md`'s "Unmasking mechanics" section — not solved here, since a
  fallback-to-original-value mechanism for a corrupted placeholder is a further change beyond this
  round's scope, not something to add unannounced.
- ✓ No original PII in Gemini requests — verified directly via
  `test_call_gemini_never_sends_original_pii_to_send_and_parse`.
- ✓ Existing regression suite continues to pass — 849 → 855 (see below).
- ✓ Integration tests added for the complete masking round-trip — see below.

### Why dedicated new tests were necessary (not just "existing tests still pass")

The repo-wide `fake_gemini` autouse fixture monkeypatches `call_gemini` at **each caller's own
module namespace** (`resume_service_module.call_gemini`, `extraction_pipeline_module.call_gemini`,
etc.) — meaning the entire existing 849-test suite bypasses `gemini_client.call_gemini`'s real body
completely and never touches the new masking code at all. Passing was necessary but not sufficient
evidence the integration works. `tests/test_gemini_client_pii_masking.py` (6 tests) calls the REAL
`call_gemini`, mocking only `_send_and_parse` (the actual network boundary) — the only tests in the
suite that genuinely exercise the masking wrapper:

1. Real PII (name/email/phone/LinkedIn/client) never reaches `_send_and_parse`.
2. The masked prompt actually contains the expected placeholder tokens.
3. A response echoing placeholders (including one embedded inline in a bullet point, not just a
   dedicated field) is correctly unmasked back to real values before `call_gemini` returns.
4. A prompt with nothing detectable is passed through completely unchanged (no-op, no wasted
   placeholder noise).
5. The malformed-JSON retry path reuses the same masking on both attempts and still unmasks
   correctly.
6. Empty-prompt edge case.

## Known limitation carried forward (not solved by this integration, honestly disclosed)

Company and Project name masking remains the same Tier 2 gap documented in
`PII_MASKING_ARCHITECTURE.md` and `PII_DETECTION_REPORT.md` — `detect_pii_in_text` still can't
reliably find an arbitrary company/project name in raw prompt text (no NER, no gazetteer), so
those two categories are not masked in ANY Gemini call by this integration, exactly as before.
Closing this would require either a new local NER capability or changing `call_gemini`'s signature
to also accept an already-extracted structured dict for exact detection — both are architecture
changes explicitly out of scope for this round ("do not redesign the architecture").

## Regression

Full suite: **855 passed** (849 before this change + 6 new masking-integration tests), 10
deselected (live_gemini), 0 failed.

Stop after implementation, per instruction.
