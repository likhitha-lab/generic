# PII Masking System — End-to-End Security Validation Report

No code changed this round — every check below was performed against the system as it stood after
the previous integration/unmasking rounds. No High-risk issue was found, so per instruction nothing
was implemented.

## Executive Summary

- **Overall security rating: Good, with disclosed limitations** — every directly-identifying,
  high-actionability PII category (candidate name, email, phone, LinkedIn, GitHub, PAN, labeled
  Client, labeled Employee ID) is fully or adequately masked before every Gemini call, verified by
  direct adversarial testing (Part 3), not assumed.
- **Production readiness: Ready, with one open scoping decision.** The masking layer is sound,
  reversible, non-persistent, and doesn't touch quality/formatting. The one meaningfully-sized gap
  (Company/Project names in raw-text mode) is a known, already-disclosed limitation across four
  prior reports, not a new finding — closing it requires a scoping decision (local NER vs.
  gazetteer/heuristic) this validation doesn't make unilaterally.
- **Remaining limitations:** Company/Project name masking (Medium), portfolio URLs without an
  `http(s)://` prefix (Low-Medium), addresses without an explicit label (Low-Medium, by design),
  no code-level guardrail stopping a future developer from calling the Gemini SDK directly instead
  of through `call_gemini` (Medium, organizational not technical).
- **Recommended next steps:** decide the Company/Project detection approach if closing that gap is
  a priority; otherwise, no urgent action needed.

---

## Part 1 — Architecture Review

| Check | Result |
|---|---|
| Every Gemini request protected | ✓ — 17 call sites across 6 files (`extraction_pipeline.py`, `resume_optimizer.py`, `experience_refiner.py`, `summary_generator.py`, `section_recovery.py`, `resume_service.py`), all route through the one `gemini_client.call_gemini` function — re-confirmed by grep this round, unchanged since the integration report |
| Masking always before Gemini | ✓ — `mask_text` runs before both the first `_send_and_parse` call and the malformed-JSON retry |
| Unmasking always after Gemini | ✓ — `unmask_structured_verified(...)["structured"]` is the last line before every return |
| No alternate Gemini path | ✓ — grepped for any `google.genai`/`import genai` usage anywhere in `app/`; found only inside `gemini_client.py` itself |
| Future Gemini calls remain protected | **Conditionally** — true as long as a future caller imports the public `call_gemini`, since `_send_and_parse`/`_get_client` are private and only called from inside it. Nothing at the code level *prevents* a future module from importing `google.genai` directly and bypassing this entirely — flagged as a Medium organizational risk in Part 7, not a bug in what exists today |

## Part 2 — Privacy Validation

| Category | Status | Why |
|---|---|---|
| Candidate Name | **Fully Protected** | `resolve_candidate_name`'s local signals run reliably pre-Gemini |
| Email | **Fully Protected** | Standard regex, reliable for conventional addresses |
| Phone | **Fully Protected** | Regex catches common formats; excludes year-range false positives |
| LinkedIn | **Fully Protected** | Dedicated regex, reliable |
| GitHub | **Fully Protected** | Dedicated regex, reliable |
| Portfolio | **Partially Protected** | Only catches URLs with an explicit `http(s)://` scheme — confirmed by direct test: `"Portfolio: janedoe.dev"` (no scheme) returns `[]` |
| Address | **Partially Protected** | Label-anchored only (`Address:`/`Residence:`/`Location:`) — confirmed: `"Bangalore, Karnataka, India"` with no label returns `[]`. Deliberate precision-over-recall tradeoff, not an oversight |
| Company | **Not Protected** (in the live call path) | `detect_pii_in_text` (the mode `call_gemini` actually uses) deliberately never guesses at company names — confirmed: `"Acme Corp"` with no label returns `[]`. Same disclosed Tier 2 gap as the last four reports |
| Client | **Partially Protected** | The common `"Client: X"` label convention (confirmed across multiple real resumes this session) is caught; an unlabeled client mention is not |
| Project | **Not Protected** (in the live call path) | Same reasoning as Company |
| Employee ID | **Partially Protected** | Label-anchored (`Employee ID:`/`Emp No:`/etc.) |
| PAN | **Fully Protected** | Distinctive standalone format (5 letters+4digits+1letter), unanchored |
| Aadhaar | **Partially Protected** | Labeled, or the human-readable grouped-by-4 form; a bare ungrouped 12-digit run is deliberately not matched (too generic a shape) |
| Passport | **Partially Protected** | Label-anchored, India format only |

## Part 3 — Adversarial Testing (results of actually running each case, not hypothetical)

