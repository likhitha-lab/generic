# Dataflix Resume Standard (reverse-engineered)

Analysis only — no code changed. Sources:
1. `P VENKATESHWAR REDDY.pdf` — Dataflix-branded, ETL Developer (~9yrs).
2. `Venkat.pdf` — Dataflix-branded, RPA/UiPath Developer (~9yrs).
3. The pasted "ideal resume" text — Dataflix-style, Data Engineer/Informatica.

Plus ground truth read directly from `app/services/file_generator.py`'s own typography/layout
constants, which the code already labels "Talent Acquisition-approved formatting pass" — used
here as the authoritative source for anything (fonts, point sizes, spacing, section order) the
three sample PDFs' extracted text can't reveal on its own (PDF text extraction carries no font/
spacing metadata).

**Honest caveat:** only 3 samples exist, and they are not fully consistent with each other (see
"Where the ideal sample conflicts" below). Where they disagree, this document treats the
code-confirmed standard and the 2-of-3 majority pattern as authoritative, not the outlier.

---

## 1. Header
- Candidate name, left-aligned, bold, 20pt — largest text on the page, fixed size regardless of
  resume length/density.
- Contact line directly under the name: `email | phone` (LinkedIn/GitHub/portfolio appended with
  the same `|` separator when present), 9.5pt.
- DATAFLIX logo, top-right, fixed size, never resized/repositioned by content length.
- Full-width horizontal divider line beneath the header, before the first section.
- All three samples match this exactly.

## 2. Section order and heading names (fixed, never adaptive)
1. Professional Summary
2. Technical Skills
3. Educational Qualifications
4. Certifications (only if present in source)
5. Tools
6. Professional Experience
7. Projects Handled
8. Achievements
9. Languages / Publications / Volunteer Experience / Leadership (only if present)

Confirmed identical across all 3 samples except:
- The "ideal" sample uses **"Project Details"** instead of "Projects Handled", and omits
  Certifications/Tools headings even where Reddy/Venkat show them as separate sections. Treated
  as the outlier — 2 of 3 samples, and the current code, use "Projects Handled".
- Reddy has a Leadership section (his own content warranted it); Venkat doesn't. Section
  *presence* is content-driven — a resume without leadership content omits it, not renders empty.

## 3. Professional Summary
- 3–5 sentences, no bullets, single paragraph (Reddy, Venkat).
- States: years of experience, domain/specialization, core tools/platforms, 1–2 standout
  strengths (compliance, leadership, scale, measurable impact).
- No numbers/metrics inside the summary itself — metrics live in Achievements/Experience bullets.
- Conflict: the "ideal" sample renders the summary as 4 bullet points instead of a paragraph.
  Outlier (1 of 3) — paragraph form is the majority/current pattern.

## 4. Technical Skills
- Grouped under bold category subheadings, each followed by a bullet list.
- Category labels are plain, domain-neutral nouns: Programming Language, Framework, Cloud,
  Database, Analytics, ETL Tools, Security, Project Management, Other Skills (Reddy/Venkat).
- "Other Skills" is always present as the final catch-all category — nothing is ever dropped for
  lacking a named bucket.
