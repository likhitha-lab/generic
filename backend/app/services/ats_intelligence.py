"""ATS Intelligence Engine - Phase 6 of the Resume Intelligence Engine.

Runs AFTER Phase 5's Resume Quality & Validation Engine (resume_quality_
engine.py) and BEFORE the Resume Builder (file_generator.py) - see
optimize_for_ats(), this module's one public entry point, wired into
resume_service.py right after the existing quality-engine gate.

STRATEGY: an ATS parser (and the recruiter who reads a resume after it
clears one) weighs three things above almost everything else - (1)
STANDARDIZED keyword spelling, since a keyword-match model treats "Node"
and "Node.js" as different tokens unless normalized to one canonical form;
(2) CONSISTENCY, since a skill that's only listed once in the Skills
section scores weaker than the same skill also demonstrated in Experience/
Project bullet text; (3) NATURAL DENSITY, since repeating a term
unnaturally often reads as keyword-stuffing to both an ATS's own anti-spam
heuristics and a human recruiter. This module optimizes exactly those
three levers, using ONLY evidence already present in the resume - no
external job-market/job-description data, and no invented technology,
metric, or claim; anything this module can't safely fix mechanically (a
missing but commonly-paired technology) is surfaced as an explicit,
clearly-labeled suggestion for the candidate to confirm, never silently
added.

PIPELINE (see optimize_for_ats(), which runs these in order):

  1. Standardized technology naming - standardize_bullet_technology_names()
     applies skill_intelligence.py's own canonical-name lookup (already
     used for the Skills list) to Experience/Project bullet TEXT too, so a
     bullet reading "Azure Kubernetes Service" and a Skills entry reading
     "AKS" don't visibly disagree - a pure spelling/naming fix, never a
     fact change.
  2. Duplicate keyword detection (+ fix) - re-runs skill_intelligence.py's
     own dedup as an idempotent final safety net (same defense-in-depth
     reasoning Phase 5's resume_quality_engine.py already applies).
  3. ATS keyword ranking - rank_keywords_for_ats() scores every listed
     skill by how strongly THIS resume's own text evidences it (frequency,
     job-title mentions, recency), never by external keyword-popularity
     data this system doesn't have.
  4. Technology prioritization - get_priority_technologies() surfaces the
     top-ranked keywords as REPORT-ONLY metadata. Deliberately does NOT
     reorder the rendered Skills list itself - Phase 2 (skill_intelligence.
     py) already owns that ordering for recruiter readability (category
     priority, e.g. languages before niche tooling), and re-ordering it
     again here by raw frequency would regress that phase's own design
     goal rather than complement it.
  5. Keyword density validation - validate_keyword_density() computes what
     fraction of listed skills are demonstrated anywhere in Experience/
     Project text, and separately flags any keyword repeated unnaturally
     often (the concrete, measurable form of "do not keyword stuff").
  6. Missing keyword detection - detect_missing_keywords() checks a small,
     hand-curated table of commonly-PAIRED technologies (Docker/
     Kubernetes, React/TypeScript, ...) and suggests - never adds - one the
     candidate might genuinely also have. Explicitly and repeatedly never
     mutates `parsed`; a fabricated skill is a worse outcome than a
     missed suggestion.
  7. Section-level ATS optimization - check_section_level_ats_optimization()
     confirms the ATS-critical sections (Skills, Experience, Education)
     are present and that keyword mentions aren't concentrated in Skills
     alone with zero reinforcement elsewhere.
"""
import logging
import re

from app.services.skill_intelligence import build_technical_skills, known_technology_variants

logger = logging.getLogger(__name__)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _count_mentions(keyword: str, corpus_lower: str) -> int:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
    return len(re.findall(pattern, corpus_lower))


# --- STEP 1: Standardized technology naming (bullet text) --------------------

