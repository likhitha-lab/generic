# Style Decision Matrix

Analysis only — no code changed. Compares three sources for every listed feature:
- **Current ResumeBuilder** — `app/services/file_generator.py`'s actual implemented behavior
  (code-confirmed, not guessed) plus the two real generated/benchmark PDFs (`P VENKATESHWAR
  REDDY.pdf`, `Venkat.pdf`), which were themselves produced by this same rendering system.
- **Ideal Dataflix resume** — the pasted "ideal resume" text, now treated as the primary style
  reference per instruction, not just an equal-weight sample.

Rule applied throughout: the ideal resume wins ties on pure style with no information-loss
consequence. It never wins where adopting it would drop candidate information, hierarchy, or ATS
parseability — preservation outranks style, per explicit instruction.

Note on evidence limits: the ideal resume was provided as plain text, not a formatted PDF/DOCX —
it carries zero font/size/spacing/margin/logo-position information. For those features, the only
real visual evidence in the whole set comes from the two benchmark PDFs, which already match the
current code exactly. Those are marked ADOPT (already-standard, uncontradicted), not "needs
confirmation" — there is no genuine disagreement to resolve, only one source that can't speak to
the question.

---

### Feature: Professional Summary format
- **Current:** Single paragraph, exactly 3 sentences (hard rule in `prompts.py`).
- **Ideal:** 4 bullet points.
- **Recommendation: ADOPT** (bullet form).
- **Technical impact:** Change the Summary generation rule from "no bullet points, exactly 3
  sentences" to "3–5 bullet points"; change the renderer's Summary section from a single
  Paragraph flowable to a bullet list (same `_bullet_list` helper already used for every other
  section).
- **Files:** `app/services/prompts.py` (rule 2), `app/services/file_generator.py` (Summary
  section rendering in both PDF and DOCX builders), `app/services/resume_scoring_engine.py`
  (its "summary" scoring dimension currently checks sentence count/length on a paragraph string —
  needs to accept a list).
- **Risk of regression:** Low-medium. `resume_scoring_engine`'s summary dimension and
  `evaluation_dashboard`'s `summary_completeness_pct` both read the summary as one string today;
  both need updating together or completeness scoring silently breaks for every resume.

### Feature: Technical Skill category names
- **Current:** Plain-noun categories (Programming Language, Framework, Cloud, Database,
  Analytics, ETL Tools, Security, Project Management, Other Skills, ...) — one fixed,
  domain-general taxonomy in `skill_intelligence.py`, used for every resume regardless of role.
- **Ideal:** Tool/domain-specific labels (Data Warehouse, ETL/ELT Tools, Data Quality, API
  Integration, Cloud & Storage, Orchestration, Practices) — a labeling scheme that reads as
  bespoke to one data-engineering resume.
- **Recommendation: NEEDS CONFIRMATION.** No information-loss angle either way (same skills,
  different bucket names), so this isn't a Reject. But adopting it isn't a clean Adopt either:
  it's unclear whether Talent Acquisition wants this exact label set applied universally (an RPA
  candidate like Venkat or an ETL developer like Reddy wouldn't naturally fit "API Integration" /
  "Orchestration"), or whether it's specific to data-engineering resumes only. Needs a direct
  answer before committing to either a full taxonomy rename or a role-adaptive one (the latter is
  a materially bigger change).
- **Technical impact (if adopted universally):** Rename `_CATEGORY_RANK_ORDER` and
  `_CATEGORY_KEYWORDS` in `skill_intelligence.py`; re-audit which keywords sort into which
  renamed bucket.
- **Files:** `app/services/skill_intelligence.py`, its tests (`test_skill_intelligence.py`).
- **Risk of regression:** Medium — the category order also drives skill *ranking/priority*
  (`_CATEGORY_RANK_ORDER` is explicitly documented as the ranking order too), so a rename that
  also reshuffles order changes which skills surface first, not just their label.

### Feature: Section heading name — "Projects Handled" vs "Project Details"
- **Current:** "Projects Handled" (code constant, matches 2 of 2 real benchmark PDFs).
- **Ideal:** "Project Details".
- **Recommendation: ADOPT** ("Project Details") — pure label swap, zero information-loss
  consequence, negligible ATS difference (both are conventional resume heading phrasings).
- **Technical impact:** One string constant change.
- **Files:** `app/services/file_generator.py` (`_DEFAULT_SECTION_SEQUENCE` tuple).
- **Risk of regression:** Very low. Any test asserting the literal text "Projects Handled" needs
  updating in the same change.

### Feature: Section heading name — "Educational Qualifications" vs "Education Qualifications"
- **Current:** "Educational Qualifications".
- **Ideal:** "Education Qualifications" (missing "-al").
- **Recommendation: NEEDS CONFIRMATION**, leaning REJECT-as-typo. This reads as an inconsistency
  in the source document itself rather than a deliberate style decision — no reason to prefer the
  grammatically rougher form. Flagging rather than silently assuming either way.
- **Technical impact:** Trivial if adopted (one string).
- **Files:** `app/services/file_generator.py`.
- **Risk of regression:** Negligible.

### Feature: Employment Details layout
- **Current:** Company, Role, Duration, every bullet point preserved in full (`prompts.py` rule 7:
  "preserve the FULL hierarchy... do not cap the number of bullet points").