- The "ideal" sample uses friendlier, tool-specific labels instead ("Data Warehouse", "API
  Integration", "Cloud & Storage"). Outlier — not matched by the other two samples or by the
  current category taxonomy.

## 5. Employment Details (Professional Experience)
- Per job: Company name, dates (right-aligned or same line as company), Role/Title, then bullet
  points.
- Every bullet is an outcome/action statement, ideally with a quantified result (%, count, time
  saved) where the source supports one — never invented if the source doesn't state one.
- A job with no bullets in the source renders as just Company/Role/Dates (Venkat — confirmed
  correct, not a defect, when the source itself has no per-job detail).
- Client, if stated for a job (not a project), renders as a `Note: Client: X` line under that
  job's bullets (Vinay Mannarapu's Original — same convention, at the Employment level, distinct
  from a Project's own Client field).
- Conflict: the "ideal" sample collapses this entire structure into two flat one-line statements
  with no company/date/bullet structure at all. This is the clearest and most serious conflict in
  the whole set — it directly contradicts the "preserve full hierarchy: company, role, duration,
  every bullet" rule already enforced elsewhere in this pipeline, and preservation is explicitly
  prioritized over style match. Not adopted as standard.

## 6. Education
- Degree, institution, graduation year — one line or tight 2–3 line block per entry, no bullets.
- Percentage/CGPA included when the source states it.
- Consistent across all 3 samples.

## 7. Projects Handled
- Per project: Title, Client (if stated), Description (1–3 sentences), `Technologies: ...` line,
  then responsibility bullets.
- A single client can have multiple named project variants (e.g. two regional deployments of the
  same engagement) — each still gets its own Title/Description/Technologies/bullets block, not
  merged into one.
- Client line placement: directly under the title, before the description.

## 8. Achievements
- Short, standalone, quantified bullets (cost/time saved, SLA %, scale) — each independent of the
  others, not narrative.
- Should not be a near-verbatim restatement of an Experience/Project bullet already shown
  elsewhere (an already-tracked, isolated finding from the Venkat case — not yet confirmed
  recurring).

## 9. Bullet writing style
- Past-tense action verb opens every bullet (Led, Engineered, Implemented, Reduced, Built,
  Architected, Developed, Profiled, Managed).
- One sentence per bullet, no semicolon-joined compound bullets.
- Quantified impact where the source supports it, phrased naturally ("reducing query execution
  time by ~35%"), not bolted on as an afterthought.
- Technical nouns kept exact/capitalized as named (Snowflake Streams, IDMC CAI, RBAC) — never
  genericized.

## 10. Typography (code-confirmed, both DOCX and PDF)
- Font: Calibri throughout (PDF falls back to Helvetica only if no licensed Calibri TTF is found
  on the host — same typeface intent either way).
- Sizes: 20pt bold name, 14pt bold section headings, 11pt body — fixed constants, never varied by
  resume length.
- Heading color: near-black (`#1A1A1A`), deliberately not Word's default theme blue.
- Margins: 0.85in on all sides, both formats, both density tiers (margins never shrink to fit more
  content).

## 11. Bullet / paragraph spacing (code-confirmed)
- Bullet indent: 0.5in text-start / 0.25in hanging indent — Word's own out-of-box "List Bullet"
  default, confirmed against a real professionally-formatted resume's `numbering.xml`.
- Two density tiers exist, "normal" and "compact" — a long resume automatically drops to the
  compact tier to fit its page cap. Only spacing/line-height tightens between tiers (line spacing
  1.25 → 1.1, space-after 6pt → 3pt, heading gaps 18/8pt → 12/5pt); font size and typeface are
  **identical** in both — a compact-tier resume reads denser, never smaller-printed.

## 12. Alignment
- Name, contact line, and all section content: left-aligned.
- Logo: right-aligned, fixed position.
- Job/project dates: right-aligned on the same line as the company/title where space allows.

## 13. White space
- Section heading: extra space before (18pt/12pt normal/compact), moderate space after (8pt/5pt).
- Between entries within a section (jobs, projects): a fixed entry gap, denser in compact tier.
- No blank sections ever render — an empty section is skipped entirely, not shown with a heading
  and no content.

## 14. ATS friendliness
- Single-column layout, no tables/text-boxes for body content (only the header uses a fixed
  logo+text layout) — single-column is what most ATS parsers handle reliably.
- Standard section heading names (no cutesy/non-standard labels) aid ATS section detection —
  another reason "Projects Handled"/"Professional Experience"/"Technical Skills" (majority
  pattern) should stay standard over "Project Details"-style alternates.
- Calibri/Helvetica — both are ATS-safe, widely-supported fonts with no embedding risk.
- Skills grouped under plain-noun categories (not clever/marketing labels) so exact keyword
  matching against a job description still works.

---

## Common patterns identified (summary)

1. **Formatting rules:** Calibri, 20/14/11pt fixed hierarchy, near-black headings, 0.85in margins,
   0.5in/0.25in bullet indent, left-aligned body / right-aligned logo, single-column.
2. **Writing style:** past-tense action-verb bullets, one outcome per bullet, quantified where the
   source supports it, technical nouns kept exact.
3. **Section hierarchy:** Summary → Technical Skills → Education → Certifications → Tools →
   Professional Experience → Projects Handled → Achievements → (Languages/Publications/
   Volunteer/Leadership if present). Fixed order, content-driven presence.
4. **Bullet patterns:** action verb + what was done + quantified result, no compound/semicolon
   bullets.
5. **Project structure:** Title → Client → Description → Technologies line → responsibility
   bullets; multiple named variants under one client each get their own block.
6. **Summary structure:** single paragraph, 3–5 sentences, years + domain + tools + 1–2 standout
   strengths, no embedded metrics.

## Where the "ideal" sample conflicts with the majority/code standard
- Flat, dateless, bulletless Professional Experience (rejected — direct information-loss
  conflict with the preservation requirement).
- Bullet-point summary instead of paragraph (outlier, 1 of 3).
- "Project Details" heading instead of "Projects Handled" (outlier, 1 of 3).
- Tool-specific skill category labels instead of plain-noun categories (outlier, 1 of 3).

Waiting for approval before any of this informs a code change.
