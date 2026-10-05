"""Prompt templates for the Gemini call sites.

Two flows use these:
  - The manual Resume Generator (`build_generate_prompt`) - user-supplied
    structured input, polished into a tone. One call, unchanged design.
  - The Resume Converter (upload/regenerate-from-upload) - now SPLIT into 5
    small, focused calls (contact/summary/skills-section/experience/
    projects) instead of one monolithic call, run concurrently and merged
    by app/services/extraction_pipeline.py. This is what fixes the
    "502 - Gemini response truncated" failure on long resumes: five small
    bounded-output calls instead of one call whose output size scales with
    the whole document, plus each call can be independently retried with a
    smaller/split prompt if it still truncates (see extraction_pipeline.py).

Both flows target the same fixed Dataflix template order: Header,
Professional Summary, Technical Skills, Education Qualifications,
Certifications, Tools, Professional Experience, Projects - plus, for the
Resume Converter flow only, a 6th extraction call (build_extras_prompt)
covering Achievements, Languages, Publications, Volunteer Experience, and
Leadership, whenever the source document actually mentions them (see that
function). Core Competencies, Hobbies, Interests, and References are still
deliberately never requested - those are recruiter-irrelevant/redundant
sections with no distinct field in this schema.
"""
import json

from app.schemas.resume import ResumeRequest

_COMPLETENESS_RULE = """
Completeness rule (applies to everything below): do NOT summarize, condense, shorten, or
selectively omit any entry. If there are 10 of something, return all 10, not "the top few". If
you are ever tempted to write "and others" / "etc." instead of listing items individually, that is
exactly the mistake this rule forbids. Missing information must be an empty string "" or empty
array [], never guessed, invented, or left out silently.
"""

_EXPERIENCE_ITEM_SHAPE = """{
    "company": "",
    "role": "",
    "duration": "",
    "points": [],
    "reason_for_leaving": "",
    "notes": "",
    "is_career_break": false,
    "break_detail": ""
  }"""

_PROJECT_ITEM_SHAPE = """{
    "title": "",
    "role": "",
    "client": "",
    "description": "",
    "technologies": "",
    "responsibilities": []
  }"""

_COMMON_RULES = """
STRICT RULES:

0. Completeness comes before everything else below:
- Do NOT summarize, condense, shorten, or selectively omit any section, entry, or bullet point.
- Every job in "Professional Experience" must appear - not just the most recent ones, not just
  the ones that seem most relevant. Every bullet point under every job must appear.
- Every skill, tool, certification, and education entry mentioned anywhere in the source must
  appear - do not pick a "top N" subset and drop the rest.
- If you are ever tempted to write "and other responsibilities" or similar instead of listing
  them individually, that is exactly the mistake this rule forbids - list every one.

1. Tone:
- Write the professional summary and every experience bullet point in a "{tone}" tone.
- Tone affects PHRASING ONLY, never completeness - do not shorten or drop a bullet point to make
  it fit the tone better.

2. Professional Summary:
- No bullet points
- Exactly 3 sentences
- Include years of experience, domain, and key strengths
- Always generate this section. (This section alone is meant to be a short abstract - the "no
  summarizing" rule above is about the rest of the resume: Experience/Education/Skills/etc. must
  be extracted in full, even though the Summary itself stays a short 3-sentence overview.)

3. Technical Skills:
- Extract EVERY skill mentioned anywhere in the source - do not cap this to a "top N" list.
- If skills are listed in a table with multiple rows/categories (e.g. separate rows for
  Languages, Operating Systems, Web Technologies, Databases, Frameworks), extract every item from
  EVERY row - it is a common mistake to only extract the first row or two and skip the rest.
- Always generate this section.

4. Education Qualifications:
- Generate this section only if the source mentions education. Include every degree/qualification
  mentioned, not just the most recent one.

5. Certifications:
- Include every certificate, badge, and notable achievement mentioned - do not cap or select a subset.
- Preserve each certification's full text exactly as written, including any date, validity period,
  or credential ID stated alongside it (e.g. "AWS Certified Solutions Architect (03/2024)" must keep
  the "(03/2024)" - do not trim it off while cleaning up the certification name).
- Generate this section only if the source mentions certifications.

6. Tools:
- Every professional tool/software/platform mentioned, not soft skills. Do not cap this list.
- Generate this section only if the source mentions tools/software/platforms.

7. Professional Experience:
- Generate this section only if the source mentions work experience.
- Include EVERY job/employer mentioned in the source, in the order they appear - do not omit
  older or seemingly-less-relevant jobs.
- Preserve the FULL hierarchy for every job: company, role, duration, and EVERY bullet point
  stated for it - do not cap the number of bullet points per job.
- Do not flatten a job down to just its company name - if the source states a role, duration, or
  responsibilities for that job, they MUST be included.
- Paraphrase each bullet point in the given tone, but do not invent bullets that aren't supported
  by the source, and do not drop or merge real ones for brevity.
- Carefully scan the ENTIRE document, including the space between job entries, for each of the
  following - these are commonly missed because they're brief and easy to skim past:
  - Reason for leaving (e.g. "left due to relocation", "company shut down", "role eliminated") ->
    put it in "reason_for_leaving" for that job. Leave "" if genuinely not stated - do not invent one.
  - Administrative notes tied to a job (e.g. PF/UAN number, notice period buyout, last working
    day) -> put it in "notes" for that job. Leave "" if genuinely not stated.
  - A career break/gap (sabbatical, freelance work, unemployment, further study, maternity/
    paternity leave, etc.) between two jobs -> represent it as its OWN entry in the "experience"
    array with "is_career_break": true, "company": "Career Break", "break_detail" set to whatever
    the source says about it (e.g. "UI/UX Firm", "Freelance Design"), and a short description in
    "points". A career break is easy to miss because it has no company name of its own - look for
    date gaps between consecutive jobs, not just explicit "career break" headings.

8. Projects (Project Handled):
- Generate this section only if the source mentions projects. Include every project mentioned.
- For each project, preserve: title, role (if stated), client/customer the project was delivered
  for (if stated - e.g. "Client: Acme Corp", or named in the project's own heading/description),
  FULL description (do not shorten or cap to a sentence count), technologies used (if stated), and
  every responsibility as its own bullet point (if stated). Leave a field "" / [] if the source
  doesn't state it - do not invent one.

9. Do NOT create or invent these sections under any circumstance: Core Competencies, Hobbies,
   Interests, References. If the source has such a section, ignore it - it has no distinct field
   in this schema and is recruiter-irrelevant/redundant with Summary/Skills anyway.
"""


