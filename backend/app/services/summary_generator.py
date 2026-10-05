"""Recruiter-quality Professional Summary generation.

Stage 1's own extracted summary (extraction_pipeline.py's build_summary_
prompt) is written from the raw source text alone and often just lightly
rephrases whatever summary paragraph the candidate already had - or, on a
resume with no summary section, a plain restatement of the most recent
role. This module replaces that with a genuinely recruiter-quality
version, synthesized from the candidate's ACTUAL work history and
optimized/categorized skills (not the original wording) - years of
experience, primary role, major technologies, industry (only if
evidenced), and leadership experience (only if applicable).

Called by resume_optimizer.py after skills have already been categorized
and experience already refined, so this always writes from the same
optimized data that ends up on the final resume, not Stage 1's raw form.

Never raises: on any Gemini failure, falls back to a deterministic summary
synthesized from the candidate's own extracted data (see
_build_deterministic_summary) rather than Stage 1's own often one-line
summary verbatim - a real, if plainer, 3-5 sentence recruiter-quality
paragraph beats a bare "Full Stack Developer." with nothing else.
"""
import datetime
import logging
import re

from app.services.experience_intelligence import has_impact_signal
from app.services.gemini_client import GeminiError, call_gemini
from app.services.prompts import build_summary_optimize_prompt

logger = logging.getLogger(__name__)


def _job_context_lines(experience: list[dict] | None) -> list[str]:
    """Company/role/duration lines Gemini uses to infer years of experience,
    primary role, and leadership experience - career-break entries are
    excluded, they're not a "role" to summarize the candidate by."""
    return [
        f"{exp.get('role') or 'Role'} at {exp.get('company') or 'Company'} ({exp.get('duration') or 'dates unknown'})"
        for exp in (experience or [])
        if not exp.get("is_career_break")
    ]


# --- Deterministic fallback summary (Priority 3 fix) -------------------------
#
# Root cause this addresses: on a Gemini failure, this module previously
# just returned Stage 1's own original summary unchanged - which, for a
# candidate whose resume had no summary section (or a one-line placeholder
# like "Full Stack Developer."), meant the FINAL rendered resume ships that
# same one-line/empty summary with no improvement at all. This builds a
# genuine 3-5 sentence summary using ONLY data already extracted elsewhere
# in `structured` (experience, skills, certifications) - the same
# never-invent standard every other phase of this pipeline holds to: every
# clause here is either a fact directly read off the resume (years of
# experience computed from listed job durations, the most recent job's own
# role/company, the candidate's own categorized skills, a certification
# name already listed) or an ALREADY-STATED bullet reused verbatim (the
# business-impact clause) - never a new claim, adjective, or metric.
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)
_LEADERSHIP_KEYWORDS = ("led a team", "led the team", "managed a team", "team of", "mentored", "mentoring")


def _years_of_experience(experience: list[dict] | None) -> int:
    """Reimplemented locally (same approach as file_generator.py/resume_
    quality_engine.py/experience_intelligence.py's own copies) rather than
    importing a private cross-module helper - an established pattern in
    this pipeline. Returns the span between the earliest and latest year
    mentioned across every non-career-break job's `duration` string."""
    current_year = datetime.date.today().year
    years: list[int] = []
    for exp in experience or []:
        if exp.get("is_career_break"):
            continue
        duration = str(exp.get("duration") or "")
        if _PRESENT_RE.search(duration):
            years.append(current_year)
        years.extend(int(y) for y in _YEAR_RE.findall(duration))
    if not years:
        return 0
    return max(years) - min(years)


def _most_recent_job(experience: list[dict] | None) -> dict | None:
    for exp in experience or []:
        if not exp.get("is_career_break") and (exp.get("role") or exp.get("company")):
            return exp
    return None


def _top_skills(categorized_skills: dict[str, list[str]] | None, limit: int = 6) -> list[str]:
    flat = [skill for items in (categorized_skills or {}).values() for skill in items]
    return flat[:limit]


def _has_leadership_evidence(experience: list[dict] | None) -> bool:
    for exp in experience or []:
        role_lower = str(exp.get("role") or "").lower()
        if "lead" in role_lower or "manager" in role_lower or "director" in role_lower:
            return True
        bullets_lower = " ".join(exp.get("points") or []).lower()
        if any(kw in bullets_lower for kw in _LEADERSHIP_KEYWORDS):
            return True
    return False