def _standardize_text(text: str, variants: dict[str, str]) -> str:
    """Root-cause fix for a text-corruption bug (e.g. "Node.js" ->
    "Node.js.js"): the previous implementation ran one INDEPENDENT re.sub
    per variant, longest-first. That meant a longer variant ("node.js")
    could substitute first (a no-op here, since "node.js" -> "Node.js" is
    already its own canonical form), and then a SHORTER variant ("node")
    got its own separate pass over the ALREADY-substituted text and
    matched the "Node" prefix again - the word-boundary regex treats "."
    as a non-word character, so "Node" followed by "." satisfies the same
    negative lookahead a second time, duplicating the suffix.

    Fixed by using ONE combined alternation pattern (longest variant
    first, so the alternation still prefers the longer match at any given
    position - Python's `|` is first-match, not longest-match, so ordering
    matters) in a SINGLE re.sub pass: each span of `text` is matched and
    substituted AT MOST ONCE, so a shorter variant can never re-match a
    span a longer variant already consumed. This is a general fix for the
    whole class of overlapping-variant bug, not a special case for
    "Node.js" specifically.
    """
    if not variants:
        return text
    ordered_variants = sorted(variants, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![a-zA-Z0-9])(" + "|".join(re.escape(v) for v in ordered_variants) + r")(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )
    return pattern.sub(lambda m: variants[m.group(0).lower()], text)


def standardize_bullet_technology_names(parsed: dict) -> int:
    """STEP: Standardized technology naming - swaps a RECOGNIZED technology
    variant mention inside Experience/Project bullet text for its own
    canonical form (see skill_intelligence.known_technology_variants) -
    same technology, same fact, only the spelling changes, exactly the
    same non-fact-changing operation Stage 1's own normalization already
    performs for the Skills list. Returns the number of bullets changed.
    """
    variants = known_technology_variants()
    changed = 0

    for exp in parsed.get("experience") or []:
        points = exp.get("points") or []
        new_points = [_standardize_text(p, variants) for p in points]
        if new_points != points:
            changed += sum(1 for old, new in zip(points, new_points) if old != new)
            exp["points"] = new_points

    for proj in parsed.get("projects") or []:
        responsibilities = proj.get("responsibilities") or []
        new_resp = [_standardize_text(r, variants) for r in responsibilities]
        if new_resp != responsibilities:
            changed += sum(1 for old, new in zip(responsibilities, new_resp) if old != new)
            proj["responsibilities"] = new_resp

    return changed


# --- Technology-rendering validation (Priority 4 fix) ------------------------
#
# Defensive, final safety net - run at the very end of optimize_for_ats(),
# right before the resume is considered ATS-ready/renderable. Independent
# of the _standardize_text root-cause fix above: this exists so that ANY
# future bug producing the same class of artifact (a doubled suffix, e.g.
# "X.js.js" or "ASP.net.net") is caught and mechanically corrected rather
# than silently reaching the rendered PDF/DOCX - "validate every rendered
# technology before generation". The collapse is a pure, unambiguous text
# fix (removing an exact repeated suffix) - it can never change a fact,
# technology, or metric, so it is safe to auto-apply rather than only log.
_DUPLICATED_SUFFIX_RE = re.compile(r"\b(\w+)\.(\w{1,6})\.\2\b", re.IGNORECASE)


def _collapse_duplicated_technology_suffix(text: str) -> str:
    return _DUPLICATED_SUFFIX_RE.sub(r"\1.\2", text)


def validate_rendered_technology_names(parsed: dict) -> int:
    """Scans every Experience/Project bullet for a duplicated-suffix
    corruption pattern (e.g. "Node.js.js") and collapses it back to the
    correct single form. Returns the number of bullets fixed."""
    fixed = 0
    for exp in parsed.get("experience") or []:
        points = exp.get("points") or []
        new_points = [_collapse_duplicated_technology_suffix(p) for p in points]
        if new_points != points:
            fixed += sum(1 for old, new in zip(points, new_points) if old != new)
            exp["points"] = new_points

    for proj in parsed.get("projects") or []:
        responsibilities = proj.get("responsibilities") or []
        new_resp = [_collapse_duplicated_technology_suffix(r) for r in responsibilities]
        if new_resp != responsibilities:
            fixed += sum(1 for old, new in zip(responsibilities, new_resp) if old != new)
            proj["responsibilities"] = new_resp

    if fixed:
        logger.warning(
            "ATS Intelligence Engine: collapsed a duplicated technology-name suffix (e.g. 'X.js.js' -> "
            "'X.js') in %d bullet(s) before rendering.", fixed,
        )
    return fixed


