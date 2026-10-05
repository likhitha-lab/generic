# PII Masking / Unmasking Architecture (design only — no code changed)

Goal: Gemini never receives real personal or business-confidential data. The candidate's final
PDF/DOCX still shows everything, unmasked. Masking is invisible to the candidate and to every
downstream module that already exists (renderer, scoring, dashboard) — it sits only around the
Gemini call boundary.

## A correction to the stated current flow, before designing the fix

The objective describes today's flow as:

```
Upload → Extraction → Normalization → Prompt Generation → Gemini → Resume Generation → PDF/DOCX
```

In the actual codebase, **"Extraction" already IS a Gemini call** (`extraction_pipeline.py` sends
the raw document text to Gemini to pull out structured fields — contact, summary, skills,
experience, projects). There is a separate, earlier, fully local/deterministic step — pulling
plain text out of the PDF/DOCX file itself (`pdf_parser.py`/`docx_parser.py`, no AI, no network
call) — that the stated flow's "Extraction" box silently merges with the Gemini-based structured
extraction that follows it.

This distinction is the crux of the whole design: if masking is inserted *after* "Extraction" (as
the stated flow would suggest), the raw text — full of real names, emails, phone numbers, company
and client names — has *already* been sent to Gemini once, during structured extraction. Masking
after that point protects nothing. **Masking has to sit between local text extraction and the
first Gemini call, not after it.**

## Classification