def _first_impact_bullet(experience: list[dict] | None) -> str | None:
    """Returns the first ALREADY-STATED bullet (in job order) that carries
    a real metric/outcome (see experience_intelligence.has_impact_signal) -
    reused verbatim, never reworded, so the summary's business-impact
    clause never states anything the Experience section doesn't already
    state itself."""
    for exp in experience or []:
        if exp.get("is_career_break"):
            continue
        for bullet in exp.get("points") or []:
            if has_impact_signal(bullet):
                return bullet.strip()
    return None


def _build_deterministic_summary(structured: dict, categorized_skills: dict[str, list[str]] | None) -> str:
    """Priority 3 fix - a smarter, still entirely deterministic (no Gemini)
    fallback summary, used only when Gemini is unavailable. Builds up to 5
    factual sentences, each added only when its underlying evidence
    actually exists (never padded to hit a target count):

      1. Most recent role/company, plus total years of experience if
         computable from listed job durations.
      2. Primary technologies/skills, from the same categorized skills the
         Gemini path would have used.
      3. Leadership/mentoring, only if the work history evidences it.
      4. A genuine business-impact bullet already stated in Experience
         (reused verbatim, never reworded).
      5. A certification already listed, if any.

    Returns "" (never raises) if there's nothing at all to build from - the
    caller falls back to Stage 1's original summary in that case."""
    experience = structured.get("experience") or []
    sentences: list[str] = []

    recent = _most_recent_job(experience)
    if recent:
        role = str(recent.get("role") or "").strip() or "Professional"
        company = str(recent.get("company") or "").strip()
        years = _years_of_experience(experience)
        if years >= 1:
            year_phrase = f"{years}+ year{'s' if years != 1 else ''} of experience"
            if company:
                sentences.append(f"{role} with {year_phrase}, most recently at {company}.")
            else:
                sentences.append(f"{role} with {year_phrase}.")
        elif company:
            sentences.append(f"{role} at {company}.")
        else:
            sentences.append(f"{role}.")

    skills = _top_skills(categorized_skills)
    if skills:
        if len(skills) == 1:
            sentences.append(f"Skilled in {skills[0]}.")
        else:
            sentences.append(f"Skilled in {', '.join(skills[:-1])}, and {skills[-1]}.")

    if _has_leadership_evidence(experience):
        sentences.append("Experienced in leading and mentoring team members.")

    impact_bullet = _first_impact_bullet(experience)
    if impact_bullet:
        sentences.append(impact_bullet if impact_bullet.endswith((".", "!", "?")) else impact_bullet + ".")

    certifications = structured.get("certifications") or []
    if certifications:
        cert_name = str(certifications[0]).split(" - ")[0].strip()
        if cert_name:
            sentences.append(f"Holds the {cert_name} certification.")

    return " ".join(sentences[:5])


def generate_summary(structured: dict, categorized_skills: dict[str, list[str]], tone: str = "Professional") -> str:
    """Generates a 3-5 sentence recruiter-quality Professional Summary from
    the candidate's work history (`structured["experience"]`) and already-
    categorized skills - covering years of experience, primary role, major
    technologies, industry (only if evidenced), and leadership experience
    (only if applicable). Written as entirely new sentences, never copied
    from `structured["summary"]` (Stage 1's own extracted summary, passed
    to the prompt only so it can be told not to reuse it). Never invents
    anything not evidenced by the work history/skills given.

    Falls back to a deterministic summary synthesized from the candidate's
    own extracted data on any Gemini failure (see _build_deterministic_
    summary), or Stage 1's original summary if even that has nothing to
    build from - never raises.
    """
    job_lines = _job_context_lines(structured.get("experience"))
    original_summary = str(structured.get("summary") or "").strip()
    try:
        result = call_gemini(
            build_summary_optimize_prompt(job_lines, categorized_skills, original_summary, tone=tone)
        )
        summary = str(result.get("summary") or "").strip()
        if not summary:
            raise ValueError("Gemini returned no usable summary")
        return summary
    except (GeminiError, ValueError, TypeError) as exc:
        logger.warning(
            "Summary generation failed (%s) - falling back to a deterministic summary synthesized "
            "from the candidate's own extracted data.", exc,
        )
        fallback = _build_deterministic_summary(structured, categorized_skills)
        return fallback or original_summary