def build_generate_prompt(data: ResumeRequest) -> str:
    """Manual Resume Generator - unchanged. User-supplied structured input,
    one call, polished into a tone."""
    payload = data.model_dump(exclude={"name", "email", "phone", "location", "links"})
    return f"""
You are a senior ATS resume expert.
{_COMMON_RULES.format(tone=data.tone)}

Return STRICT JSON with exactly these keys, in this exact section order. "experience" must be an
array with one object per job (and one object per career break, if any) using this exact shape,
and "projects" must be an array with one object per project using its exact shape - include every
key on every object, using "" / [] / false for anything not applicable:

{{
  "summary": "",
  "skills": [],
  "education": [],
  "certifications": [],
  "tools": [],
  "experience": [
    {_EXPERIENCE_ITEM_SHAPE}
  ],
  "projects": [
    {_PROJECT_ITEM_SHAPE}
  ]
}}

Candidate input:
{payload}
"""


# --- Resume Converter: 5 focused prompts, one per section --------------------
#
# Each prompt gets the FULL raw resume text (extraction_pipeline.py splits the
# TEXT, not the prompt, only if a given call's response comes back truncated -
# see that module) but only asks Gemini to return the one section named. A
# small, bounded output for 4 of the 5 calls (contact/summary/skills-section/
# projects are inherently short) means only "experience" is at real risk of
# truncation on a long resume, and even that is now scoped to one array
# instead of the entire standardized document.

_EXTRACTION_INTRO = """You are a meticulous resume data-extraction system, not a copywriter. Extract
ONLY what is asked below from the resume text - do not summarize, invent, or embellish. This is one
of several focused extraction passes over the same document; only answer for the field(s) requested
here, but answer for ALL of them if there are several (e.g. every job, every skill) - never a subset.
{completeness}"""


