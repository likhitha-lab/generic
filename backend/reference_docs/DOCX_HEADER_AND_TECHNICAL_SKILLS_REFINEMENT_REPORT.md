# DOCX Header + Technical Skills Refinement Report

Two files touched: `app/services/file_generator.py` (DOCX header only — PDF generator code paths
verified unchanged, still lines 810-978, `canvas.drawImage` call at line 884 untouched) and
`app/services/skill_intelligence.py` (Technical Skills only). Plus their test files. Nothing else.

## Part 1 — DOCX Header (two-column table)

**Before:** Logo sat above the candidate's Name/Contact (three stacked header paragraphs: logo,
name, contact), not the side-by-side layout the PDF has.

**After:** A single borderless 1×2 table in the header — left cell holds Name (Heading 1 style)
then Contact directly below it; right cell holds the logo, right-aligned, vertically top-aligned
so it sits level with the Name line. The divider (`_add_horizontal_rule`) stays exactly where it
was — a plain header paragraph directly after the table, unchanged. Body still begins with
"Professional Summary" — confirmed by test.

A table is used deliberately: Word's header is a linear paragraph flow with no floating/anchored
positioning by default, so a table is the only way to put the logo on the *same horizontal line*
as the Name. This is unrelated to the codebase's existing "avoid tables in body content" ATS
guidance (see `_add_experience_entry`'s own comment on what that protects against — real `<w:tbl>`
data structures in rendered *content* some ATS parsers mis-order) — this table is borderless, holds
no data, exists purely as a layout scaffold in the header, which ATS systems don't parse for
job-matching content anyway.

If the logo asset is missing, falls back to the prior plain-stacked-paragraph layout (the whole
reason for the table doesn't apply without a logo).

## Part 2 — Logo aspect ratio (real root cause found, not guessed)

**Root cause:** the PDF's `canvas.drawImage(..., width=90, height=35, preserveAspectRatio=True)`
call was already there, unchanged, this whole session — `preserveAspectRatio=True` means ReportLab
never actually renders the logo at the full 35pt box height; it fits the logo's real 209×48px
(~4.35:1) image within that box, width-constrained, since the box's own ratio (90/35 ≈ 2.57:1) is
narrower than the logo's native shape. The PDF's *visible* logo is therefore already shorter than
35pt tall. python-docx's `add_picture(width=, height=)` has no equivalent "fit preserving aspect"
mode — it always deforms the image to exactly fill both dimensions given. A prior fix (this same
session, a few tasks ago) set DOCX's width/height to the PDF's *bounding box* dimensions directly
(1.25in × 0.486in) — which matched the box, but not what the box actually displays, visibly
stretching the logo taller than its real proportions.

**Fix:** new `_docx_logo_height_inches(target_width_in)` reads the logo file's real pixel
dimensions (fresh, every call — "highest available resolution", never a hardcoded pixel count that
could go stale) and derives the height that preserves that native ratio at the shared, still-fixed
width. Width never changes (1.25in, same as before, the binding constraint both formats share);
height is now provably shorter and never independently chosen.

## Part 3 — Technical Skills Eligibility

**Problem confirmed:** responsibility/activity phrases ("Requirements Gathering", "Client
Interaction", "Internal Reviews", "Bug Detection", ...) are short noun phrases, not full sentences
or certification titles — so the existing `_looks_like_non_skill` check (word-count >5 / cert-
keyword match) never caught any of them; a dedicated filter was genuinely needed, not a duplicate
of existing logic.

**What was NOT done, deliberately:** the request's own "Include: Programming Languages, Databases,
..." list reads like an inclusion whitelist. This module's own docstring documents that an
inclusion whitelist was already tried and rejected as a real, confirmed defect (a candidate's
genuine-but-unusual skill matching no listed category used to vanish silently). Implementing Part 3
as a strict whitelist would resurrect that exact bug. Instead: a small, explicit, evidence-driven
**exclusion** set of only the confirmed responsibility phrases (plus their obvious singular/plural/
hyphenation variants) — matched as a whole normalized phrase, never a substring, so a genuine
technology can never be caught by accident (verified by a dedicated test: "Code Review Tools"
survives even though "review" overlaps with the excluded "Internal Reviews"). Everything not on
this list still falls through to "Other Skills" exactly as before — the "never silently drop a
genuine skill" guarantee is preserved.

Wired into `_clean()` (Stage 1 of the pipeline, shared by both `build_technical_skills` and
`build_technical_skills_grouped`) — one new check in the existing per-item filtering loop.

### Before / after

```
BEFORE (16 items):
Python, SQL, Requirements Gathering, Client Interaction, Internal Reviews, External Reviews,
Knowledge Transfer, Story Prioritization, Walk-throughs, Business Needs Understanding,
Bug Detection, Bug Addressing, Test Data Generation, Documentation Discussions, AWS, Snowflake

AFTER (4 items - every genuine technology preserved, every responsibility phrase excluded):
Python, SQL, AWS, Snowflake
```

## Validation — everything else identical

- PDF generator: not edited (confirmed by line-scan of `file_generator.py` — `_build_pdf_with_tier`/
  `build_pdf_bytes`/the `canvas.drawImage` call are unchanged).
- Professional Summary, Experience, Career Break, Project Details, Achievements, Education,
  Certifications, Tools: not edited in any file.
- PII detection/masking, Gemini prompts/integration, extraction/parsing, ATS optimization,
  business logic: not edited — no changes outside `file_generator.py` (DOCX header only) and
  `skill_intelligence.py` (Technical Skills only).
- Existing regression tests: only updated where these two changes directly required it (header
  text now lives inside a table cell, not a bare header paragraph; the 2 logo-dimension tests that
  asserted the old, stretched, incorrect behavior).

## Tests

- `tests/test_file_generator_formatting.py`: 3 existing helpers fixed to also read header table
  cell text; 2 logo tests rewritten to check the corrected aspect-preserving relationship instead
  of the old (buggy) exact-box-match; 7 new tests for the header table itself (two columns exist,
  borderless, Name+logo share a row, contact directly below name in the same cell, divider survives
  in position, body still starts with Professional Summary, graceful fallback when the logo is
  missing).
- `tests/test_file_generator_visibility.py`, `tests/test_resumes_visibility_api.py`: same header-
  table text-extraction fix.
- `tests/test_skill_intelligence.py`: 5 new tests (all confirmed responsibility examples rejected,
  genuine technologies never rejected, exact-phrase-not-substring guard, end-to-end `_clean` check,
  end-to-end `build_technical_skills` check).

## Regression

Full suite: **913 passed** (901 before this change + 12 new), 10 deselected (live_gemini), 0 failed.

Stop immediately after completing these two improvements, per instruction.
