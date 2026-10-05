# PII Masking Engine — Implementation Report

Not integrated into the pipeline — confirmed by grep, no reference to `pii_masker.py` exists
outside its own file. `resume_service.py`, `extraction_pipeline.py`, `resume_optimizer.py`,
Gemini prompts and calls, and rendering are all untouched.

## What was built

`app/services/pii_masker.py`:

- **`build_mapping(pii_report)`** — consumes a `pii_detector.py` report directly, returns
  `{real_value: "<PLACEHOLDER_N>"}`. Deterministic: same report in, same mapping out, always
  (no timestamps, no randomness, no process-specific state).
- **`mask_text(text, mapping)` / `unmask_text(masked_text, mapping)`** — raw-string mask/unmask.
- **`mask_structured(structured, mapping)` / `unmask_structured(...)`** — recursively walks an
  already-extracted structured dict/list and masks/unmasks every string value found anywhere
  inside it, not just the handful of dedicated fields `detect_pii_in_structured` reads — an entity
  mentioned inline inside a bullet point gets masked too.

One small, deliberate change to the detector: `pii_detector._REPORT_KEYS` → public `REPORT_KEYS`.
The masker needs the exact same category set and iteration order the detector defines — this is a
direct, intentional coupling ("the masking engine must consume the output from pii_detector.py"),
not a private-import workaround.

## Requirements, addressed one by one

- **Same value → same placeholder, always:** `build_mapping` checks `if value in mapping: continue`
  before assigning a new placeholder — a value already mapped (even under a different category)
  keeps its first-assigned placeholder.
- **Different values → different placeholders:** trivially true — each new, unseen value gets the
  next sequential counter value for its category.
- **Never partially replace an entity:** each match is the full literal value, replaced atomically
  in one substitution — there's no code path that touches part of a value.
- **Never replace substrings inside larger words:** `(?<!\w)`/`(?!\w)` lookarounds, not `\b...\b` —
  see "bug found" below for why the distinction mattered.
- **Fully reversible:** `unmask_text`/`unmask_structured` restore the exact original — verified by
  a round-trip test (`mask → unmask == original`) on both raw text and a full structured dict.
- **In memory only, never written to disk, original values never logged:** the module does no I/O
  or logging at all — the mapping is a plain dict returned to the caller; there's nothing here that
  *could* persist or log a value. (Enforcing this once wired into the real pipeline — not writing
  the mapping to a request log, not persisting it past one request's lifetime — remains the
  integration's responsibility, not this module's; flagging so it isn't assumed automatically
  covered later.)
- **Do not modify text outside detected entities:** the regex only ever touches the matched span;
  everything else in the string is untouched by construction.
- **Preserve exact character positions "as much as practical":** literally impossible in general —
  a placeholder is a different length than the value it replaces, so absolute positions shift.
  What's actually preserved is *precise, non-destructive span replacement*: every match resolves
  against the real value's own boundaries via regex, never an approximate or position-blind
  operation that could corrupt adjacent text.

## Real design decision surfaced during implementation

If the exact same string is detected under two different categories (e.g. the same real company
name appears both as an `experience[].company` value and inline in a project's `client` field),
which placeholder wins? Resolved by `REPORT_KEYS`'s fixed iteration order — whichever category is
processed first gets to name the placeholder; every later category reusing that same value string
gets the same placeholder, not a second one. Covered by
`test_build_mapping_same_value_always_same_placeholder`.

## Bug avoided (caught before it shipped, not after)

An initial design used `\bvalue\b` (standard word-boundary anchors) for masking. That fails for
any value whose first or last character is already non-word — a phone number starting with `+`,
for instance: `\b` requires a transition between a word and non-word character, and can never
match at a position between two non-word characters, so `\b+1-415...` would simply never match at
all, silently skipping exactly the values most likely to need it. Switched to
`(?<!\w)value(?!\w)` (require NOT preceded/followed by a word character, rather than requiring a
symmetric word/non-word transition) — this handles a leading `+`, `@`, or any punctuation
correctly. Covered by `test_mask_text_handles_value_starting_with_non_word_character`.

## Tests

`tests/test_pii_masker.py` — 17 tests (one assertion was initially wrong about client-detection
order, corrected after confirming the code's actual behavior was correct, not a bug — see the test
file's own comment). Covers: deterministic mapping, same-value/different-category collision
resolution, different-values-different-placeholders, full masking, no-touch-outside-entities,
no-substring-inside-larger-word, the non-word-boundary fix, longer-value-masked-first (overlap
guard), never-partial masking, empty-input handling, full mask→unmask reversibility (both raw text
and structured dict), placeholder-numbering past 9 with no prefix collision, and
non-mutation of the input dict.

**Regression:** full suite — **849 passed** (832 before this change, +17 new), 10 deselected
(live_gemini), 0 failed.

Stop after implementation, per instruction.
