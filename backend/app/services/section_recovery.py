"""Section Recovery Engine - the detect -> retry -> infer -> deterministic
extraction loop.

Root cause this addresses: before this module, the only code that even
NOTICED a section might have silently vanished was
`extraction_pipeline._warn_if_section_likely_dropped` - and that function is
explicitly, deliberately log-only (see its own docstring), covers just 3 of
the ~11 sections a resume can have, and never takes any corrective action.
A document whose raw text clearly mentions Certifications, but whose
Stage 1 JSON came back with an empty `certifications` list (a truncated
Gemini response, a document layout the model misread, ...), previously just
rendered without that section - "a successfully generated but silently
incomplete resume", the single worst failure mode this whole redesign is
about eliminating.

This module runs AFTER Stage 1 extraction/normalization
(extraction_pipeline.extract_resume -> normalization.normalize_parsed/
enforce_limits) and BEFORE the Resume Intelligence Engine
(resume_optimizer.optimize_resume) - see resume_service.py's call site. Four
tiers, run in order, STOPPING at the first one that produces content for a
given section - never overwrites a field that already has real content,
only ever fills one `detect_missing_sections` flagged as empty-but-likely-
present:

    1. Detect   - keyword-presence signal: does the raw source text mention
                  this section at all, even though the structured field
                  came back empty? (see detect_missing_sections)
    2. Retry    - re-run that section's own Stage 1 Gemini prompt once more
                  (see _retry_missing_sections) - the same prompts already
                  proven in extraction_pipeline.py, reused as-is, not
                  reimplemented.
    3. Infer    - deterministic, no-Gemini: scan the OTHER already-populated
                  free text (summary/experience bullets/project text) for a
                  recognizable signal (a known technology keyword, a
                  certification-shaped phrase) - see infer_section.
    4. Deterministic extraction - same keyword/regex scanning as tier 3, but
                  directly over the raw source text, for the case where
                  Stage 1 missed a section so completely that nothing about
                  it reached any other field either - see deterministic_extract.

Every tier degrades independently and never raises - a failure at any tier
just means "try the next one", exactly like every other Gemini call in this
pipeline (resume_optimizer.py, experience_refiner.py, summary_generator.py
all follow the same degrade-never-raise rule).
"""
import logging
import re

from app.services.gemini_client import GeminiError, call_gemini
from app.services.prompts import (
    build_experience_prompt,
    build_extras_prompt,
    build_projects_prompt,
    build_skills_section_prompt,
)
from app.services.skill_categorizer import CATEGORY_KEYWORDS

logger = logging.getLogger(__name__)

# Which underlying Stage 1 prompt/call produces each section - used to group
# retries so a document missing BOTH "skills" and "certifications" (the same
# underlying build_skills_section_prompt call) triggers exactly one retry
# call, not two redundant ones for the same prompt.
_SKILLS_SECTION_FIELDS = ("skills", "education", "certifications", "tools")
_EXTRAS_FIELDS = ("achievements", "languages", "publications", "volunteer_experience", "leadership")

# Keyword-presence signal per section - the same diagnostic idea as
# extraction_pipeline._SECTION_KEYWORDS, generalized to every section this
# engine can recover (that one stays as its own narrower, log-only,
# pre-recovery diagnostic - see that module's docstring). False positives
# are expected and acceptable here too: a false-positive flag just costs one
# extra retry call that legitimately comes back empty again, not a
# correctness bug - never used to change output on its own, only to decide
# whether recovery is worth attempting at all.
SECTION_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "skills": ("skill", "proficient", "technolog", "programming"),
    "tools": ("tool", "software", "platform"),
    "certifications": ("certif", "license", "credential"),
    "education": ("education", "university", "college", "degree", "b.tech", "m.tech", "bachelor", "master", "diploma"),
    "experience": ("experience", "employment"),
    "projects": ("project",),
    "achievements": (
        "award", "achievement", "recognition", "star performer", "employee of the month",
        "innovation award", "hackathon", "patent", "finalist", "scholarship",
    ),
    "languages": ("languages known", "language proficiency", "spoken language", "native language", "fluent in"),
    "publications": ("publication", "published", "journal", "conference paper", "research paper"),
    "volunteer_experience": ("volunteer", "community service", "pro bono", "pro-bono", "ngo"),
    "leadership": ("leadership", "mentored", "mentoring", "led a team", "led the"),
}

_RECOVERABLE_SECTIONS = tuple(SECTION_SIGNAL_KEYWORDS.keys())


