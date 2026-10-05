"""Resume Scoring Engine - Phase 8 of the Resume Intelligence Engine.

Pure analysis, NEVER modification: every function in this module takes
`parsed` and returns a report - none of them ever writes to `parsed` or
any nested dict/list inside it. This is deliberately unlike Phases 5-7
(resume_quality_engine.py, ats_intelligence.py, industry_intelligence.py),
which all apply automatic fixes - this phase's own mission is explicitly
"never modify the resume automatically, only provide analysis", so it is
NOT wired to call any of those phases' mutating orchestrators
(run_quality_engine/optimize_for_ats/apply_industry_intelligence). It only
ever calls genuinely pure, read-only functions - skill_intelligence.
build_technical_skills() takes a list and returns a NEW list without ever
mutating the one it's given, experience_refiner.bullet_range_for_role() is
a pure lookup - and otherwise reimplements small, local, read-only checks
rather than reusing another module's mutating helpers, the same "small
parallel implementation per module" pattern already used throughout this
pipeline (see e.g. every module's own local `_years_of_experience`).

ALGORITHM: 9 named sub-scores, each weighted (weights sum to 100 - see
_WEIGHTS), combined into one Overall Resume Score (0-100):

    Experience               15   presence, bullet-count-vs-role-tier target,
                                  weak-verb ratio, metric coverage
    ATS                      15   keyword coverage (skills demonstrated in
                                  Experience/Projects text), section completeness
    Duplicate Content        12   exact/near-duplicate skills, bullets, projects
    Summary                 12   presence, length, sentence count, genericness
    Recruiter Readability    10   bullet length distribution, all-caps, short/
                                  verbose bullets
    Technical Skills         10   count vs. recommended range, category diversity
    Information Density      10   fraction of bullets carrying a metric/
                                  specific/technology vs. filler
    Project                   8   bullet-count target, description/technologies
                                  present
    Education                 8   presence, plausible entry count

Every score is computed independently and reported alongside the specific
finding that drove it - the goal is actionable, specific feedback ("Add
measurable achievements", "Remove duplicate skills"), not just a number.
"""
import difflib
import re

from app.services.experience_refiner import bullet_range_for_role
from app.services.skill_intelligence import MAX_TECHNICAL_SKILLS, build_technical_skills
from app.services.tool_classifier import MAX_TOOLS

_WEIGHTS = {
    "experience": 15, "ats": 15, "duplicate_content": 12, "summary": 12,
    "recruiter_readability": 10, "technical_skills": 10, "information_density": 10,
    "project": 8, "education": 8,
}
assert sum(_WEIGHTS.values()) == 100, "resume_scoring_engine weights must sum to 100"

# Recruiter personas (Enterprise feature) - each persona is a full weight
# profile over the same 9 dimensions, summing to 100 like _WEIGHTS itself.
# "general" IS _WEIGHTS - score_resume(parsed) with no persona argument
# (every caller before this feature existed) reproduces the exact original
# scoring, unchanged - this is what keeps the feature backward compatible.
PERSONAS: tuple[str, ...] = ("general", "technical_recruiter", "executive_recruiter", "startup_recruiter")

_PERSONA_WEIGHTS: dict[str, dict[str, int]] = {
    "general": dict(_WEIGHTS),
    # Emphasizes ATS matching and Technical Skills depth - what a
    # technical recruiter screens for first.
    "technical_recruiter": {
        "experience": 15, "ats": 20, "duplicate_content": 10, "summary": 8,
        "recruiter_readability": 7, "technical_skills": 20, "information_density": 10,
        "project": 7, "education": 3,
    },
    # Emphasizes Summary/Experience/Readability - narrative and leadership
    # signal over raw technology-keyword depth.
    "executive_recruiter": {
        "experience": 20, "ats": 8, "duplicate_content": 10, "summary": 20,
        "recruiter_readability": 15, "technical_skills": 5, "information_density": 8,
        "project": 5, "education": 9,
    },
    # Emphasizes Information Density and shipped Projects - what a
    # startup/scale-up recruiter screens for (concrete, high-output work)
    # over formal credentials.
    "startup_recruiter": {
        "experience": 15, "ats": 10, "duplicate_content": 10, "summary": 10,
        "recruiter_readability": 8, "technical_skills": 12, "information_density": 20,
        "project": 12, "education": 3,
    },
}
for _persona_name, _persona_weights in _PERSONA_WEIGHTS.items():
    assert sum(_persona_weights.values()) == 100, f"persona {_persona_name!r} weights must sum to 100"
    assert set(_persona_weights.keys()) == set(_WEIGHTS.keys()), f"persona {_persona_name!r} missing a dimension"