def build_contact_prompt(raw_text: str, strict_name_retry: bool = False) -> str:
    strict_name_block = ""
    if strict_name_retry:
        strict_name_block = """
IMPORTANT CORRECTION - READ BEFORE ANSWERING:
A previous extraction attempt on this exact document incorrectly returned the opening words of the
Professional Summary (e.g. "Versatile Project Manager with...", "Experienced software engineer...")
as the candidate's "name". That is wrong and you must not repeat it. The name is a short, 2-4 word
proper noun (a person's actual name), never a sentence, never a job title, never text that also
appears as (or starts) a summary/bio. Re-examine the very top of the document specifically for the
real name. If you genuinely cannot find one, return "name": "" - do not fall back to the summary's
opening words under any circumstance.
"""
    return f"""{_EXTRACTION_INTRO.format(completeness=_COMPLETENESS_RULE)}

Extract ONLY the candidate's contact details:

- "name" must contain ONLY the candidate's actual full personal name - a human being's name, never
  a section heading, job title, company name, or any other text from the document.
- The candidate's name is normally the single most prominent line at the very top of the document
  (often in the largest font, sometimes immediately followed by a job title line and then contact
  details). Use that position and prominence to identify it - do not just take the first line of
  text, since resumes sometimes have a heading, logo placeholder, or blank line before the name.
- NEVER use a section heading as the name, including but not limited to: "Professional Summary",
  "Summary", "Objective", "Career Objective", "Profile", "About Me", "Skills", "Technical Skills",
  "Core Competencies", "Experience", "Work Experience", "Professional Experience", "Education",
  "Projects", "Certifications", "Awards", "Contact", "Contact Details", "Curriculum Vitae", "Resume".
- NEVER use the Professional Summary sentence (or its opening words) as the name, even partially -
  a summary typically opens with an adjective describing the candidate (e.g. "Experienced...",
  "Professional...", "Versatile...", "Results-driven...", "Highly motivated..."). If the text you're
  about to put in "name" starts with a word like that, it is NOT the name - look elsewhere, or
  return "name": "".
- If you cannot confidently identify the candidate's real name anywhere in the document, return
  "name": "" - an empty string is always better than a wrong value.
- Extract email, phone, LinkedIn URL, GitHub URL, and personal website/portfolio URL whenever
  present ANYWHERE in the document text, not only near the top - contact details are sometimes in
  a footer, sidebar, or repeated on a later page. Extract them EXACTLY as written in the source
  (same digits/formatting for phone, same casing for email/URLs) - do not reformat, normalize, or
  invent any part of them.
- "open_to_relocate" / "open_to_remote": true/false ONLY if the candidate EXPLICITLY states a
  relocation or remote-work preference somewhere in the document (e.g. "Open to relocation",
  "Willing to relocate", "Open to remote work", "Remote only") - if neither is explicitly stated,
  leave it as null. Never infer this from job locations, company names, or anything else - only an
  explicit statement counts.
- Do NOT extract address, city, state, or country - "linkedin"/"github"/"portfolio" must contain
  ONLY their respective URL, or "" if none is present.
{strict_name_block}
Return STRICT JSON with exactly these keys, nothing else:

{{
  "name": "",
  "email": "",
  "phone": "",
  "linkedin": "",
  "github": "",
  "portfolio": "",
  "open_to_relocate": null,
  "open_to_remote": null
}}

Resume text:
{raw_text}
"""


def build_summary_prompt(raw_text: str, tone: str = "Professional") -> str:
    return f"""{_EXTRACTION_INTRO.format(completeness=_COMPLETENESS_RULE)}

Extract/write ONLY the Professional Summary:
- No bullet points, exactly 3 sentences, in a "{tone}" tone.
- Include years of experience, domain, and key strengths, based only on what's actually in the
  source document - do not invent experience or skills the candidate doesn't have.
- If the source has no summary/objective section, write one from the rest of the document's
  content (experience/skills) - do not return an empty string unless the document has virtually
  no extractable professional content at all.

Return STRICT JSON with exactly this key, nothing else:

{{
  "summary": ""
}}

Resume text:
{raw_text}
"""


def build_skills_section_prompt(raw_text: str) -> str:
    return f"""{_EXTRACTION_INTRO.format(completeness=_COMPLETENESS_RULE)}

Extract ONLY these four sections. Treat all four as equally mandatory - do not silently favor one
over another (e.g. returning "tools" but leaving "education"/"certifications" empty when the source
document actually has them is exactly the mistake this instruction forbids):

- "skills": every technical skill mentioned anywhere in the source - do not cap this to a "top N" list.
- "education": every degree/qualification mentioned, not just the most recent one. Education
  details are sometimes a small block near the end of the document, or a single line rather than
  its own clearly-labeled section - look for degree names, institution names, and graduation years
  anywhere in the text, not only under an explicit "Education" heading. Generate as empty [] ONLY
  if the source truly has no education information anywhere.
- "certifications": every certificate, badge, license, and notable achievement mentioned - do not
  cap or select a subset. These are often a short list easy to skim past - check the whole
  document, not just a section literally titled "Certifications". Empty [] ONLY if the source truly
  has none.
- "tools": every professional tool/software/platform mentioned (not soft skills) - do not cap this
  list. Empty [] if the source has none.

Do NOT create or invent: Core Competencies, Hobbies, Interests, References - if the source has such
a section, ignore it entirely (do not fold it into any of the four above). Awards/Achievements/
Recognition/Languages/Publications/Volunteer Experience/Leadership are handled by a SEPARATE
extraction call (see build_extras_prompt) - do not fold those into "skills"/"certifications" here
either; leaving them out of this call's output is correct, not a loss.

Return STRICT JSON with exactly these keys, nothing else:

{{
  "skills": [],
  "education": [],
  "certifications": [],
  "tools": []
}}

Resume text:
{raw_text}
"""