def detect_missing_sections(raw_text: str, structured: dict) -> list[str]:
    """Returns every section name (from SECTION_SIGNAL_KEYWORDS) that is
    EMPTY in `structured` but whose signal is present somewhere in
    `raw_text` - candidates for recovery. Never flags a section that
    already has content, regardless of what the raw text says.

    "skills"/"tools" get a SECOND, stronger signal beyond the generic
    heading-keyword list: the presence of ANY recognized technical term
    (see _TECHNICAL_KEYWORD_CORPUS) anywhere in the raw text - this is
    exactly the "no explicit Skills heading, but the resume clearly
    mentions Python/Kubernetes/etc. throughout" case the heading-keyword
    check alone would miss entirely (a resume that lists technologies only
    inline in Experience/Project bullets, never under a literal "Skills"
    word, is common and must still trigger recovery)."""
    lowered_text = raw_text.lower()
    missing = []
    for section, keywords in SECTION_SIGNAL_KEYWORDS.items():
        if structured.get(section):
            continue
        signal = any(kw in lowered_text for kw in keywords)
        if not signal and section in ("skills", "tools"):
            signal = bool(_word_boundary_matches(raw_text, _TECHNICAL_KEYWORD_CORPUS))
        if signal:
            missing.append(section)
    return missing


def _retry_missing_sections(missing: set[str], raw_text: str, tone: str) -> dict[str, list]:
    """Tier 2 - one retry call PER UNDERLYING PROMPT (never per section), so
    a document missing several fields that share one Stage 1 call (e.g.
    skills + certifications, both from build_skills_section_prompt) costs
    one Gemini call, not several. Returns only the fields that came back
    non-empty; a field not in the returned dict simply didn't recover at
    this tier and falls through to inference/deterministic extraction."""
    recovered: dict[str, list] = {}

    if missing & set(_SKILLS_SECTION_FIELDS):
        try:
            result = call_gemini(build_skills_section_prompt(raw_text))
            for field in _SKILLS_SECTION_FIELDS:
                if field in missing and result.get(field):
                    recovered[field] = result[field]
        except (GeminiError, ValueError, TypeError) as exc:
            logger.warning("Section recovery retry (skills section) failed: %s", exc)

    if "experience" in missing:
        try:
            result = call_gemini(build_experience_prompt(raw_text, tone=tone))
            if result.get("experience"):
                recovered["experience"] = result["experience"]
        except (GeminiError, ValueError, TypeError) as exc:
            logger.warning("Section recovery retry (experience) failed: %s", exc)

    if "projects" in missing:
        try:
            result = call_gemini(build_projects_prompt(raw_text, tone=tone))
            if result.get("projects"):
                recovered["projects"] = result["projects"]
        except (GeminiError, ValueError, TypeError) as exc:
            logger.warning("Section recovery retry (projects) failed: %s", exc)

    if missing & set(_EXTRAS_FIELDS):
        try:
            result = call_gemini(build_extras_prompt(raw_text))
            for field in _EXTRAS_FIELDS:
                if field in missing and result.get(field):
                    recovered[field] = result[field]
        except (GeminiError, ValueError, TypeError) as exc:
            logger.warning("Section recovery retry (extras) failed: %s", exc)

    return recovered


def _word_boundary_matches(text: str, keywords: tuple[str, ...]) -> list[str]:
    """Case-insensitive, word-boundary-checked keyword scan over `text` -
    returns each match in the ORIGINAL casing/spelling it was found in
    (deduped, first occurrence kept), never the lowercase keyword itself,
    so recovered content still reads like the candidate's own resume."""
    found: list[str] = []
    seen_lower: set[str] = set()
    for keyword in keywords:
        pattern = re.compile(r"(?<![a-zA-Z0-9])" + re.escape(keyword) + r"(?![a-zA-Z0-9])", re.IGNORECASE)
        match = pattern.search(text)
        if match and match.group(0).lower() not in seen_lower:
            seen_lower.add(match.group(0).lower())
            found.append(match.group(0))
    return found


# Flattened, deduped keyword corpus for skill/tool inference - reuses
# skill_categorizer.py's existing, already-curated technical-term
# dictionaries rather than maintaining a second copy (see that module for
# the full category breakdown); this engine doesn't need the category
# labels, only "is this substring a recognizable technical term at all".
_TECHNICAL_KEYWORD_CORPUS: tuple[str, ...] = tuple(
    dict.fromkeys(keyword for keywords in CATEGORY_KEYWORDS.values() for keyword in keywords)
)


