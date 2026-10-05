# Technical Skills Normalization Report

Scope: `app/services/skill_intelligence.py` only — one new pipeline stage, wired into
`build_technical_skills`/`build_technical_skills_grouped` (both already share the exact same
pipeline). No other section, no rendering, no PDF/DOCX generation, no PII masking, no ATS scoring,
no business logic touched — confirmed by diff scope (only this one file plus its own test file
changed).

## Why the existing dedup couldn't solve this (evidence, not assumption)

Before writing anything new, tested the existing fuzzy-merge (`_detect_duplicates`, threshold
0.93, `difflib.SequenceMatcher`) against the exact examples given:

| Pair | Similarity | Clears 0.93? |
|---|---|---|
| "Oracle SQL (11g)" vs "Oracle SQL (12c)" | 0.875 | No |
| "PL/SQL" vs "Stored Procedures" | 0.174 | No |
| "PL/SQL" vs "Cursors" | 0.154 | No |

No threshold adjustment fixes this safely — raising tolerance to catch 0.875 would also start
false-merging genuinely different skills elsewhere, and nothing catches 0.15-0.17 (semantically
related, textually unrelated — a different kind of redundancy entirely). This is real evidence a
new, purpose-built pass was needed, not a speculative addition.

## What was built — `_consolidate_related_skills`, three passes

1. **`_fold_version_variants`** — collapses a version/edition-tagged duplicate into one
   version-agnostic entry. Only strips a trailing parenthetical that contains a digit ("(11g)",
   "(12c)", "(3.x)") — a parenthetical with no digit ("(Advanced)", "(Cloud)") is meaningful
   content, never touched.
2. **`_group_shared_anchor_variants`** — folds skills that all mention the same explicit,
   pre-vetted "anchor" word into one grouped entry with every distinguishing remainder preserved
   as a sub-item.
3. **`_group_sql_family_features`** — folds a small, explicit set of generic database feature
   terms (Stored Procedures, Functions, Views, Triggers, Cursors, Indexes, Packages, Joins,
   Subqueries, Constraints) beneath the first recognized SQL-family skill in the list — only when
   that anchor is actually present; a feature term with no anchor is left completely untouched.

### A real risk found and avoided while designing pass 2

The natural, fully-generic version of pass 2 would detect ANY shared 2-6 letter acronym across
skills. Tested it against the given examples before shipping: it would also fire on "SQL" — which
appears in "Oracle SQL", "SQL Developer", and bare "SQL", three genuinely different things (a
database dialect, an IDE, a language) — incorrectly merging them into one nonsense bullet. "ETL",
by contrast, is used in only one sense across ordinary resume text. Rather than ship a rule proven
to misfire, the anchor list (`_SHARED_ANCHOR_WORDS`) is a small, explicit, evidence-vetted set
(currently just `"ETL"`) — extended only when a future word is shown to be as unambiguous, never a
blanket pattern. This is a deliberate scope limit, documented in the code, not an oversight.

## Before / after (all three confirmed examples, run through the real pipeline)

```
BEFORE: ['PL/SQL', 'Stored Procedures', 'Functions', 'Views', 'Triggers', 'Cursors']
AFTER:  ['PL/SQL (Stored Procedures, Functions, Views, Triggers, Cursors)']
  6 bullets -> 1. Every term preserved, nothing invented.

BEFORE: ['Oracle SQL (11g)', 'Oracle SQL (12c)', 'SQL Developer', 'SQL']
AFTER:  ['Oracle SQL', 'SQL Developer', 'SQL']
  4 bullets -> 3. Version duplicates collapsed; SQL Developer (tool) and
  bare SQL (distinct, ATS-valuable keyword) correctly left untouched.

BEFORE: ['ETL mappings', 'ETL mapping creation', 'Workflows (ETL)', 'Transformations (ETL)']
AFTER:  ['ETL (Mappings, Mapping creation, Workflows, Transformations)']
  4 bullets -> 1. Every distinguishing word preserved as a sub-item.
```

## Requirement-by-requirement

1. Never remove genuine skills — every input skill survives as either its own entry or a named
   sub-item inside a grouped one; nothing is silently dropped (`_group_sql_family_features` only
   folds a feature term when a real anchor is present — with no anchor, it's left standing on its
   own, per its own test).
2. Never invent skills — every word in every grouped output traces back to an input string;
   nothing synthesized beyond the anchor label itself, which is always one of the input skills'
   own text (or, for the SQL-feature case, the recognized anchor already in the list).
3. ATS keywords preserved — "SQL" and "SQL Developer" both survive as distinct entries in example
   2; every sub-item name inside a grouped bullet is still present as plain text, still matchable
   by a keyword scan.
4. Semantically equivalent skills merged — version variants (pass 1).
5. Implementation details grouped beneath a primary skill — SQL features under PL/SQL (pass 3).
6. Exact duplicates eliminated — unchanged, already handled by `_dedupe_exact` upstream.
7. Obvious wording variants eliminated — ETL-style shared-wording folding (pass 2).
8. Concise grouped representation preferred — 6→1, 4→3, 4→1 in the three examples.
9. All important technologies preserved — confirmed via test assertions checking every original
   term's presence (as a top-level entry or a sub-item) after consolidation.
10. Repetition reduced, readability improved — same evidence as #8.

## Tests

15 new tests in `tests/test_skill_intelligence.py`: each of the three passes tested individually
(happy path, the "no anchor present -> untouched" safety case, the "non-version parenthetical
untouched" case, the deliberate SQL-scope-limit case), the combined `_consolidate_related_skills`
orchestration against all three confirmed examples plus a no-op case, and 3 end-to-end tests
through the real public `build_technical_skills` entry point.

## Regression

Full suite: **894 passed** (879 before this change + 15 new), 10 deselected (live_gemini), 0 failed.

Stop after implementation, per instruction.