# --- STEP 2: Duplicate keyword detection (+ fix) -----------------------------

def detect_and_fix_duplicate_keywords(parsed: dict) -> str | None:
    """Re-runs skill_intelligence.py's own dedup/alias engine as an
    idempotent safety net (defense in depth - same reasoning Phase 5's
    resume_quality_engine.py already applies to this exact check).
    Mutates `parsed["skills"]` only if it actually changes something;
    returns an accurate, human-readable description of what changed
    (duplicates removed vs. only naming/order standardized), or None if
    the list was already fully clean.
    """
    skills = parsed.get("skills")
    if not isinstance(skills, list) or not skills:
        return None
    reprocessed = build_technical_skills(skills)
    if reprocessed == skills:
        return None
    parsed["skills"] = reprocessed
    removed = len(skills) - len(reprocessed)
    if removed > 0:
        return f"Removed {removed} duplicate technology keyword(s) via ATS re-check."
    return "Standardized technology keyword naming/order in Technical Skills via ATS re-check."


# --- STEP 3/4: ATS keyword ranking + Technology prioritization --------------

def rank_keywords_for_ats(parsed: dict) -> list[tuple[str, int]]:
    """STEP: ATS keyword ranking. Scores every listed skill by how
    strongly THIS resume's own text evidences it:
      - a mention in the Professional Summary (high-visibility text) -> +2
      - a mention in a job TITLE -> +3 (the single strongest signal both
        an ATS keyword-match model and a human recruiter use), doubled for
        the most recent job (index 0 - resumes list jobs most-recent-first
        by convention throughout this pipeline)
      - a mention in that job's bullet text -> +1, doubled for the most
        recent job
      - a mention in a project's title/description/technologies/
        responsibilities -> +1
    Returns [(keyword, score), ...] sorted by descending score (ties
    broken by the skill list's own original order). Uses ONLY evidence
    inside this resume - no external keyword-popularity data.
    """
    skills = parsed.get("skills") or []
    if not skills:
        return []

    scores: dict[str, int] = {kw: 0 for kw in skills}

    summary_lower = _normalize_text(str(parsed.get("summary") or ""))
    for kw in skills:
        scores[kw] += _count_mentions(kw, summary_lower) * 2

    experience = parsed.get("experience") or []
    for index, exp in enumerate(experience):
        recency_weight = 2 if index == 0 else 1
        role_lower = _normalize_text(str(exp.get("role") or ""))
        bullets_lower = _normalize_text(" ".join(exp.get("points") or []))
        for kw in skills:
            if _count_mentions(kw, role_lower):
                scores[kw] += 3 * recency_weight
            scores[kw] += _count_mentions(kw, bullets_lower) * recency_weight

    for proj in parsed.get("projects") or []:
        proj_text = _normalize_text(
            f"{proj.get('title', '')} {proj.get('description', '')} {proj.get('technologies', '')} "
            + " ".join(proj.get("responsibilities") or [])
        )
        for kw in skills:
            scores[kw] += _count_mentions(kw, proj_text)

    ordered = sorted(skills, key=lambda kw: (-scores[kw], skills.index(kw)))
    return [(kw, scores[kw]) for kw in ordered]


def get_priority_technologies(parsed: dict, top_n: int = 10) -> list[str]:
    """STEP: Technology prioritization - the top `top_n` keywords by
    rank_keywords_for_ats, as REPORT-ONLY metadata (never reorders the
    rendered Skills list - see module docstring for why)."""
    return [kw for kw, _score in rank_keywords_for_ats(parsed)[:top_n]]


# --- STEP 5: Keyword density validation --------------------------------------

