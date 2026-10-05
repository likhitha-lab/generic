# Technical Skills Curation Report

Scope: `app/services/skill_intelligence.py` only, plus its own test file — confirmed by this
turn's own edit history. No extraction, parsing, Gemini prompts/integration, PII masking, PDF/DOCX
generation, Summary, Experience, Projects, Education, Certifications, ATS optimization, or business
logic touched.

## Same tension as the prior Technical Skills task, resolved the same way

The requirement's own "For every extracted item, determine whether it is: [10 technical
categories]... If yes, include it. Otherwise, do not render it" reads as an inclusion whitelist.
`skill_intelligence.py`'s own module docstring documents that an inclusion whitelist was already
tried and rejected here as a real, confirmed defect (a candidate's genuine-but-unusual skill
matching no listed category used to vanish from the resume with no trace). Implementing this
requirement literally as a strict whitelist would resurrect that exact bug. As with the prior
Technical Skills Curation-adjacent task, this is resolved the same way: the existing
`_RESPONSIBILITY_PHRASES` exclusion set (shipped last round) already handles "do not duplicate
experience responsibilities inside Technical Skills" for every concretely-named example so far — no
new responsibility-phrase examples were given this round, so nothing was added there without
evidence. What *is* new and actionable this round is the grouping guidance — two concrete examples,
both implemented.

## What was built

Two new consolidation passes, added to the same `_consolidate_related_skills` pipeline (shared by
`build_technical_skills` and `build_technical_skills_grouped`) established two tasks ago:

### 1. `_fold_generic_vendor_suffixes` — "Oracle" + "Oracle SQL" + "Oracle DB" → "Oracle"

Groups skills by their first word; when every member of a group is either the bare first word or
that word plus one generic, non-distinguishing descriptor ("SQL", "DB", "Database", "Server"),
collapses the whole group to the bare form. Deliberately conservative and all-or-nothing: if *any*
member of the group adds a genuinely distinguishing word (confirmed by testing — "Oracle Fusion"),
the *entire* group is left untouched, including the otherwise-foldable "Oracle SQL" — never risks
losing a real, distinct product to simplify a borderline case sitting next to it.

### 2. `_group_etl_transformation_types` — "Router/Lookup/Aggregator/Joiner/Expression Transformation" → "Informatica Transformations (...)"

Detects 2+ items matching "`<Type>` Transformation(s)" and groups them under a vendor-prefixed
label. The vendor name (Informatica/Talend/SSIS/DataStage/Ab Initio) is drawn *only* from another
skill already present in the same list — never guessed. Confirmed by test: with no such vendor
skill present, falls back to the generic "Transformations" label rather than inventing one.

## Before / after (both confirmed examples, run through the real pipeline)

```
BEFORE: Informatica IDMC, Router Transformation, Lookup Transformation, Aggregator Transformation,
        Joiner Transformation, Expression Transformation
AFTER:  Informatica IDMC, Informatica Transformations (Router, Lookup, Aggregator, Joiner, Expression)
  6 items -> 2. Every transformation type preserved as a named sub-item.

BEFORE: Oracle, Oracle SQL, Oracle DB
AFTER:  Oracle
  3 items -> 1. No information lost - "SQL"/"DB" added nothing beyond "this is a database product".

Safety check (not in the given examples, verified anyway):
BEFORE: Oracle, Oracle SQL, Oracle Fusion
AFTER:  Oracle, Oracle SQL, Oracle Fusion  (unchanged - Oracle Fusion is a real, distinct product)
```

## Requirement-by-requirement

- Genuine technologies/platforms/frameworks/languages/databases/cloud services/ETL/BI capabilities
  preserved — both passes only ever fold or group, never delete; every original term is either its
  own surviving entry or a named sub-item inside a grouped one.
- No duplicated experience responsibilities in Technical Skills — unchanged from last round
  (`_RESPONSIBILITY_PHRASES`), no new evidence to extend it this round.
- Related technologies grouped — both new examples, implemented and tested.
- Never fabricate — the vendor name in transformation-grouping is always drawn from an
  already-present skill, never invented; "Oracle" in vendor-suffix-folding is always a literal
  substring of an already-present skill.
- Never remove genuine technologies — verified via the Oracle Fusion safety test: a group is folded
  only when *every* member is provably redundant, never partially.

## Tests

8 new tests in `tests/test_skill_intelligence.py`: vendor-suffix folding (collapses redundant
naming, synthesizes the bare form when absent from input, never collapses a distinguishing product,
no-op on a lone mention), ETL transformation grouping (groups under a present vendor anchor, falls
back to the generic label without one, no-op on a lone mention), and a combined
`_consolidate_related_skills` test against both confirmed examples together.

## Regression

Full suite: **921 passed** (913 before this change + 8 new), 10 deselected (live_gemini), 0 failed.

Stop, per instruction.