- **Ideal:** Two flat one-line statements ("Currently joining Dataflix as an Informatica
  Developer" / "Working as a Data Engineer at Innova Solutions") — no company/date structure, no
  bullets at all.
- **Recommendation: REJECT.** This is the one unambiguous, severe conflict in the whole matrix.
  Adopting it would delete dates, delete every responsibility bullet, and delete the
  company/duration hierarchy for every candidate — direct, large-scale information loss,
  explicitly ranked below style by instruction. Not adopted regardless of how many future ideal
  samples show this pattern, unless the preservation requirement itself is revisited.
- **Technical impact:** N/A (not adopting).
- **Files:** N/A.
- **Risk of regression:** N/A.

### Feature: Project Details layout — Client/Technologies line placement
- **Current:** `Client: X` on its own explicit labeled line (just added this pipeline round),
  then `Technologies: X` on its own line.
- **Ideal:** Client name folded into the same line as Technologies (e.g. "Hyundai | IDMC CDI +
  CAI, Snowflake, CDQ, AWS"), with a separate blanket "Client: ..." note once at the top of the
  whole Experience section rather than per project.
- **Recommendation: REJECT adopting the combined line** — current's explicit `Client:` /
  `Technologies:` labels are more ATS-parseable (an unlabeled "Hyundai | ..." line is ambiguous to
  a keyword/section parser: could read as a location, a client, or a stray technology term).
  Keep current's explicit-label approach.
- **Technical impact:** N/A (not adopting).
- **Files:** N/A.
- **Risk of regression:** N/A.

### Feature: Bullet writing style
- **Current:** Past-tense action verb, one sentence per bullet, quantified impact where the
  source supports it (`prompts.py` tone/completeness rules + `experience_intelligence.py`'s
  `diversify_action_verbs`/`prioritize_bullets_with_impact`).
- **Ideal:** Same pattern exactly.
- **Recommendation: ADOPT** (already matches — no change needed).
- **Technical impact:** None.
- **Files:** None.
- **Risk of regression:** None.

### Feature: Fonts
- **Current:** Calibri (Helvetica fallback if no licensed TTF found), code-confirmed, matches both
  benchmark PDFs.
- **Ideal:** No evidence — plain text carries no font information.
- **Recommendation: ADOPT** (current stands — uncontradicted by any real evidence).
- **Technical impact / Files / Risk:** None — no change proposed.

### Feature: Font sizes
- **Current:** 20pt name / 14pt heading / 11pt body, fixed constants, never varied by resume
  length.
- **Ideal:** No evidence.
- **Recommendation: ADOPT** (current stands).

### Feature: Bullet indentation
- **Current:** 0.5in text-start / 0.25in hanging indent (Word's own default, confirmed against a
  real resume's `numbering.xml`).
- **Ideal:** No evidence.
- **Recommendation: ADOPT** (current stands).

### Feature: Section spacing
- **Current:** 18pt/8pt heading before/after (normal tier), 12pt/5pt (compact tier).
- **Ideal:** No evidence.
- **Recommendation: ADOPT** (current stands).

### Feature: Margins
- **Current:** 0.85in all sides, both formats, both density tiers, never shrinks.
- **Ideal:** No evidence.
- **Recommendation: ADOPT** (current stands).

### Feature: White space / blank-section handling
- **Current:** Empty sections never render (heading + content both skipped together).
- **Ideal:** Consistent with this (no empty headings shown).
- **Recommendation: ADOPT** (already matches).

### Feature: Logo placement
- **Current:** Fixed top-right, fixed size, never resized/repositioned by content length.
- **Ideal:** No evidence (no logo/header in the plain-text sample at all).
- **Recommendation: ADOPT** (current stands — matches both benchmark PDFs).

### Feature: Page density (normal/compact tiers)
- **Current:** Two tiers, spacing/line-height only tighten (1.25 → 1.1 line spacing, 6pt → 3pt
  space-after); font size and typeface never change between tiers.
- **Ideal:** No evidence (no page-count/visual signal in plain text).
- **Recommendation: ADOPT** (current stands).

### Feature: Certifications/Tools as separate sections vs folded into Skills
- **Current:** Separate "Certifications" and "Tools" headings when the source has content for
  them (matches both benchmark PDFs).
- **Ideal:** No separate Tools/Certifications headings — tool names appear inline inside the
  Technical Skills categories instead; no certifications shown at all.
- **Recommendation: NEEDS CONFIRMATION.** Can't tell whether this is a deliberate structural
  choice (fold Tools into Skills, drop Certifications as its own section) or simply this one
  candidate having no certifications and a resume author who happened to list tools under Skills.
  Given 2 of 3 real samples keep these as distinct sections, and the ideal sample's absence has an
  equally plausible content-driven explanation, this should not be acted on without a direct
  answer.
- **Technical impact (if adopted):** Would require removing the separate Tools/Certifications
  entries from `_DEFAULT_SECTION_SEQUENCE` and folding their content into the Skills grouping —
  a real, non-trivial restructuring, not a label change.
- **Files:** `app/services/file_generator.py`, `app/services/skill_intelligence.py`.
- **Risk of regression:** Medium-high if adopted — touches section presence logic used by every
  resume, not just a label.

---

## Summary

| Feature | Recommendation |
|---|---|
| Professional Summary format (bullets) | ADOPT |
| Technical Skill category names | NEEDS CONFIRMATION |
| "Projects Handled" → "Project Details" | ADOPT |
| "Educational Qualifications" spelling | NEEDS CONFIRMATION (likely typo) |
| Employment Details layout (flat, no dates/bullets) | REJECT |
| Project client/technologies combined line | REJECT (keep current explicit labels) |
| Bullet writing style | ADOPT (already matches) |
| Fonts / sizes / indentation / spacing / margins / logo / density | ADOPT (current stands, uncontradicted) |
| Tools/Certifications folded into Skills | NEEDS CONFIRMATION |

Waiting for approval before any ADOPT item becomes an actual code change.