_MIN_COVERAGE_RATIO = 0.4
_OVERUSE_MENTION_THRESHOLD = 6


def validate_keyword_density(parsed: dict) -> dict:
    """STEP: Keyword density validation. Returns {"coverage_ratio",
    "warnings", "suggestions"}. `coverage_ratio` is the fraction of listed
    skills that are demonstrated anywhere in Experience/Project text (not
    just named in the Skills list) - low coverage is flagged; a keyword
    repeated unnaturally often is flagged separately as a stuffing risk -
    the concrete, measurable form of "do not keyword stuff"."""
    skills = parsed.get("skills") or []
    warnings: list[str] = []
    suggestions: list[str] = []
    if not skills:
        return {"coverage_ratio": 0.0, "warnings": warnings, "suggestions": suggestions}

    body_text = " ".join(
        [str(parsed.get("summary") or "")]
        + [p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or [])]
        + [r for proj in (parsed.get("projects") or []) for r in (proj.get("responsibilities") or [])]
    )
    body_lower = _normalize_text(body_text)

    mention_counts = {kw: _count_mentions(kw, body_lower) for kw in skills}
    demonstrated = [kw for kw, count in mention_counts.items() if count > 0]
    coverage_ratio = round(len(demonstrated) / len(skills), 2)

    if coverage_ratio < _MIN_COVERAGE_RATIO:
        warnings.append(
            f"Only {len(demonstrated)}/{len(skills)} listed skills are demonstrated anywhere in "
            "Experience/Project text - the rest appear only in the Skills list."
        )
        suggestions.append(
            "Reinforce key skills by mentioning them naturally within Experience/Project bullets, "
            "not only the Skills list."
        )

    overused = [kw for kw, count in mention_counts.items() if count > _OVERUSE_MENTION_THRESHOLD]
    if overused:
        warnings.append(f"These keywords repeat unusually often and may read as keyword-stuffed: {overused}.")
        suggestions.append("Vary phrasing instead of repeating the same keyword many times.")

    return {"coverage_ratio": coverage_ratio, "warnings": warnings, "suggestions": suggestions}


# --- STEP 6: Missing keyword detection (suggestion-only, never invented) ----

# Small, hand-curated table of commonly-PAIRED technologies - deliberately
# modest rather than exhaustive. Only ever used to SUGGEST a candidate
# confirm whether they also have a commonly-paired skill; NEVER used to add
# one - see detect_missing_keywords's own docstring.
_COMMON_PAIRINGS: dict[str, tuple[str, ...]] = {
    "docker": ("kubernetes",), "kubernetes": ("docker",),
    "react": ("typescript", "redux"), "typescript": ("react",),
    "aws": ("terraform",), "azure": ("azure devops",),
    "python": ("django", "flask"), "django": ("python",), "flask": ("python",),
    "kafka": ("zookeeper",),
    "ci/cd": ("jenkins",), "jenkins": ("ci/cd",),
    "terraform": ("aws", "azure"),
    "spring boot": ("java",), "node.js": ("express",),
    "postgresql": ("sql",), "mysql": ("sql",),
    "tensorflow": ("python",), "pytorch": ("python",),
    "microservices": ("docker", "kubernetes"),
}
_MAX_MISSING_SUGGESTIONS = 5


def detect_missing_keywords(parsed: dict) -> list[str]:
    """STEP: Missing keyword detection. Checks _COMMON_PAIRINGS against the
    candidate's own listed skills and returns up to
    `_MAX_MISSING_SUGGESTIONS` commonly-paired technologies that aren't
    already present anywhere (skills or bullet text). NEVER mutates
    `parsed` and never asserts the candidate has the suggested skill -
    every caller of this function must present it as an optional
    consideration ("if applicable"), not an automatic addition; inventing
    a skill the candidate doesn't have would be a fabrication, the one
    thing every phase of this pipeline treats as non-negotiable.
    """
    skills = parsed.get("skills") or []
    if not skills:
        return []

    body_text = _normalize_text(
        " ".join(skills)
        + " " + str(parsed.get("summary") or "")
        + " " + " ".join(p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or []))
    )
    present = {_normalize_text(kw) for kw in skills}

    suggestions: list[str] = []
    for kw in skills:
        for candidate in _COMMON_PAIRINGS.get(_normalize_text(kw), ()):
            if candidate in present or _count_mentions(candidate, body_text):
                continue
            display = candidate.title() if candidate.islower() else candidate
            if display not in suggestions:
                suggestions.append(display)
            if len(suggestions) >= _MAX_MISSING_SUGGESTIONS:
                return suggestions
    return suggestions