def build_experience_prompt(raw_text: str, tone: str = "Professional") -> str:
    return f"""{_EXTRACTION_INTRO.format(completeness=_COMPLETENESS_RULE)}

Extract ONLY the Professional Experience section:
- Include EVERY job/employer mentioned in the source, in the order they appear - do not omit
  older or seemingly-less-relevant jobs, and do not stop early if there are many.
- Preserve the FULL hierarchy for every job: company, role, duration, and EVERY bullet point
  stated for it, paraphrased in a "{tone}" tone (phrasing only - never drop or merge a real bullet
  point to shorten it).
- Do not flatten a job down to just its company name - if the source states a role, duration, or
  responsibilities for that job, they MUST be included.
- Carefully scan the ENTIRE document, including the space between job entries, for each of the
  following - these are commonly missed because they're brief and easy to skim past:
  - Reason for leaving (e.g. "left due to relocation", "company shut down", "role eliminated") ->
    put it in "reason_for_leaving" for that job. Leave "" if genuinely not stated - do not invent one.
  - Administrative notes tied to a job (e.g. PF/UAN number, notice period buyout, last working
    day) -> put it in "notes" for that job. Leave "" if genuinely not stated.
  - A career break/gap (sabbatical, freelance work, unemployment, further study, maternity/
    paternity leave, etc.) between two jobs -> represent it as its OWN entry in the "experience"
    array with "is_career_break": true, "company": "Career Break", "break_detail" set to whatever
    the source says about it (e.g. "UI/UX Firm", "Freelance Design"), and a short description in
    "points". Look for date gaps between consecutive jobs, not just explicit "career break" headings.
- Return an empty array ONLY if the source genuinely mentions no work experience at all. If the
  document contains ANY company name, job title, or employment date range, "experience" MUST be
  non-empty - returning [] while work history is actually present is exactly the mistake this
  instruction forbids.

Return STRICT JSON with exactly this key. "experience" must be an array with one object per job
(and one object per career break, if any) using this exact shape - include every key on every
object, using "" / [] / false for anything not applicable:

{{
  "experience": [
    {_EXPERIENCE_ITEM_SHAPE}
  ]
}}

Resume text:
{raw_text}
"""


def build_projects_prompt(raw_text: str, tone: str = "Professional") -> str:
    return f"""{_EXTRACTION_INTRO.format(completeness=_COMPLETENESS_RULE)}

Extract ONLY the Projects section:
- Include every project mentioned in the source - do not cap or select a subset.
- For each project, preserve: title, role (if stated), FULL description (do not shorten or cap to
  a sentence count) paraphrased in a "{tone}" tone, technologies used (if stated), and every
  responsibility as its own bullet point (if stated).
- Leave a field "" / [] if the source doesn't state it - do not invent one.
- If the source mentions no projects at all, return an empty array.

Return STRICT JSON with exactly this key. "projects" must be an array with one object per project
using this exact shape - include every key on every object, using "" / [] for anything not applicable:

{{
  "projects": [
    {_PROJECT_ITEM_SHAPE}
  ]
}}

Resume text:
{raw_text}
"""


def build_extras_prompt(raw_text: str) -> str:
    """6th Resume Converter extraction call - Achievements/Languages/
    Publications/Volunteer Experience/Leadership. Split out from the other
    four Stage 1 calls (rather than folded into build_skills_section_prompt)
    for the same reason Stage 1 is 5 small calls instead of 1: a small,
    bounded, single-purpose call is less likely to truncate and easier to
    retry independently (see extraction_pipeline.py). This is also the
    direct fix for a real, confirmed defect: Stage 1 used to be explicitly
    told to IGNORE an Awards/Achievements section if the source had one,
    even when it was clearly present - the content was simply thrown away
    before anything downstream (including resume_optimizer.py's own
    achievement-derivation step) ever saw it."""
    return f"""{_EXTRACTION_INTRO.format(completeness=_COMPLETENESS_RULE)}

Extract ONLY these five sections, if the source document actually contains them - an empty list is
the correct and expected answer for any of these that genuinely aren't present, never a failure:

- "achievements": Awards, recognitions, "Star Performer"/"Employee of the Month"/similar
  distinctions, innovation awards, hackathon wins, patents, competition finalist placements,
  scholarships - one entry per achievement, in the candidate's own words (lightly cleaned up, never
  invented). Scan the WHOLE document for these, not only a section literally titled "Awards" or
  "Achievements" - they're sometimes a single line under Experience/Projects, or a short block near
  the end of the document with no heading at all.
- "languages": Spoken/written languages the candidate lists (e.g. "English (Fluent)", "Spanish -
  Native") - not programming languages, those belong in Technical Skills.
- "publications": Papers, articles, books, or other published works the candidate lists, with
  whatever detail (title, venue, year) the source actually states.
- "volunteer_experience": Volunteer work, community service, or pro-bono engagements - similar
  shape to a short Experience entry (organization/role/what was done), but keep it as a single
  descriptive string per entry rather than the full Experience object shape, since these are
  usually stated far more briefly in the source.
- "leadership": Leadership roles or responsibilities called out separately from a job title (e.g.
  "Mentored 5 junior engineers", "Led the local chapter of X organization", a club/community
  leadership position) that aren't already fully captured as an Experience bullet point.

Do NOT invent any entry for any of these five - if the source doesn't mention one, its list stays
empty. Do NOT fold Technical Skills, Tools, Certifications, or Education content into any of these
five lists either - each belongs in its own separate extraction call.

Return STRICT JSON with exactly these keys, nothing else:

{{
  "achievements": [],
  "languages": [],
  "publications": [],
  "volunteer_experience": [],
  "leadership": []
}}

Resume text:
{raw_text}
"""


