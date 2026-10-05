"""Final resume quality scoring - the last step before the Resume Builder
(file_generator.py) runs.

Pipeline position: Extraction -> Normalization -> Resume Intelligence
Engine -> resume_validator.py (clean + flag) -> **this module** (score) ->
Resume Builder (DOCX/PDF). Operates on the same already-cleaned `parsed`
dict resume_validator.py just produced, plus that same pass's
ValidationReport as an input signal (rather than re-implementing duplicate/
completeness detection a second time) - this module ADDS a 0-100 score and
recruiter-style suggestions on top of what resume_validator.py already
found, it doesn't duplicate that work.

Evaluates 9 named dimensions (weights sum to 100):
    Professional Summary   15
    Skills                 15
    Experience             20
    Education              10
    Formatting              10
    ATS Compliance          10
    Readability             10
    Duplicate Detection      5
    Section Completeness     5

Returns exactly {"score": int, "warnings": [...], "suggestions": [...]} -
plus a "breakdown" of the per-dimension points, additive and safe to
ignore for any caller that only wants the three required keys.

Gating ("only generate DOCX/PDF after validation passes" - see
resume_has_minimum_viable_content): deliberately a low, content-presence
bar, not a score cutoff. A thin-but-real resume (a first-time job seeker
with little to list) still deserves a generated document, a lower score,
and constructive suggestions - refusing to render anything is reserved for
the genuinely-empty case where there is nothing worth putting in a
document at all (see the function's own docstring for the exact rule).
"""
import re

# Same action-verb vocabulary prompts.py asks Gemini to use when writing
# bullets (_ACTION_VERB_EXAMPLES) - kept as its own set here rather than
# imported, since the two serve different purposes (one is instruction
# text for a prompt, this one is a lookup used to check the RESULT).
_ACTION_VERBS = {
    "designed", "developed", "architected", "implemented", "optimized", "led",
    "configured", "migrated", "automated", "integrated", "improved", "managed",
    "coordinated", "analyzed", "delivered", "negotiated", "streamlined",
    "facilitated", "directed", "launched", "reduced", "increased", "presented",
    "audited", "diagnosed", "built", "created", "established", "drove",
    "spearheaded", "executed", "resolved", "mentored", "collaborated",
}