# --- STEP 7: Section-level ATS optimization ----------------------------------

_ATS_CRITICAL_SECTIONS = ("skills", "experience", "education")


def check_section_level_ats_optimization(parsed: dict, coverage_ratio: float) -> tuple[list[str], list[str]]:
    """STEP: Section-level ATS optimization. Confirms the ATS-critical
    sections are present, and flags the specific case where keyword
    mentions are concentrated entirely in Skills with zero reinforcement
    elsewhere (coverage_ratio == 0 despite having skills listed)."""
    warnings: list[str] = []
    suggestions: list[str] = []
    for field in _ATS_CRITICAL_SECTIONS:
        if not parsed.get(field):
            warnings.append(f"Missing {field.capitalize()} section - ATS systems weight this section heavily.")

    if coverage_ratio == 0.0 and parsed.get("skills"):
        warnings.append("Keywords appear only in the Skills section, with zero reinforcement in Experience/Projects.")
        suggestions.append("Distribute key technology mentions across Experience and Projects, not only Skills.")

    return warnings, suggestions


# --- Orchestration -------------------------------------------------------------

def generate_ats_report(parsed: dict) -> dict:
    """Runs STEPs 3-7 (ranking/prioritization/density/missing-keywords/
    section-checks) and assembles one report. Does not itself mutate
    `parsed` - see optimize_for_ats for the one function that does
    (technology-name standardization + duplicate-keyword dedup)."""
    density = validate_keyword_density(parsed)
    missing = detect_missing_keywords(parsed)
    section_warnings, section_suggestions = check_section_level_ats_optimization(parsed, density["coverage_ratio"])

    warnings = list(density["warnings"]) + section_warnings
    suggestions = list(density["suggestions"]) + section_suggestions
    if missing:
        suggestions.append(
            "Commonly-paired technologies not currently listed (add ONLY if genuinely applicable - "
            "never fabricate experience): " + ", ".join(missing)
        )

    score = 100 - 10 * (len(density["warnings"]) + len(section_warnings))
    score = max(score, 0)

    return {
        "score": score,
        "warnings": warnings,
        "suggestions": suggestions,
        "priority_keywords": get_priority_technologies(parsed),
        "keyword_coverage_ratio": density["coverage_ratio"],
        "missing_keyword_suggestions": missing,
    }


def optimize_for_ats(parsed: dict) -> tuple[dict, dict]:
    """Public entry point. Applies the two safe, fact-preserving automatic
    fixes (bullet technology-name standardization, duplicate-keyword
    re-check) directly to `parsed`, then computes the full ATS report
    described above. Returns (parsed, report) - `report` is for internal
    logging/validation, never rendered.
    """
    changed_bullets = standardize_bullet_technology_names(parsed)
    dedup_fix = detect_and_fix_duplicate_keywords(parsed)
    validate_rendered_technology_names(parsed)

    report = generate_ats_report(parsed)
    if changed_bullets:
        message = f"Standardized {changed_bullets} technology mention(s) in Experience/Projects to ATS-standard naming."
        report["warnings"].insert(0, message)
        logger.info("ATS Intelligence Engine: %s", message)
    if dedup_fix:
        report["warnings"].insert(0, dedup_fix)
        logger.info("ATS Intelligence Engine: %s", dedup_fix)

    logger.info("ATS Intelligence Engine: score=%d, coverage=%.2f", report["score"], report["keyword_coverage_ratio"])
    return parsed, report