# --- Resume Intelligence Engine (app/services/resume_optimizer.py) --------
#
# Everything above this point (build_contact_prompt through
# build_projects_prompt) is Stage 1/Extraction: extract EVERYTHING, never
# summarize, never drop an entry - that's what makes the draft handed to
# these prompts trustworthy raw material. These do the opposite job on
# purpose: they consolidate, categorize, and rewrite Stage 1's output into a
# recruiter-ready resume, called by resume_optimizer.py after Stage 1
# (extraction_pipeline.extract_resume) + normalization.py have already
# produced a complete, nothing-discarded JSON. Stage 1's own prompts/output
# are never modified by these - the optimizer only ever runs afterward, on
# Stage 1's result.
#
# Domain-agnostic by design: none of these prompts hardcode categories or
# examples specific to any one profession (software/cloud, finance,
# healthcare, marketing, ...) - each one instructs Gemini to derive
# categories/wording that fit whatever domain the actual input represents,
# so the same prompt works for a Cloud Engineer, a Project Manager, or a
# Content Writer without a special case for any of them.
#
# Split into small, focused calls - one for skills/tools, one PER JOB for
# experience-bullet consolidation, one for projects, one for achievements,
# one for the summary - rather than one call trying to do everything at
# once. A single resume with, say, 200 skills and 130 total experience
# bullets across 5 jobs produces a draft JSON tens of thousands of
# characters long; asking one Gemini call to both read all of that AND
# write back a comparably large categorized/consolidated result reliably
# exceeds GEMINI_MAX_OUTPUT_TOKENS (8192) and truncates - proven directly
# against a real resume of that size. Splitting by concern keeps each call's
# input and output small and bounded, regardless of how large the source
# resume is - the same reason Stage 1 itself is five small calls instead of
# one.

_OPTIMIZE_SKILLS_TOOLS_INTRO = """You are a senior technical recruiter and resume writer with 20+
years of experience shortlisting candidates for Fortune 500 companies, curating a candidate's raw,
unsorted list of skills and tools into the "Technical Skills" and "Tools" sections of a
recruiter-quality resume. The candidate could be in ANY profession - software, cloud, data
science, project management, finance, healthcare, marketing, sales, mechanical/civil engineering,
content writing, HR, or any other field. Derive categories that fit what THIS candidate's items
actually are; do not assume a technology/software domain if the items are actually, say, financial
instruments, clinical skills, or marketing platforms.

Think like a recruiter scanning this section for 10 seconds: every entry should read as a
specific, nameable competency, technology, tool, platform, or method wherever possible. Do NOT
invent any skill or tool that isn't in the lists below. Categorizing, reclassifying between
skills/tools, and removing exact/near-duplicate entries is expected; adding new items, or dropping
a genuine input item for length/relevance reasons, is not - every genuine item the candidate
listed must survive somewhere in the output (see rule 1's "Other Skills" catch-all for anything
that doesn't cleanly fit a named category)."""


