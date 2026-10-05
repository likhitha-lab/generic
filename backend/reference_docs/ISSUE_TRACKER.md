# Resume Pipeline — Recurring Issue Tracker

Running log across every Original/Generated/Expected validation triplet. An issue only gets a
code fix once it recurs across 2+ resumes (see rule below) — isolated (count=1) issues are
documented here and left alone until/unless they recur.

**Rule:** count=1 -> document only, no code change. count>=2 -> smallest possible fix in the
existing pipeline (extraction/entity-linking/refinement/validation/rendering), then full
regression run.

## Validation cases processed
1. Venkat (RPA/UiPath developer, 8.7yrs) — `Venkat_RPA_UiPath_Resume.pdf` (Original) vs
   `Venkat.pdf` (Generated). Dataflix-format reference used: `P VENKATESHWAR REDDY.pdf`
   (different candidate, format-only exemplar, not content-paired).
2. Style/formatting reference: pasted "ideal resume" text — a Dataflix style exemplar, not a
   content-paired triplet. Reverse-engineered into `DATAFLIX_RESUME_STANDARD.md`, decided into
   `STYLE_DECISION_MATRIX.md` (3 ADOPT, 2 REJECT, 3 NEEDS CONFIRMATION).
3. Real regression sample resumes (`reference_docs/`) — scanned directly (not a triplet) to
   verify the shipped style changes (bullet Summary, weak-verb replacement) against real bullet
   text, not just synthetic fixtures.

## Recurring issue counts

| Issue | Count | Resumes | Status |
|---|---|---|---|
| Missing "Client" field on Project entries | 1 | Venkat | FIXED (schema had no field at all, not a regression — added `client` to canonical_model.Project, prompts.py extraction shape, both renderers) |
| Projects silently dropped by count cap (`rank_and_select_projects`) | 1 | Venkat | FIXED (8→4 real distinct projects lost; cap directly violated "100% Project Preservation" target and would already fail the Evaluation Dashboard's own 95% threshold — removed count cap from resume_optimizer.py's main path) |
| Certification date/validity substring dropped | 1 | Venkat | Prompt wording strengthened (rule 5), not yet confirmed fixed — extraction-stage LLM compliance issue, needs a 2nd resume to confirm before calling it resolved |
| Skills-table rows (Web tech, OS, DB) partially skipped during extraction | 1 | Venkat | Prompt wording strengthened (rule 3), same caveat as above |
| Achievements duplicating content already in surviving Project bullets | 1 | Venkat | Documented only — isolated, no fix |
| Career-break entry generated for a short (~5mo) unlabeled gap | 1 | Venkat | Documented only — isolated, no fix. Dates correctly left blank (no invented date range), so not a hallucination; open question is whether the genuine-break detection threshold is too aggressive |
| Summary understates true domain/client breadth (recency-biased) | 1 | Venkat | Documented only — isolated, no fix |
| Compound-verb bullets ("X-ing & verb") broken by weak-verb rewrite | 1 | Rohan Deshmukh (real regression sample, found by direct corpus scan, not a triplet) | FIXED same pass it was found — self-introduced bug in a change just shipped, not gated behind the recurrence rule (that rule is for extraction/comparison findings across triplets, not regressions in code shipped this session) |

## Open, unresolved (NEEDS CONFIRMATION — blocking, no action taken)
- Skill category taxonomy: adopt ideal's tool-specific labels universally, or keep current
  plain-noun taxonomy, or make it role-adaptive? (`STYLE_DECISION_MATRIX.md`)
- "Education Qualifications" vs "Educational Qualifications" spelling — leaning typo, not acted on.
- Tools/Certifications folded into Technical Skills vs kept as separate sections — unclear if the
  ideal sample's omission is a deliberate style choice or just that candidate having nothing to
  list.
- Certification date-preservation and skills-table-completeness prompt strengthening (Venkat) —
  wording changed, but not yet re-confirmed against a second live-Gemini sample (sandbox has no
  live Gemini access this whole session — network blocked, confirmed repeatedly).

## Not yet analyzable
- `P VENKATESHWAR REDDY.pdf` (ETL Developer) — no Original/Generated pair provided.
- Unnamed Informatica/Snowflake Data Engineer resume (Hyundai/Mobis) — no Original/Generated pair
  provided.