def get_persona_weights(persona: str) -> dict[str, int]:
    """Returns the weight profile for `persona`, falling back to
    "general" (== the original _WEIGHTS) for an unrecognized value -
    defensive, never raises."""
    return _PERSONA_WEIGHTS.get(persona, _PERSONA_WEIGHTS["general"])

_METRIC_PATTERNS = (
    re.compile(r"\b\d+(?:\.\d+)?%"),
    re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|million|billion)?\b", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s?[kKmM]?\+?\s*(?:users|customers|applications|apps|projects|servers|"
               r"engineers|team members|clients|transactions|requests)\b", re.IGNORECASE),
    re.compile(r"\bteam of \d+\b", re.IGNORECASE),
    re.compile(r"\b\d{2,}\+"),
)
_WEAK_VERB_STARTS = ("worked", "responsible for", "handled", "helped", "participated", "was involved", "assisted")
_PROJECT_BULLET_RANGE = (3, 5)
_NEAR_DUPLICATE_THRESHOLD = 0.85


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _has_metric(bullet: str) -> bool:
    return any(pattern.search(bullet) for pattern in _METRIC_PATTERNS)


def _is_weak_opening(bullet: str) -> bool:
    return bullet.strip().lower().startswith(_WEAK_VERB_STARTS)


def _all_bullets(parsed: dict) -> list[str]:
    bullets = [p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or [])]
    bullets += [r for proj in (parsed.get("projects") or []) for r in (proj.get("responsibilities") or [])]
    return bullets


def _near_duplicate_count(items: list[str], threshold: float = _NEAR_DUPLICATE_THRESHOLD) -> int:
    """Read-only near-duplicate COUNT (never merges/mutates the input) -
    how many items would be dropped by a conservative fuzzy merge."""
    kept: list[str] = []
    duplicates = 0
    seen_exact: set[str] = set()
    for item in items:
        key = _normalize_text(item)
        if key in seen_exact:
            duplicates += 1
            continue
        seen_exact.add(key)
        is_near_dup = any(
            difflib.SequenceMatcher(None, item.lower(), existing.lower()).ratio() >= threshold for existing in kept
        )
        if is_near_dup:
            duplicates += 1
        else:
            kept.append(item)
    return duplicates


# --- Experience Score ----------------------------------------------------------