def build_skills_tools_optimize_prompt(skills: list[str], tools: list[str]) -> str:
    return f"""{_OPTIMIZE_SKILLS_TOOLS_INTRO}

Raw skills (unsorted, may include duplicates, may include non-skill items misclassified as
skills, e.g. responsibilities, soft skills, or action verbs - filter those out entirely, don't
force them into a category):
{json.dumps(skills, indent=2)}

Raw tools (unsorted, may include items that are actually skills/technologies, not tools - move
those to the skills output instead):
{json.dumps(tools, indent=2)}

Return STRICT JSON with exactly these two keys:

{{
  "skills": {{
    "<Category Name>": ["", "..."]
  }},
  "tools": []
}}

RULES:

1. Skills - group into clear, genuinely-fitting categories. Depending on the candidate's actual
   field these might look like "Programming Languages", "Frameworks", "Cloud Platforms",
   "Databases", "DevOps Technologies", "Architecture Patterns", "AI/ML Technologies", "Analytics
   Technologies" (technical fields), "Financial Modeling", "Clinical Procedures", "Content &
   Media", "Supply Chain" (non-technical fields), or "Project Management", "Business Skills",
   "Soft Skills" (process/interpersonal competencies like "Agile", "Stakeholder Management",
   "Leadership", "Communication") - these are illustrative only, not a fixed list to force items
   into. Choose whatever category names actually fit the items given.
   - A small number of items are NOT skills at all and belong in a different resume section, not
     here - a certification/license name (e.g. "AWS Certified Solutions Architect" - belongs in
     "certifications", never in skills, even though it names AWS), a degree/qualification (e.g.
     "Bachelor of Science in Computer Science" - belongs in "education"), a project name (e.g.
     "Customer Portal Redesign" - belongs in "projects"), or a full sentence describing what the
     candidate did (a responsibility/achievement, not a named competency). Move or discard ONLY
     these - never a genuine competency just because it's a process/business/soft skill rather
     than a technology.
   - Every other genuine item from the input - including process, business, and soft skills like
     "Leadership", "Stakeholder Management", "Communication", "Business Analysis", "Consulting",
     "Governance" - is a real skill and MUST appear in the output. If it doesn't cleanly fit one of
     the specific categories above, put it in an "Other Skills" category rather than discarding it
     - never drop a genuine input item for any reason.
   - Remove exact and near-duplicate entries (e.g. "CI/CD" and "CI/CD Pipelines" -> keep one;
     "Financial Modeling" and "Financial Modelling" -> keep one) - this is the only kind of
     "removal" allowed; it never reduces the candidate's actual skill set, only its redundancy.
   - Keep each category to roughly 5-12 items for readability. If a category would genuinely need
     more than that, split it into two or more MORE SPECIFIC categories instead (e.g. split an
     oversized "Cloud" category into "Cloud Platforms" and "Cloud Networking") - never drop a real
     skill to satisfy this guideline; splitting into finer categories, or adding to "Other Skills",
     is always preferred over dropping. There is no target or maximum for the total number of
     skills across all categories combined - a candidate with 60 genuine skills should see all 60,
     grouped and organized, not trimmed to a "curated" subset.

2. Tools - ONLY actual software, IDEs, platforms, and named products a person opens, installs, or
   runs. Good examples (illustrative, not exhaustive, and not all relevant to every candidate):
   "Git", "GitHub", "GitLab", "JIRA", "Confluence", "Azure DevOps", "Jenkins", "Terraform",
   "Docker Desktop", "Kubernetes Dashboard", "Power BI", "Tableau", "Postman", "VS Code",
   "IntelliJ", "Visual Studio", "Oracle SQL Developer", "SSMS", "SAP", "Salesforce", "Snowflake",
   "Databricks". General technologies, concepts, protocols, or process/business/soft skills belong
   in "skills" instead (move them there, per rule 1 above - every genuine item must still end up
   somewhere, never discarded). Bad examples for THIS list specifically (move to skills, don't
   discard): "ExpressRoute", "DNS", "Virtual Network", "REST API", "Azure", "AWS", "Cloud",
   "CI/CD", "Networking", "Leadership", "Business Intelligence", "Consulting", "Project
   Management", "Planning", "Communication".
   - There is no target or maximum for the total number of tools - list every genuine one; keeping
     same-vendor tools adjacent to each other (e.g. all Azure tools together) is helpful, but
     dropping any of them is not.
"""


_OPTIMIZE_EXPERIENCE_INTRO = """You are a senior technical recruiter and professional resume
writer with 20+ years of experience recruiting for Fortune 500 companies, rewriting one job's raw,
unfiltered list of responsibility bullet points into a small set of strong, high-impact,
information-dense, ATS-friendly bullets. Your goal is NOT to preserve every sentence - it is to
transform the raw bullets into bullets that would make a recruiter shortlist this candidate. This
applies to any profession - the same consolidation approach works whether the raw bullets describe
cloud infrastructure work, financial audits, clinical care, marketing campaigns, or anything else.

Do NOT invent any achievement, detail, or responsibility that isn't supported by the bullets
below. Merging, consolidating wording, and increasing information density is expected; adding new
content that isn't already implied by the raw bullets is not."""

# Action verbs to open bullets with - illustrative across domains, not an exhaustive or
# domain-specific list; Gemini should choose whichever genuinely fits each bullet's content.
_ACTION_VERB_EXAMPLES = (
    "Architected, Designed, Developed, Built, Led, Directed, Implemented, Optimized, Migrated, "
    "Modernized, Delivered, Established, Engineered, Integrated, Automated, Configured, Improved, "
    "Managed, Coordinated, Analyzed, Negotiated, Streamlined, Facilitated, Launched, Reduced, "
    "Increased, Presented, Audited, Diagnosed"
)

# Weak, low-information verbs that must never open a bullet - each one signals the writer didn't
# actually say what the candidate did.
_WEAK_VERB_EXAMPLES = "Worked (on), Responsible for, Handled, Helped, Participated (in), Was involved in"