| Attack | Result |
|---|---|
| Malformed placeholders (`<CANDIDATE_NAME>`, `_01`, no underscore, wrong case) | Detected, left unchanged, warned — never resolved to a guessed value (20 existing tests + re-verified) |
| Nested placeholders (dict-in-list-in-dict) | Restores correctly at every depth |
| Duplicated placeholders (same placeholder, 3+ occurrences) | All 3 occurrences restored correctly, verified with a fresh live test (`Jane Doe, Jane Doe, Jane Doe` → all 3 restored) |
| Overlapping entities (`"Global Tech Corp"` containing `"Tech Corp"`) | Longer value masked first, no corruption — verified live: both `<COMPANY_1>`/`<COMPANY_2>` round-trip correctly even when one is a substring of the other |
| Repeated names / repeated emails | Confirmed live: 3x repeated name + 3x repeated email both mask and unmask correctly, full round-trip identity preserved |
| Mixed structured/unstructured content | Covered by existing `mask_structured`/`unmask_structured` tests |
| Placeholders inside bullets | Confirmed (existing test — inline company mention inside a bullet point) |
| Placeholders inside markdown (bold `**X**`, link `[text](url)`) | Confirmed live — markdown syntax around a placeholder doesn't interfere with exact-token replacement, full round-trip identity preserved |
| Placeholders inside JSON | N/A as a separate case — Gemini's response is already parsed into a Python dict before unmasking runs; this *is* the structured-dict case, already covered |
| Placeholders inside lists | Confirmed (existing tests) |
| Placeholders inside tables | Confirmed live with a markdown pipe-table string — plain text as far as masking is concerned, round-trips correctly |
| Placeholder as a dict KEY (not a value) | **New finding, pathological, Low risk** — `mask_structured`/`unmask_structured` only ever transform dict *values*, never keys. Confirmed live: a placeholder used as a key is never touched. Not exploitable in normal operation, since every prompt's JSON schema (`prompts.py`) has fixed field names — Gemini has no mechanism to introduce a new key, let alone a placeholder-shaped one |

## Part 4 — Logging Review

Read every `logger.*` call in `pii_detector.py`, `pii_masker.py`, and `gemini_client.py`:

- `pii_detector.py` — no logging calls at all.
- `pii_masker.py` — 2 warning calls (`find_unknown_placeholders`, `unmask_structured_verified`), both log only the malformed *placeholder token* itself (e.g. `<CANDIDATE_NAME>`), never an original value.
- `gemini_client.py` — every log call checked individually: API-key-*detected* boolean (not the key), exception types/classifications, retry counts, JSON-repair status. One line (`"raw response follows"`, malformed-JSON path) logs Gemini's actual response text — but structurally safe: that response was generated from an already-masked prompt, so it can only ever contain placeholders/instructional text, never real PII, since Gemini was never given real values to echo back. This is an emergent safety property of the masking architecture, not something added for this validation.

**No original PII appears in any log path. No mapping table is ever logged anywhere.**

## Part 5 — Persistence Review

Grepped `resume_service.py` and every `app/storage/*.py` file for any reference to `mapping` —
none found. `mapping` is referenced only inside `gemini_client.py` (built and consumed as a local
variable within one `call_gemini` call) and `pii_masker.py` (where it's defined/operated on) — it
never crosses into any storage or DB layer. No `@lru_cache` or similar caching decorator exists on
any masking function. Lifetime is not just request-scoped but **call-scoped** — narrower than the
architecture doc originally required, discarded the moment each individual `call_gemini` invocation
returns.

## Part 6 — Resume Quality

- **Information preservation:** guaranteed by construction — `unmask_text`/`unmask_structured`
  round-trip to the exact original (tested explicitly), and unmasking happens before any
  downstream consumer (optimizer post-processing, `file_generator.py`) ever sees the content.
- **Formatting / ATS compatibility / bullet quality / summaries / project descriptions:** none of
  these code paths know masking exists — `file_generator.py`, `experience_intelligence.py`'s bullet
  enhancement, `resume_scoring_engine.py` are all untouched and unaware, confirmed by the full
  877-test suite (including every formatting/ATS/quality test from prior rounds) passing unchanged.

## Part 7 — Remaining Risks

| Issue | Severity | Impact | Likelihood | Recommendation |
|---|---|---|---|---|
| Company/Project names not masked (raw-text mode) | **Medium** | Employer/project names reach Gemini unmasked — real but less severe than direct-contact PII; Client (the more sensitive related category) IS protected | High — true on nearly every resume | Decide: local NER model vs. gazetteer/heuristic (same open question since the architecture doc) |
| Portfolio URL without `http(s)://` scheme | **Low-Medium** | A personal site URL reaches Gemini unmasked | Medium — some resumes omit the scheme | Extend the URL regex to also match a bare `domain.tld/...` pattern, if prioritized |
| Address without an explicit label | **Low-Medium** | A bare city/state line reaches Gemini unmasked | Medium | Deliberate precision tradeoff already made; revisit only with evidence of real impact |
| No code-level guardrail against a future direct-`genai` caller | **Medium** | A future change could silently bypass masking entirely | Low today (zero such usage exists), but not enforced | A lint rule or code-review checklist item, not a code fix |
| Malformed placeholder surviving into final rendered resume | **Low** (downgraded from the prior round) | A visible glitch (a literal `<CANDIDATE_NAME>`-shaped string in the output), not a PII leak — the token itself carries no real data | Low — requires Gemini to itself corrupt a placeholder | Already detected and logged (this round's earlier work); a fallback-to-original-value mechanism would fully close it if ever prioritized |
| Placeholder used as a JSON dict key | **Low** | Not unmasked if it occurred | Effectively none — schema keys are fixed by `prompts.py`, Gemini has no mechanism to introduce one | No action needed |

**No High-severity issue found. No code implemented this round, per instruction.**

## Regression

Full suite: **877 passed**, 10 deselected (live_gemini), 0 failed — unchanged from before this
validation pass (no code was modified).

Stop after implementation, per instruction.
