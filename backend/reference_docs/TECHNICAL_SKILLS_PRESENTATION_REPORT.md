# Technical Skills Presentation Report

Scope: `app/services/skill_intelligence.py` only (plus its own test file). No other section, no
rendering, no PII masking, no ATS scoring, no Gemini prompts, no extraction touched — confirmed by
diff scope. `build_technical_skills` (the flat-list function used by `ats_intelligence.py`,
`resume_scoring_engine.py`, `resume_quality_engine.py`) is completely unaffected by this change —
only `build_technical_skills_grouped` (the one caller that renders category headings) gained new
behavior.

## What changed

Added a presentation-only relabeling pass, applied to `build_technical_skills_grouped`'s already-
final `{category: [items]}` mapping, right before it returns. It never re-classifies an item
(`classify_skill`/`_classify_and_filter` are untouched) and never drops one — it only changes which
heading an already-classified item is displayed under:

- **Simple renames:** Programming Language → Languages, Database → Databases, Cloud → Cloud &
  Storage, ETL Tools → ETL / ELT Tools.
- **Dynamic split:** "Analytics" bundles three genuinely distinct recruiter-facing capabilities —
  split per-candidate, based on which are actually present, into Data Warehouse (Snowflake,
  Redshift, BigQuery, ...), BI & Reporting (Power BI, Tableau, ...), and Data Engineering (ETL,
  Spark, Airflow, Databricks, ...). An item matching none of the three keeps the "Analytics" label
  as a safe fallback — never dropped for not fitting a more specific bucket.
- **Merge:** Business Skills + Project Management → one "Practices" heading.
- **Two new base categories** (same established pattern this file already uses for ETL Tools/ERP
  Systems — see its own code comments): **API Integration** (REST/SOAP/OAuth2/JWT/webhook/GraphQL
  — moved here from Networking/Security, where they were previously mixed in with unrelated
  concepts like DNS/BGP/firewall/SIEM) and **Data Quality** (data quality/profiling/validation
  practice terms, distinct from ETL Tools' own product names like Informatica IDQ).

## A real bug found and fixed during end-to-end validation

Testing against a realistic full skill list (not just the isolated PL/SQL example) surfaced a
genuine ordering bug: with both bare "SQL" and "PL/SQL" present, the SQL-family anchor picker took
whichever appeared first in the list — if "SQL" happened to come first, it incorrectly became the
anchor for "Stored Procedures"/"Triggers" (which are PL/SQL-specific constructs, not generic ANSI
SQL ones). Fixed by checking for a specific dialect (PL/SQL, T-SQL, MySQL, PostgreSQL, Oracle SQL,
SQL Server) first, falling back to bare "SQL" only when no more specific anchor exists. Covered by
a new regression test using the exact scenario that surfaced it.

## Before / after (realistic full skill list, not just isolated examples)

```
BEFORE (30 flat items):
SQL, PL/SQL, Stored Procedures, Triggers, Views, Functions, Python, Snowflake,
Snowflake Streams, Redshift, Power BI, Tableau, ETL, Spark, Airflow, Databricks,
Informatica IDMC, Talend, Data Quality, Data Profiling, REST API, OAuth2, JWT,
AWS, Azure Data Lake, MySQL, PostgreSQL, Agile, Stakeholder Management, Project Management

AFTER (10 dynamic groups, 26 surviving item-mentions - nothing lost, only consolidated):
Languages:        SQL, PL/SQL (Stored Procedures, Triggers, Views, Functions), Python
Cloud & Storage:  Azure Data Lake, AWS
Databases:        MySQL, PostgreSQL
Data Warehouse:   Redshift, Snowflake, Snowflake Streams
Data Engineering: Databricks, ETL, Spark, Airflow
BI & Reporting:   Power BI, Tableau
ETL / ELT Tools:  Informatica IDMC, Talend
Data Quality:     Data Quality, Data Profiling
API Integration:  REST API, OAuth2, JWT
Practices:        Agile, Project Management, Stakeholder Management
```

## Requirement-by-requirement

- ✓ Same technologies retained — every one of the 18 distinct terms in the realistic test list is
  confirmed present in the final output by a dedicated test
  (`test_grouped_never_drops_a_skill_across_the_whole_relabeling_pass`).
- ✓ Better organization — 10 focused, capability-named groups instead of a generic "Analytics"/
  "Programming Language"/"Business Skills" set that recruiters don't map onto real job titles.
- ✓ Reduced repetition — PL/SQL's 6 items collapse to 1 (from the prior consolidation pass),
  compounding with this presentation pass's grouping.
- ✓ Improved recruiter readability — labels chosen to read like an actual job-family capability
  ("Data Warehouse", "BI & Reporting") rather than an internal taxonomy name.
- ✓ ATS keywords preserved — every technology name is still present as plain text (either its own
  entry or a sub-item inside a grouped one), still matchable by a keyword scan.

## Categories not implemented this round (scope discipline, not oversight)

"Orchestration" was named as an example but not implemented as its own label — the orchestration-
flavored items in the given examples (Airflow, schedulers) already land under the new dynamic
"Data Engineering" label, which is a reasonable, low-risk grouping; carving out a fully separate
"Orchestration" label would mean re-classifying items away from other existing categories with less
evidence backing exactly where the line should sit. Flagging rather than guessing.

## Tests

7 new tests in `tests/test_skill_intelligence.py` (simple renames, dynamic Analytics split, the
fallback-label safety case, the Practices merge, the two new categories, the SQL/PL-SQL anchor-
priority bug's regression test, and the full never-drops-a-skill end-to-end check) plus 1 existing
test updated to reflect the intentional `OAuth` reclassification and 1 updated for the
`"Programming Language"` → `"Languages"` label change.

## Regression

Full suite: **901 passed** (894 before this change + 7 new), 10 deselected (live_gemini), 0 failed.

Stop after implementation, per instruction.