def build_experience_optimize_prompt(
    company: str, role: str, points: list[str], min_bullets: int, max_bullets: int, tone: str = "Professional"
) -> str:
    raw_points = "\n".join(f"- {p}" for p in points)
    return f"""{_OPTIMIZE_EXPERIENCE_INTRO}

Company: {company}
Role: {role}

Raw bullet points for this job ({len(points)} total, likely containing repetitive/duplicate
entries describing the same underlying work):
{raw_points}

Consolidate these into approximately {min_bullets} to {max_bullets} strong, non-repetitive,
information-dense bullet points in a "{tone}" tone - every bullet should communicate as much of
technology, business impact, scale, leadership, architecture, automation, migration,
optimization, cost savings, performance improvement, and metrics as the raw bullets actually
support, using as few words as possible. Never optimize by simply making bullets shorter - optimize
by packing more real signal into each one.

For example:
- BAD (repetitive, low information density): "Created DNS Zones.", "Configured DNS Zones.",
  "Created Private DNS Zones.", "Configured Private DNS Zones."
  GOOD (merged, dense): "Designed, configured, and managed Azure DNS and Private DNS
  infrastructure supporting enterprise cloud networking."
- BAD: "Created Storage Accounts.", "Managed Storage Accounts.", "Provisioned Storage Accounts."
  GOOD: "Provisioned and managed Azure Storage solutions supporting enterprise cloud workloads."
- The same merging approach applies regardless of domain - e.g. several bullets about drafting,
  reviewing, and finalizing contracts could become one bullet like "Drafted, reviewed, and
  finalized vendor contracts across multiple business units."

Rules:
- Every bullet must begin with a strong action verb - for example (not exhaustive, pick whichever
  genuinely fits): {_ACTION_VERB_EXAMPLES}.
- NEVER begin a bullet with a weak, low-information verb/phrase: {_WEAK_VERB_EXAMPLES}. If a raw
  bullet only supports a weak phrasing, rewrite it around a strong verb using the same
  underlying fact, never invent a stronger claim than the raw bullet supports.
- Preserve every distinct technology, tool, and platform named in the raw bullets - merge the
  WORDING of repetitive/related items, never drop a named technology.
- Preserve every achievement and metric stated in the raw bullets (numbers, percentages, dollar
  amounts, team sizes, time frames, awards) - carry these into whichever consolidated bullet they
  belong to, word-for-word where possible; never drop or soften a stated number.
- Preserve every leadership responsibility, architecture/design decision, and scale detail (team
  size, system scale, data volume, user base) stated in the raw bullets - these must survive as
  their own bullet or be clearly present in a merged one, never quietly absorbed into a generic
  statement.
- Never generate two bullets that say substantially the same thing - each bullet must add
  genuinely new information.
- Avoid generic, contentless wording ("responsible for various tasks", "worked on projects") -
  every bullet must state a specific, concrete action tied to real content from the raw bullets.
- Fewer than {min_bullets} bullets is fine only if the raw list genuinely doesn't have enough
  distinct content - never pad with filler to reach a target count.
- Do not invent metrics, team sizes, or outcomes that aren't stated or clearly implied by the raw
  bullets - use only information present in the raw bullets given.

Return STRICT JSON with exactly this key:

{{
  "points": ["", "..."]
}}
"""


_OPTIMIZE_PROJECTS_INTRO = """You are a senior technical recruiter and resume writer consolidating
a candidate's raw, unfiltered project descriptions into concise, high-impact project entries for a
professional resume - for any profession, not just software.

Do NOT invent any project, technology, or outcome that isn't supported by the input below.
Merging, consolidating wording, and removing duplicate/repetitive points is expected; adding new
content that isn't already implied by the input is not."""


def build_projects_optimize_prompt(projects: list[dict], tone: str = "Professional") -> str:
    draft = json.dumps(projects, indent=2)
    return f"""{_OPTIMIZE_PROJECTS_INTRO}

Raw projects (may contain repetitive/duplicate responsibility points within a project, and may
contain two entries that are really the same project stated twice - merge those into one):
{draft}

For each project, produce a "description" (the project's business objective/purpose, 1-2
sentences), a "technologies" line, and 3 to 5 consolidated, high-impact bullet points in a "{tone}"
tone covering what was built and its impact/outcome (if the input states or clearly implies one -
do not invent one if it doesn't).

Give each project its OWN distinct narrative - do not describe every project with the same
sentence pattern or the same opening structure. If one project was about a migration, another
about automation, another about a customer-facing feature, write each in a way that reflects what
made THAT project distinct (e.g. one bullet set emphasizing migration/modernization, another
emphasizing automation/performance, another emphasizing integration/analytics) - only to the
extent the raw input actually supports that framing; never invent a theme the input doesn't
support.

Return STRICT JSON with exactly this key. "projects" must be an array with one object per
(deduplicated) project using this exact shape:

{{
  "projects": [
    {{
      "title": "",
      "role": "",
      "description": "",
      "technologies": "",
      "responsibilities": ["", "..."]
    }}
  ]
}}
"""