_WEIGHTS = {
    "summary": 15,
    "skills": 15,
    "experience": 20,
    "education": 10,
    "formatting": 10,
    "ats_compliance": 10,
    "readability": 10,
    "duplicate_detection": 5,
    "section_completeness": 5,
}
assert sum(_WEIGHTS.values()) == 100, "quality-checker weights must sum to 100"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = re.sub(r"\s+", " ", item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _count_skill_items(skills) -> int:
    if isinstance(skills, dict):
        return sum(len(items) for items in skills.values())
    return len(skills or [])


def _score_summary(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    weight = _WEIGHTS["summary"]
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        warnings.append("Missing Professional Summary.")
        suggestions.append(
            "Add a 3-5 sentence Professional Summary covering years of experience, primary role, "
            "and key expertise."
        )
        return 0

    sentence_count = len([s for s in re.split(r"[.!?]+", summary) if s.strip()])
    if len(summary) < 40:
        warnings.append("Professional Summary is very short.")
        suggestions.append("Expand the Professional Summary to 3-5 complete sentences.")
        return round(weight * 0.45)
    if sentence_count < 2:
        suggestions.append("Consider writing the Professional Summary as 3-5 complete sentences.")
        return round(weight * 0.7)
    return weight


def _score_skills(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    weight = _WEIGHTS["skills"]
    skills = parsed.get("skills")
    total_items = _count_skill_items(skills)
    if not total_items:
        warnings.append("Missing Skills section.")
        suggestions.append("Add a Skills section listing core technical competencies.")
        return 0

    score = weight
    if not isinstance(skills, dict):
        suggestions.append(
            "Group skills into categories (e.g. Programming Languages, Cloud Platforms) for easier scanning."
        )
        score -= round(weight * 0.2)
    if total_items < 5:
        warnings.append("Skills section has very few entries.")
        suggestions.append("List more relevant technical skills - aim for at least 8-10.")
        score -= round(weight * 0.35)
    return max(score, 0)


def _score_experience(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    weight = _WEIGHTS["experience"]
    experience = parsed.get("experience") or []
    if not experience:
        warnings.append("Missing Professional Experience.")
        suggestions.append("Add at least one work experience entry.")
        return 0

    score = weight
    total_bullets = 0
    action_verb_bullets = 0
    for exp in experience:
        if exp.get("is_career_break"):
            continue
        points = exp.get("points") or []
        if not points:
            warnings.append(f"{exp.get('company') or 'A job entry'} has no bullet points.")
            score -= round(weight * 0.15)
            continue
        total_bullets += len(points)
        for point in points:
            first_word = (point.strip().split(" ") or [""])[0].strip(".,;:").lower()
            if first_word in _ACTION_VERBS:
                action_verb_bullets += 1

    if total_bullets and (action_verb_bullets / total_bullets) < 0.6:
        suggestions.append("Start more bullet points with strong action verbs (e.g. Led, Designed, Implemented).")
        score -= round(weight * 0.2)

    return max(score, 0)


def _score_education(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    weight = _WEIGHTS["education"]
    if not (parsed.get("education") or []):
        warnings.append("Missing Education section.")
        suggestions.append("Add educational background (degree, institution, year).")
        return 0
    return weight


def _score_formatting(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    """Structural readiness of the DATA for formatting - not the rendered
    PDF/DOCX layout itself, which file_generator.py already guarantees is
    consistent regardless of what's in `parsed` (margins, typography,
    page-fit are its job, not this one's)."""
    weight = _WEIGHTS["formatting"]
    score = weight
    for exp in parsed.get("experience") or []:
        if not exp.get("is_career_break") and not str(exp.get("duration") or "").strip():
            warnings.append(f"{exp.get('company') or 'A job entry'} is missing a duration/date range.")
            score -= round(weight * 0.2)
        if any(len(str(p)) > 300 for p in (exp.get("points") or [])):
            suggestions.append("Break up very long bullet points into shorter, focused statements.")
            score -= round(weight * 0.15)
    return max(score, 0)


def _score_ats_compliance(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    weight = _WEIGHTS["ats_compliance"]
    score = weight
    if not str(parsed.get("name") or "").strip():
        warnings.append("Missing candidate name - may reduce ATS match quality.")
        score -= round(weight * 0.3)
    if not str(parsed.get("email") or "").strip():
        warnings.append("Missing email - ATS systems typically expect contact information.")
        score -= round(weight * 0.3)
    keyword_count = _count_skill_items(parsed.get("skills")) + len(parsed.get("tools") or [])
    if keyword_count < 5:
        suggestions.append("Add more relevant keywords (skills/tools) to improve ATS keyword matching.")
        score -= round(weight * 0.4)
    return max(score, 0)


def _score_readability(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    weight = _WEIGHTS["readability"]
    score = weight
    summary = str(parsed.get("summary") or "")
    if summary and summary.isupper():
        warnings.append("Professional Summary is written in all caps.")
        score -= round(weight * 0.3)

    short_bullet_found = False
    for exp in parsed.get("experience") or []:
        for point in exp.get("points") or []:
            if len(str(point).split()) < 3:
                short_bullet_found = True
    if short_bullet_found:
        suggestions.append("Some bullet points are too short to convey real impact - expand them.")
        score -= round(weight * 0.2)

    return max(score, 0)


def _score_duplicate_detection(validation_warnings: list[str], warnings: list[str], suggestions: list[str]) -> int:
    """Uses resume_validator.py's own findings rather than re-detecting
    duplicates a second time - this module scores what that pass already
    found, it doesn't duplicate the detection logic."""
    weight = _WEIGHTS["duplicate_detection"]
    duplicate_warnings = [w for w in validation_warnings if "duplicate" in w.lower() or "repeated" in w.lower()]
    if not duplicate_warnings:
        return weight
    warnings.extend(duplicate_warnings)
    suggestions.append(
        "The source data contained repeated content (already removed automatically) - review the "
        "original resume for redundant entries."
    )
    return max(weight - len(duplicate_warnings), 0)


def _score_section_completeness(validation_warnings: list[str], warnings: list[str], suggestions: list[str]) -> int:
    """Also draws on resume_validator.py's findings (its "missing"/"empty"
    warnings) rather than re-checking section presence a second time."""
    weight = _WEIGHTS["section_completeness"]
    completeness_warnings = [
        w for w in validation_warnings if "missing" in w.lower() or "empty" in w.lower()
    ]
    if not completeness_warnings:
        return weight
    warnings.extend(completeness_warnings)
    suggestions.append("Fill in the missing/empty sections noted above for a more complete resume.")
    return max(weight - len(completeness_warnings), 0)


def check_resume_quality(parsed: dict, validation_warnings: list[str] | None = None) -> dict:
    """Scores `parsed` (already cleaned by resume_validator.py) across the
    9 weighted dimensions above. `validation_warnings` is
    resume_validator.py's ValidationReport.warnings - pass it so Duplicate
    Detection/Section Completeness reuse that pass's findings instead of
    re-detecting them. Returns exactly {"score", "warnings", "suggestions"}
    plus an additive "breakdown" of per-dimension points.
    """
    validation_warnings = validation_warnings or []
    warnings: list[str] = []
    suggestions: list[str] = []

    breakdown = {
        "summary": _score_summary(parsed, warnings, suggestions),
        "skills": _score_skills(parsed, warnings, suggestions),
        "experience": _score_experience(parsed, warnings, suggestions),
        "education": _score_education(parsed, warnings, suggestions),
        "formatting": _score_formatting(parsed, warnings, suggestions),
        "ats_compliance": _score_ats_compliance(parsed, warnings, suggestions),
        "readability": _score_readability(parsed, warnings, suggestions),
        "duplicate_detection": _score_duplicate_detection(validation_warnings, warnings, suggestions),
        "section_completeness": _score_section_completeness(validation_warnings, warnings, suggestions),
    }

    return {
        "score": sum(breakdown.values()),
        "warnings": _dedupe_preserve_order(warnings),
        "suggestions": _dedupe_preserve_order(suggestions),
        "breakdown": breakdown,
    }


def resume_has_minimum_viable_content(parsed: dict) -> bool:
    """True if there's genuinely enough content to be worth rendering a
    document at all - deliberately a very low bar. A thin resume (missing
    several sections) is still rendered - that's exactly what the score/
    warnings/suggestions above are for, not a reason to block generation.
    This only refuses a document that would come out completely or almost
    completely blank: no name, no email, no experience, no skills, and no
    education, all at once.
    """
    return bool(
        str(parsed.get("name") or "").strip()
        or str(parsed.get("email") or "").strip()
        or parsed.get("experience")
        or _count_skill_items(parsed.get("skills"))
        or parsed.get("education")
    )