| # | Category | Mask? | Why | Risk if NOT masked | Risk if masked |
|---|---|---|---|---|---|
| 1 | **Personal Information** (candidate name; DOB/gender/marital status/nationality/photo/gov-ID if ever present) | **Yes** | Name is the single most re-identifying field in the document; DOB/gender/marital-status/nationality/gov-ID are the kind of field most privacy regimes single out for extra protection | Candidate's real identity leaves the org boundary attached to their full career history, sent to a third-party API (Google) the candidate never separately consented to; regulatory exposure (GDPR/CCPA/India DPDP Act) | Essentially none — name is a pure pass-through field everywhere in this pipeline (never rephrased by Gemini in any observed output); DOB/gender/etc. are never used for career-content rewriting either |
| 2 | **Company Information** (past/current employer names) | **Yes** | Employer name + role + dates together are highly re-identifying even without the candidate's name attached | Exposes the candidate's full employment history, tied to specific real companies, to a third-party API | Low — company name is preserved verbatim in every observed generation, never used to *shape* wording beyond the rare "domain/scale" framing (mitigable later by passing a coarse industry tag alongside the placeholder if quality data ever shows a real gap — not needed for a first version) |
| 3 | **Client Information** (end-client named in an Employment note or a Project's Client field — e.g. "Hyundai / Mobis India", "Kaiser Permanente") | **Yes — higher priority than Company** | This is a THIRD PARTY's business relationship, not just the candidate's personal data. A staffing/consulting candidate naming an end-client on their resume may be disclosing something the candidate's employer is contractually bound (NDA/MSA) to keep confidential — sending it to an external LLM API compounds that exposure | Breach of a business confidentiality obligation the candidate may not even be aware they're bound by; legal/reputational risk to both the employer and the end client, not just the candidate | Low — same reasoning as Company Information; client name is a pass-through field, confirmed across every real sample resume seen this session |
| 4 | **Project Information** (title, description, technologies, responsibility bullets) | **Partial** | The *technical content* (technologies used, what was built) is the candidate's own skill demonstration — safe and useful for Gemini to see, it's the whole point of the resume. Only the parts that name a real client/company inline (a project titled after a real client, or a description that mentions one) carry the same risk as #3 | A project description that embeds a real client/company name leaks the same confidential relationship as an explicit Client field, just less obviously | Masking the WHOLE project (title + description) rather than just the embedded client/company name would strip useful technical signal Gemini needs to write a good bullet — mask only the detected entity substrings within project text, not the whole field |
| 5 | **Contact Information** (email, phone, LinkedIn, GitHub, portfolio URL, physical address) | **Yes — highest priority** | The single most directly actionable category if ever leaked (enables real-world contact, phishing, social engineering); canonical "directly identifying" PII under most privacy regimes | Direct real-world contact info sent to a third-party API | None — 100% pass-through fields, never rewritten anywhere in this pipeline (confirmed by `identity_validation.py`'s own design, which already treats these as verbatim-preserve-only) |
| 6 | **Optional Sensitive Information** (gov-ID, bank/financial details, health/medical info, religion/political affiliation, salary figures, a reference's personal contact info, photos) | **Yes, and consider stripping outright rather than mask-and-restore** | Several of these are GDPR "special category" data (health, religion, political affiliation) with materially stricter handling requirements than ordinary PII; none of them belong on a professional resume by most responsible-hiring standards in the first place | Highest-severity category if ever leaked — identity theft risk (gov-ID/financial), discrimination risk (health/religion/political), competitive sensitivity (salary) | None for quality — none of these fields are ever used for career-content rewriting. Mask-and-restore (not an unconditional strip) is still the safer *default*, since some source resumes legitimately include a photo/DOB by cultural convention and the final PDF should still reflect the candidate's own original document if that's what they uploaded — but this category is the one most worth a policy conversation about stripping rather than merely masking |

## Placeholder format

```
<CANDIDATE_NAME_1>
<EMAIL_1>
<PHONE_1>
<LINKEDIN_1>
<GITHUB_1>
<PORTFOLIO_1>
<COMPANY_1>  <COMPANY_2>  ...
<CLIENT_1>   <CLIENT_2>   ...
<PROJECT_1>  <PROJECT_2>  ...
```

Rules:
- One counter **per category, per resume** (not global) — the first distinct company found is
  `COMPANY_1`, the second distinct company is `COMPANY_2`, etc.
- **Same real string anywhere in the document → same placeholder, every time.** This matters
  concretely: a real resume in this session (Vinay Mannarapu's) names "Innova Solutions" as both
  the Employment section's company AND the Project section's employer context — both instances
  must map to the exact same token, or Gemini would (correctly) treat them as two different,
  unrelated companies and Entity Linking's already-existing company-similarity dedup would no
  longer see them as the same entity either.
- Placeholders are plain, unhyphenated, bracket-delimited tokens — deliberately NOT a hash or
  anything derived from the real value, so a placeholder alone (if it ever leaked) reveals nothing
  and can't be correlated against other resumes.

## Mapping table

One mapping table per resume-processing request, e.g.:

```json
{
  "CANDIDATE_NAME_1": "Vinay Mannarapu",
  "EMAIL_1": "vinaymannarapusql07@gmail.com",
  "PHONE_1": "+91-6303654009",
  "COMPANY_1": "Innova Solutions",
  "CLIENT_1": "Hyundai / Mobis India",
  "PROJECT_1": "Customer Vehicle Analysis - Mexico"
}
```

- Lives **only in memory** for the lifetime of that one request. Never written to disk, logs, or
  the DB in a form that links placeholder → real value in plaintext.
- Never reused across resumes or across regenerate/re-upload requests for the *same* resume — a
  fresh table every time, so a placeholder from one run can never be replayed against another
  run's mapping.

## New secure flow

```
Resume Upload
  ↓
Local Text Extraction  (pdf_parser.py / docx_parser.py - EXISTING, unchanged, no Gemini, no network call)
  ↓
PII Detection            [NEW]
  ↓
PII Masking + Mapping Table Creation   [NEW]
  ↓
Gemini-based Structured Extraction  (EXISTING extraction_pipeline.py - now only ever sees placeholders)
  ↓
Normalization  (EXISTING, unchanged - pure text cleanup, identity-agnostic either way)
  ↓
Canonical Model / Entity Linking / Section Recovery  (EXISTING, unchanged - similarity-based dedup
  works identically on placeholders; exact-placeholder matches are if anything MORE precise for
  company/client dedup than fuzzy-matching slightly-differently-phrased real names)
  ↓
Resume Optimizer's Gemini rewriting calls (summary/bullet polishing)  (EXISTING - still only ever
  sees placeholders; masking protects this stage too, not just initial extraction)
  ↓
PII Unmasking          [NEW]  - restore real values from the mapping table
  ↓
Identity Validation, ATS scoring, Evaluation Dashboard  (EXISTING, unchanged - now run on the
  real, unmasked content, exactly as they do today; a name/contact check re-run here also verifies
  the unmask step itself didn't silently fail)
  ↓
PDF / DOCX Rendering  (EXISTING, unchanged - renders real, unmasked content, no different from today)
```

No stage is reordered; two new stages are inserted around the existing Gemini boundary, and
everything downstream of unmasking runs exactly as it does today.

## Detection: two tiers, honestly different in difficulty

**Tier 1 — achievable with zero new dependencies**, using local logic this codebase already has,
running before any Gemini call:
- Candidate Name — `person_extractor.py`'s existing local name-resolution chain already runs
  before extraction and needs no changes to *locate* the name; masking only adds "replace this
  exact string everywhere it appears in raw_text."
- Email / Phone / LinkedIn / GitHub / Portfolio — `app/utils/contact.py`'s existing regex patterns
  already find these deterministically, pre-Gemini.

This tier alone covers Contact Information and (name-wise) Personal Information — the two
highest-priority, most directly-identifying categories — using capabilities that already exist.

**Tier 2 — requires a genuinely new local capability**, and this needs to be said plainly rather
than hand-waved: Company/Client/Project names are arbitrary proper nouns with no reliable regex
pattern. Reliably finding them in free text before Gemini ever sees that text requires either:
- a small, fully local Named Entity Recognition model (e.g. spaCy's `en_core_web_sm`, ORG/GPE
  entity types) — runs offline, no external API call, deterministic given fixed model weights; or
- a lighter-weight heuristic/gazetteer approach (known company-name suffixes — Inc./LLC/Corp/
  Solutions/Technologies/Systems/Pvt Ltd — combined with proximity to a "Client:" label or the
  position in the document a company name is expected to occupy), lower accuracy, zero new model
  dependency.

This is the one place in the whole design where "no new dependency" and "reliable masking" are in
real tension, and it should be resolved as its own decision, not assumed away.

## Unmasking mechanics and failure handling

Unmasking is a string-replace pass over Gemini's JSON response using the same mapping table.
Real risk: Gemini could rephrase, drop, or malform a placeholder token in its output (the same
class of non-determinism this whole session has repeatedly found in other contexts). Mitigation,
consistent with this codebase's existing "deterministic fallback, never silent" pattern (the same
discipline `section_recovery.py` already applies): if an expected placeholder is not found verbatim
in Gemini's response for a field, fall back to that field's original pre-mask value from Stage 1
extraction rather than leaving a broken `<COMPANY_1>` literal in the final rendered resume.

## What does not change

Frontend, auth, storage, DB schema, API contracts, file upload flow, deployment, `file_generator.py`'s
rendering, `resume_scoring_engine.py`, `resume_comparison.py` — none of these need to know masking
exists. The only new state is the per-request mapping table, held in memory, never persisted.

## Open questions before implementation

1. Tier 2 detection approach — local NER model vs. gazetteer/heuristic (accuracy vs. dependency
   tradeoff above).
2. Optional Sensitive Information — mask-and-restore (default recommended here) vs. strip outright.
3. Whether Company/Client masking should carry a coarse hint (industry/size tag) to preserve any
   quality Gemini currently gets from knowing the real name — no evidence yet that it does; not
   recommended for a first version without a confirmed quality regression to justify it.

Waiting for approval before any of this becomes an implementation plan.