def _score_experience(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    experience = parsed.get("experience") or []
    active_jobs = [exp for exp in experience if not exp.get("is_career_break")]
    if not active_jobs:
        warnings.append("Missing Professional Experience.")
        suggestions.append("Add at least one work experience entry.")
        return 0

    score = weight
    total_bullets, weak_bullets, metric_bullets = 0, 0, 0
    for exp in active_jobs:
        points = exp.get("points") or []
        if not points:
            warnings.append(f"{exp.get('company') or '(no company)'} has no bullet points.")
            score -= round(weight * 0.15)
            continue
        _lo, hi = bullet_range_for_role(exp.get("role"))
        if len(points) > hi:
            warnings.append(f"{exp.get('company') or '(no company)'} has {len(points)} bullets - above the {hi}-bullet target.")
            score -= round(weight * 0.1)
        for point in points:
            total_bullets += 1
            if _is_weak_opening(point):
                weak_bullets += 1
            if _has_metric(point):
                metric_bullets += 1

    if total_bullets and weak_bullets / total_bullets > 0.2:
        warnings.append(f"{weak_bullets}/{total_bullets} experience bullets open with a weak verb.")
        suggestions.append("Rewrite weak-verb bullets to start with a strong action verb.")
        score -= round(weight * 0.2)

    if total_bullets and metric_bullets / total_bullets < 0.3:
        warnings.append(f"Only {metric_bullets}/{total_bullets} experience bullets state a measurable metric.")
        suggestions.append("Add measurable achievements (metrics, percentages, scale) to more bullet points.")
        score -= round(weight * 0.25)

    return max(score, 0)


# --- ATS Score -------------------------------------------------------------------

def _score_ats(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    skills = parsed.get("skills") or []
    if not skills:
        warnings.append("Missing Technical Skills section - ATS systems weight this heavily.")
        return 0

    score = weight
    body_lower = _normalize_text(
        " ".join([str(parsed.get("summary") or "")] + _all_bullets(parsed))
    )
    demonstrated = sum(
        1 for kw in skills
        if re.search(r"(?<![a-z0-9])" + re.escape(str(kw).lower()) + r"(?![a-z0-9])", body_lower)
    )
    coverage = demonstrated / len(skills)
    if coverage < 0.4:
        warnings.append(f"Only {demonstrated}/{len(skills)} skills are demonstrated in Experience/Project text.")
        suggestions.append("Reinforce key skills by mentioning them within Experience/Project bullets, not only Skills.")
        score -= round(weight * 0.5)

    for field, label in (("experience", "Experience"), ("education", "Education")):
        if not parsed.get(field):
            warnings.append(f"Missing {label} section - ATS systems weight this heavily.")
            score -= round(weight * 0.25)

    return max(score, 0)


# --- Duplicate Content Score -----------------------------------------------------

def _score_duplicate_content(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    score = weight

    skills = parsed.get("skills") or []
    if isinstance(skills, list) and skills:
        reprocessed = build_technical_skills(skills, max_items=len(skills))
        if len(reprocessed) < len(skills):
            removed = len(skills) - len(reprocessed)
            warnings.append(f"{removed} duplicate/aliased technology keyword(s) found in Technical Skills.")
            suggestions.append("Remove duplicate skills (including spelling/alias variants of the same technology).")
            score -= round(weight * 0.35)

    for exp in parsed.get("experience") or []:
        points = exp.get("points") or []
        dup_count = _near_duplicate_count(points)
        if dup_count:
            warnings.append(f"{exp.get('company') or '(no company)'} has {dup_count} duplicate/near-duplicate bullet(s).")
            score -= round(weight * 0.15)

    project_titles = [_normalize_text(p.get("title") or "") for p in (parsed.get("projects") or [])]
    project_titles = [t for t in project_titles if t]
    if len(project_titles) != len(set(project_titles)):
        warnings.append("Duplicate project titles found.")
        suggestions.append("Merge or remove duplicate/redundant projects.")
        score -= round(weight * 0.2)

    return max(score, 0)


# --- Summary Score ---------------------------------------------------------------

def _score_summary(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        warnings.append("Missing Professional Summary.")
        suggestions.append("Add a 3-5 sentence Professional Summary covering experience, role, and key expertise.")
        return 0

    score = weight
    if len(summary) < 40:
        warnings.append("Professional Summary is very short.")
        suggestions.append("Improve the summary - expand it to 3-5 complete sentences.")
        score -= round(weight * 0.5)
    sentence_count = len([s for s in re.split(r"[.!?]+", summary) if s.strip()])
    if sentence_count < 2:
        suggestions.append("Improve the summary - write it as 3-5 complete sentences rather than one line.")
        score -= round(weight * 0.2)
    if summary.isupper():
        warnings.append("Professional Summary is written in all caps.")
        score -= round(weight * 0.2)

    return max(score, 0)


# --- Recruiter Readability Score --------------------------------------------------

# STEP 11 - Readability: ~28 words is a reasonable proxy for "roughly two
# lines" at a typical resume's font size/margins (tightened from a
# previous, more permissive 40-word threshold) - this module can't measure
# actual rendered line-wrap (that's file_generator.py/the Rendering
# Engine's territory, out of scope here), so word count is the closest
# content-level signal available.
_VERBOSE_WORD_COUNT = 28


def _score_recruiter_readability(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    bullets = _all_bullets(parsed)
    if not bullets:
        return weight

    score = weight
    short = [b for b in bullets if len(b.split()) < 3]
    verbose = [b for b in bullets if len(b.split()) > _VERBOSE_WORD_COUNT]
    if short:
        warnings.append(f"{len(short)} bullet(s) are too short to convey real impact.")
        score -= round(weight * 0.25)
    if verbose:
        warnings.append(f"{len(verbose)} bullet(s) are overly verbose (over {_VERBOSE_WORD_COUNT} words).")
        suggestions.append("Break up long, run-on bullets into concise, focused statements.")
        score -= round(weight * 0.25)

    return max(score, 0)


# --- Technical Skills Score --------------------------------------------------------

def _score_technical_skills(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    skills = parsed.get("skills") or []
    if not skills:
        warnings.append("Missing Technical Skills section.")
        suggestions.append("Add a Technical Skills section listing core competencies.")
        return 0

    score = weight
    if len(skills) < 5:
        warnings.append("Technical Skills section has very few entries.")
        score -= round(weight * 0.4)
    elif len(skills) > MAX_TECHNICAL_SKILLS:
        warnings.append(f"Technical Skills has {len(skills)} entries - above the recommended {MAX_TECHNICAL_SKILLS}.")
        suggestions.append("Reduce keyword dumping - trim Technical Skills to the most relevant, senior items.")
        score -= round(weight * 0.4)

    tools = parsed.get("tools") or []
    if len(tools) > MAX_TOOLS:
        warnings.append(f"Tools has {len(tools)} entries - above the recommended {MAX_TOOLS}.")
        suggestions.append("Reduce keyword dumping - trim Tools to the most relevant, frequently-used software.")
        score -= round(weight * 0.2)

    return max(score, 0)


# --- Information Density Score -----------------------------------------------------

def _score_information_density(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    bullets = _all_bullets(parsed)
    if not bullets:
        return weight

    substantive = sum(1 for b in bullets if _has_metric(b) or len(b.split()) >= 6)
    ratio = substantive / len(bullets)
    score = round(weight * ratio)
    if ratio < 0.5:
        warnings.append("Many bullets are low on substance (no metric, technology, or specific detail).")
        suggestions.append("Increase information density - pack more concrete detail (technology, scale, outcome) into each bullet.")
    return max(score, 0)


# --- Project Score -----------------------------------------------------------------

def _score_project(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    projects = parsed.get("projects") or []
    if not projects:
        return weight  # optional section - absence isn't penalized, matching this pipeline's established rule

    score = weight
    for proj in projects:
        responsibilities = proj.get("responsibilities") or []
        _lo, hi = _PROJECT_BULLET_RANGE
        if len(responsibilities) > hi:
            warnings.append(
                f"Project {proj.get('title') or '(untitled)'} has {len(responsibilities)} bullets - "
                f"above the {hi}-bullet target."
            )
            score -= round(weight * 0.2)
        if not proj.get("description") and not proj.get("technologies"):
            warnings.append(f"Project {proj.get('title') or '(untitled)'} is missing a description/technologies.")
            score -= round(weight * 0.15)

    return max(score, 0)


# --- Education Score -----------------------------------------------------------------

def _score_education(parsed: dict, warnings: list[str], suggestions: list[str], weight: int) -> int:
    education = parsed.get("education") or []
    if not education:
        warnings.append("Missing Education section.")
        suggestions.append("Add educational background (degree, institution, year).")
        return 0

    score = weight
    if not (parsed.get("certifications") or []):
        suggestions.append("Add missing certifications if you have any relevant ones - they strengthen ATS matching.")

    return score


# --- Orchestration -------------------------------------------------------------

def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = _normalize_text(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def score_resume(parsed: dict, persona: str = "general") -> dict:
    """Public entry point. Pure - never mutates `parsed`. Returns
    {"score": int, "breakdown": {...9 named sub-scores...}, "warnings":
    [...], "suggestions": [...], "persona": str}. `score` is the weighted
    Overall Resume Score (0-100) - see module docstring for the
    9-dimension algorithm.

    `persona` (Enterprise feature - see PERSONAS/get_persona_weights)
    selects which weight profile to score against; defaults to "general"
    (== the original _WEIGHTS), so every caller written before personas
    existed gets byte-for-byte the same score as always.
    """
    weights = get_persona_weights(persona)
    warnings: list[str] = []
    suggestions: list[str] = []

    breakdown = {
        "experience": _score_experience(parsed, warnings, suggestions, weights["experience"]),
        "ats": _score_ats(parsed, warnings, suggestions, weights["ats"]),
        "duplicate_content": _score_duplicate_content(parsed, warnings, suggestions, weights["duplicate_content"]),
        "summary": _score_summary(parsed, warnings, suggestions, weights["summary"]),
        "recruiter_readability": _score_recruiter_readability(
            parsed, warnings, suggestions, weights["recruiter_readability"]
        ),
        "technical_skills": _score_technical_skills(parsed, warnings, suggestions, weights["technical_skills"]),
        "information_density": _score_information_density(
            parsed, warnings, suggestions, weights["information_density"]
        ),
        "project": _score_project(parsed, warnings, suggestions, weights["project"]),
        "education": _score_education(parsed, warnings, suggestions, weights["education"]),
    }

    return {
        "score": sum(breakdown.values()),
        "breakdown": breakdown,
        "warnings": _dedupe_preserve_order(warnings),
        "suggestions": _dedupe_preserve_order(suggestions),
        "persona": persona if persona in _PERSONA_WEIGHTS else "general",
    }