_ACHIEVEMENTS_INTRO = """You are a senior resume writer identifying notable achievements ALREADY
STATED in a candidate's resume content - you are not writing new content, only extracting and
lightly rephrasing what's already there for a dedicated "Achievements" section.

Do NOT invent any achievement, award, metric, or recognition that isn't clearly stated or directly
implied by the content below. If nothing qualifies, return an empty list - an empty list is the
correct and expected answer for many resumes, not a failure."""


def build_achievements_prompt(summary: str, experience_points: list[str], project_descriptions: list[str]) -> str:
    points_block = "\n".join(f"- {p}" for p in experience_points) or "(none)"
    projects_block = "\n".join(f"- {p}" for p in project_descriptions) or "(none)"
    return f"""{_ACHIEVEMENTS_INTRO}

Summary:
{summary or "(none)"}

Experience bullet points:
{points_block}

Project descriptions/points:
{projects_block}

Identify any already-stated, quantifiable or clearly notable achievements - e.g. an award, a
recognized certification of merit, a measurable business/technical impact (cost savings,
performance improvement, revenue growth, team size led, awards won, promotions), or a similar
concrete accomplishment. Do not restate routine responsibilities as if they were achievements -
only include something that stands out as a genuine accomplishment already described above.

Return STRICT JSON with exactly this key - an empty array if nothing qualifies:

{{
  "achievements": ["", "..."]
}}
"""


_OPTIMIZE_SUMMARY_INTRO = """You are a senior technical recruiter with 20+ years of experience
writing resume summaries that get candidates shortlisted at Fortune 500 companies. Write a short,
recruiter-quality resume summary based only on the candidate's work history and skills below - for
any profession, not just technical roles. Do not invent years of experience, a role, technologies,
an industry, architecture/cloud expertise, business impact, or leadership experience not evidenced
by this information, and do not use empty, AI-sounding buzzwords or filler phrases ("dynamic
self-starter", "results-driven synergy", "passionate about", "detail-oriented team player",
"proven track record of excellence") - every sentence must be grounded in something concrete
actually shown below, never a generic phrase that could describe anyone."""


def build_summary_optimize_prompt(
    job_lines: list[str],
    categorized_skills: dict[str, list[str]],
    original_summary: str = "",
    tone: str = "Professional",
) -> str:
    jobs = "\n".join(f"- {line}" for line in job_lines) or "(no work history available)"
    if categorized_skills:
        skills_block = "\n".join(
            f"- {category}: {', '.join(items)}" for category, items in categorized_skills.items()
        )
    else:
        skills_block = "(none)"
    original_block = original_summary.strip() or "(none extracted)"
    return f"""{_OPTIMIZE_SUMMARY_INTRO}

Work history (company/role/duration):
{jobs}

Skills, by category:
{skills_block}

The candidate's ORIGINAL summary, extracted as-is from their source document (for context only -
see the rule against copying it below):
{original_block}

Write 3 to 5 ATS-optimized sentences in a "{tone}" tone covering:
- Years of experience (estimate from the work history durations above).
- Primary role/title - the position that best represents the candidate overall, based on the
  work history above (most recent and/or most senior role).
- Major technologies - name specific ones from the skills above, not just category labels (e.g.
  say "Python and AWS", not just "programming languages and cloud platforms").
- Industry/domain the candidate has worked in, ONLY if it's clearly evidenced by the company
  names, project context, or the original summary above - omit this entirely rather than
  guessing one that isn't evidenced.
- Architecture/system-design expertise, ONLY if the work history or skills clearly evidence it
  (e.g. an architect-level title, or system design/architecture-pattern skills listed above) -
  omit entirely if there's no evidence.
- Cloud platform expertise, ONLY if a specific cloud platform (AWS/Azure/GCP/etc.) appears in the
  skills above - name it specifically, do not say "cloud technologies" if a specific platform is
  known.
- Leadership experience, ONLY if the work history suggests any (e.g. "Lead", "Manager",
  "Director", "Head of" in a role title, or management responsibilities mentioned in the original
  summary) - omit this entirely if there's no evidence of it.
- Business impact, ONLY if a concrete outcome (cost savings, performance improvement, scale,
  revenue, team size) is evidenced by the work history/original summary - omit entirely rather
  than inventing a generic impact claim.

Rules:
- Do NOT copy any sentence, or near-copy of a sentence, from the candidate's original summary
  above - write entirely new sentences synthesized from the work history and skills, even if the
  original summary already reads well.
- Do not invent anything (a technology, an employer, a duration, an industry, a leadership title)
  that isn't evidenced by the work history or skills given.
- Do not use generic buzzwords with no concrete grounding in the actual data above.

Return STRICT JSON with exactly this key:

{{
  "summary": ""
}}
"""
