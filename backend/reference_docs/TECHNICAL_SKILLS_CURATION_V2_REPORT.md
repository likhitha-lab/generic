# Technical Skills Curation V2 + DOCX Logo Positioning Report

Scope this round: `app/services/file_generator.py` (logo positioning only — PDF code paths
verified unchanged, `_build_pdf_with_tier`/`build_pdf_bytes`/`canvas.drawImage` all at their same
lines) and `app/services/skill_intelligence.py` (Technical Skills only). Plus their test files.

## Part 1 — DOCX Header Logo Positioning

**Root cause:** the right header cell was sized to *exactly* the logo picture's own declared
width, with zero buffer. Word's default table-cell internal padding (~0.08in each side, never
explicitly zeroed) then ate into that already-exact space, pushing the picture against the cell/
page boundary — plausibly clipping its right-hand portion, where the "DATAFLIX" wordmark sits.

**Fix:** the right cell is now sized to the logo's width **plus a small buffer**
(`_LOGO_CELL_RIGHT_BUFFER_IN = 0.15`), taken out of the left cell (so the table's total width —
and therefore the divider, margins, everything else about the header — is unchanged), and the
cell's internal padding is explicitly zeroed (`_zero_cell_margins`, new `w:tcMar` OXML). The
picture's own `width`/`height` values are completely untouched — same asset, same aspect ratio,
same resolution, confirmed by a dedicated test comparing the picture's declared extent before and
after.

Verified directly: right cell width increased from 1.25in (bare logo width) to 1.4in; picture
extent unchanged at exactly 1.25in × its aspect-preserved height.

## Part 2 — Technical Skills Curation V2

Three of the five given examples were already correctly handled by the pipeline built over the
last several rounds — confirmed by running them directly before writing any new code, not assumed:

| Example | Already worked? |
|---|---|
| Oracle / Oracle SQL / Oracle DB → Oracle | Yes (`_fold_generic_vendor_suffixes`) |
| Oracle / Oracle SQL / Oracle DB / **Oracle Database** → Oracle | Yes — "Database" was already in the generic-suffix word set |
| Router/Lookup/Joiner Transformation (3 of 5 types) → Informatica Transformations | Yes — the mechanism was never hardcoded to a specific count |

**One genuinely new instruction, and it reverses a prior decision:** "SQL Developer → SQL". Two
rounds ago, this exact pairing was deliberately kept distinct ("SQL Developer" as Oracle's IDE vs.
bare "SQL" as the language) — with its own test asserting they must never merge. This round's own
example explicitly asks for the opposite. Followed as newer, more explicit instruction — not
silently overwritten: `_NORMALIZATION_MAP` gained one entry (`"sql developer": "SQL"`) with a
comment pointing at this exact reversal, and the prior test that asserted the opposite was updated
to assert the new, instructed behavior instead. Flagging this plainly rather than pretending the
two rounds' guidance was always consistent.

**Orchestration category:** newly named as an allowed category. Rather than reclassify items
across unrelated existing categories (a bigger, less-evidenced change), split it out of the
existing "Data Engineering" dynamic label specifically for genuine scheduling/orchestration tool
names (Airflow, Oozie, Luigi, Prefect, Dagster, Control-M, Autosys) — these are a distinct
recruiter-facing capability (task/job scheduling) from Spark/Hadoop/Databricks-style data
*processing*, which keeps the "Data Engineering" label. One existing test (Airflow was asserted
under "Data Engineering") updated to reflect the new, more precise split.

## Phase mapping (confirming the architecture already matches what was asked)

- **Phase 1 (Normalize / map synonyms to canonical names):** `_normalize` (Stage 2,
  `_NORMALIZATION_MAP`) + `_consolidate_related_skills`'s version-folding and vendor-suffix-folding
  passes.
- **Phase 2 (Classify into a fixed taxonomy):** `classify_skill`/`_classify_and_filter` +
  `_apply_dynamic_display_labels` (Languages, Databases, Cloud & Storage, Data Warehouse, Data
  Engineering, Orchestration, BI & Reporting, ETL / ELT Tools, Data Quality, API Integration,
  Practices, ... — every category this round names, plus a few more already present).
- **Phase 3 (Curate — exclude responsibility-type entries):** `_is_technical_skill_eligible` /
  `_RESPONSIBILITY_PHRASES` (shipped in the first Technical Skills Curation round). No new
  responsibility-phrase examples were given this round (the 11 named this time —
  "Requirements Documentation", "Workflow Monitoring", "Task Scheduling", "Report Building",
  "Documentation", "Business Needs", "Discussion Notes", "Test Scenario Creation" — are close
  variants of, but not identical to, the phrases already excluded). Added as additional exact
  entries in the existing set (not a new mechanism), since they're clearly the same class of
  confirmed evidence as the original 12.

## Before / after (combined example)

```
BEFORE:
Oracle, Oracle SQL, Oracle DB, Oracle Database, SQL Developer, SQL, Informatica IDMC,
Router Transformation, Lookup Transformation, Joiner Transformation,
Requirements Documentation, Workflow Monitoring, Task Scheduling

AFTER:
Databases:        Oracle
Languages:        SQL
ETL / ELT Tools:  Informatica IDMC, Informatica Transformations (Router, Lookup, Joiner)

(Requirements Documentation / Workflow Monitoring / Task Scheduling excluded - Professional
Experience territory, not Technical Skills)
```

## Requirement-by-requirement

- Technologies preserved — every genuine technology in the combined example survives as its own
  entry or a named sub-item.
- Responsibilities removed — confirmed excluded via the expanded `_RESPONSIBILITY_PHRASES` set.
- ATS keywords preserved — every surviving technology name is still plain, matchable text.
- Redundancy reduced — 13 raw items → 3 curated group lines in the combined example above.

## Validation

- ✓ PDF unchanged — confirmed by line-scan, no PDF code path edited.
- ✓ DOCX header unchanged except logo position — fonts/spacing/divider/margins untouched; only the
  logo cell's width and internal padding changed, verified by a dedicated test that the picture's
  own dimensions are unaffected.
- ✓ Logo fully visible, aspect ratio/asset unchanged.
- ✓ Technical Skills resemble the benchmark style — grouped capability lines, not flat bullets.

## Tests

`tests/test_file_generator_formatting.py`: 2 new (logo cell has room beyond the picture width +
`tcMar` present; picture dimensions unaffected by the cell-width change).

`tests/test_skill_intelligence.py`: 4 new (SQL Developer normalizes to SQL; the confirmed
"Oracle Database" 4-way fold; the confirmed 3-of-5 transformation-type subset; the 8 new
responsibility-phrase examples all excluded) + 2 existing tests updated for the intentional
reversals (SQL Developer no longer asserted distinct; Airflow now asserted under "Orchestration"
instead of "Data Engineering").

## Regression

Full suite: **927 passed** (921 before this change + 6 new), 10 deselected (live_gemini), 0 failed.

Stop after implementation, per instruction.