def infer_section(section: str, structured: dict) -> list[str]:
    """Tier 3 - deterministic (no Gemini), scans the candidate's OTHER
    already-populated free text (summary, experience bullets, project
    descriptions/responsibilities) for a recognizable signal. Only
    implemented for sections where "mentioned elsewhere in the resume's own
    content" is a meaningful, low-risk signal - skills/tools (a technology
    named in a bullet point but never listed in a Skills section) and
    certifications (a credential named inline, e.g. "AWS Certified..." in a
    bullet, but with no dedicated Certifications section). Every other
    section returns [] here - there is no reliable "infer from other
    fields" signal for experience/education/projects/achievements/
    languages/publications/volunteer/leadership without risking a fabricated
    entry, so those fall through to deterministic_extract or stay missing."""
    other_text_parts = [str(structured.get("summary") or "")]
    for exp in structured.get("experience") or []:
        other_text_parts.extend(str(p) for p in (exp.get("points") or []))
    for proj in structured.get("projects") or []:
        other_text_parts.append(str(proj.get("description") or ""))
        other_text_parts.extend(str(r) for r in (proj.get("responsibilities") or []))
    other_text = " ".join(other_text_parts)
    if not other_text.strip():
        return []

    if section in ("skills", "tools"):
        return _word_boundary_matches(other_text, _TECHNICAL_KEYWORD_CORPUS)
    if section == "certifications":
        return _certification_like_phrases(other_text)
    return []


_CERTIFICATION_LINE_RE = re.compile(
    r"[^.\n]*\b(certified|certification|certificate)\b[^.\n]*", re.IGNORECASE
)


def _certification_like_phrases(text: str) -> list[str]:
    """Extracts the clause/sentence around a "certified"/"certification"/
    "certificate" mention - the same shape a candidate would normally list
    under a Certifications heading, just pulled out of running prose
    instead. Deduped, trimmed; never invents a credential name that isn't
    literally present in the source text."""
    seen: set[str] = set()
    results: list[str] = []
    for match in _CERTIFICATION_LINE_RE.finditer(text):
        phrase = match.group(0).strip(" ,;-")
        key = phrase.lower()
        if phrase and key not in seen:
            seen.add(key)
            results.append(phrase)
    return results


_EDUCATION_LINE_RE = re.compile(
    r"[^\n]*\b(bachelor|master|b\.?tech|m\.?tech|b\.?sc|m\.?sc|mba|ph\.?d|diploma)\b[^\n]*",
    re.IGNORECASE,
)


def deterministic_extract(section: str, raw_text: str) -> list[str]:
    """Tier 4 - last resort, no Gemini: the same kind of keyword/regex
    scanning as infer_section, but directly over the ORIGINAL raw source
    text rather than other already-extracted fields - for when Stage 1
    missed a section so completely that nothing about it reached any other
    field either. Only implemented for skills/tools/certifications/
    education, the sections with a reasonably reliable keyword/regex
    signal; every other section returns [] (see infer_section's docstring
    for why fabricating an experience/project/achievement entry from a
    keyword match would be too risky to attempt)."""
    if section in ("skills", "tools"):
        return _word_boundary_matches(raw_text, _TECHNICAL_KEYWORD_CORPUS)
    if section == "certifications":
        return _certification_like_phrases(raw_text)
    if section == "education":
        seen: set[str] = set()
        results: list[str] = []
        for match in _EDUCATION_LINE_RE.finditer(raw_text):
            line = match.group(0).strip(" ,;-")
            key = line.lower()
            if line and key not in seen:
                seen.add(key)
                results.append(line)
        return results
    return []


def recover_missing_sections(raw_text: str, structured: dict, tone: str = "Professional") -> tuple[dict, dict]:
    """Runs the full detect -> retry -> infer -> deterministic loop over
    `structured` IN PLACE, and returns (structured, recovery_log).
    `recovery_log` maps every section detect_missing_sections flagged to
    which tier recovered it ("retry" / "inference" / "deterministic") or
    "unrecovered" if all three tiers came back empty - never a silent gap;
    a caller (see resume_quality_engine.py's Phase 6 wiring) can surface
    this log directly instead of only finding out via application logs."""
    missing = detect_missing_sections(raw_text, structured)
    if not missing:
        return structured, {}

    recovery_log: dict[str, str] = {}
    missing_set = set(missing)
    retried = _retry_missing_sections(missing_set, raw_text, tone)

    for section in missing:
        if retried.get(section):
            structured[section] = retried[section]
            recovery_log[section] = "retry"
            continue

        inferred = infer_section(section, structured)
        if inferred:
            structured[section] = inferred
            recovery_log[section] = "inference"
            continue

        deterministic = deterministic_extract(section, raw_text)
        if deterministic:
            structured[section] = deterministic
            recovery_log[section] = "deterministic"
            continue

        recovery_log[section] = "unrecovered"
        logger.warning(
            "Section Recovery Engine: %r appeared present in the source text but could not be "
            "recovered by retry, inference, or deterministic extraction - it remains empty.",
            section,
        )

    return structured, recovery_log
