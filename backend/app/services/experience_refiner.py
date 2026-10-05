"""Per-job Professional Experience bullet refinement.

Each job in a resume can arrive from Stage 1 extraction with anywhere from
a handful to 80+ raw, repetitive bullet points (see extraction_pipeline.py
- it deliberately extracts EVERYTHING, never summarizes, so a detailed
long-tenure role can easily produce 20-80 raw bullets). This module is the
dedicated place that turns that raw list into a small set of professional,
ATS-ready, information-dense bullets per job: merging duplicate/similar
bullets and compressing repeated responsibilities, while preserving every
achievement, metric, technology, scale, and leadership responsibility
mentioned - never inventing one that isn't there. Every output bullet
starts with a strong action verb (see prompts.py's _ACTION_VERB_EXAMPLES),
never a weak one ("Worked on", "Responsible for", "Handled", "Helped",
"Participated").

Reuses prompts.py's build_experience_optimize_prompt rather than a second,
near-duplicate prompt - one prompt, one place its wording is tuned,
shared with anything else that needs per-job bullet consolidation.

Bullet-count target is tiered by role, thinking like a recruiter preparing
a resume for a Fortune 500 client rather than preserving every extracted
bullet (see classify_role_tier/bullet_range_for_role):
  - "standard" (an individual-contributor engineering role, any level from
    junior to senior) -> 5-8 bullets.
  - "lead" (Architect, Technical Lead, Principal/Staff Engineer, Manager)
    -> 8-10 bullets, since these roles genuinely have more distinct,
    resume-worthy responsibilities than an IC role.
  - "executive" (Director, VP, Head of, Chief/C-level, President) -> up to
    12 bullets, ONLY for this tier, where the extra detail is justified by
    the breadth of an executive's remit.
Callers that want a fixed range regardless of role tier can pass
min_bullets/max_bullets explicitly.

Never raises: any Gemini failure for a given job degrades to that job's
own deduped-but-unrefined bullets, so a failure here never blocks
generating the rest of the resume - refine_experience()'s caller always
gets back a complete experience list, one entry per input job.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from app.services.gemini_client import GeminiError, call_gemini
from app.services.prompts import build_experience_optimize_prompt

logger = logging.getLogger(__name__)

# A job with fewer raw bullets than this has nothing meaningful left to
# merge - skip the Gemini call entirely rather than "refining" 1-3 bullets
# down to 1-3 bullets.
_MIN_POINTS_TO_REFINE = 4

# Overall band every job's bullet count is scoped to, regardless of role
# tier - see _BULLET_RANGE for how a job's target is chosen within it.
_TARGET_MIN_BULLETS = 5
_TARGET_MAX_BULLETS = 12

_BULLET_RANGE: dict[str, tuple[int, int]] = {
    "standard": (5, 8),
    "lead": (8, 10),
    "executive": (10, 12),
}

# Checked in this order (most specific/highest first) - "manager" alone
# lands in "lead" rather than "executive" (an Engineering Manager is a
# leadership role above an IC engineer, but below Director/VP/C-level),
# while "architect"/"principal"/"staff"/"tech lead" are the named
# individual-contributor-adjacent roles the request calls out explicitly.
_EXECUTIVE_ROLE_KEYWORDS = (
    "director", "head of", "vp", "vice president", "chief", "president",
    "cto", "cio", "ceo", "coo", "evp", "svp", "executive",
)
_LEAD_ROLE_KEYWORDS = (
    "architect", "principal", "staff", "tech lead", "technical lead",
    "lead engineer", "engineering lead", "team lead", "manager",
)


def classify_role_tier(role: str | None) -> str:
    """Classifies a job title into "standard"/"lead"/"executive" from
    keywords in the role name alone - a small, pure, deterministic function
    on purpose (no Gemini call), so a bullet-count target is fast, free,
    and doesn't depend on the same model call that might itself be
    degrading. Defaults to "standard" when nothing matches - a "normal
    engineering role" (junior through senior individual contributor) is
    the common case."""
    lowered = (role or "").lower()
    if any(keyword in lowered for keyword in _EXECUTIVE_ROLE_KEYWORDS):
        return "executive"
    if any(keyword in lowered for keyword in _LEAD_ROLE_KEYWORDS):
        return "lead"
    return "standard"


def bullet_range_for_role(role: str | None) -> tuple[int, int]:
    """(min_bullets, max_bullets) target for a job, from its role title -
    always within the overall 5-12 band (_TARGET_MIN_BULLETS/_MAX)."""
    return _BULLET_RANGE[classify_role_tier(role)]


def _normalize_for_dedup(text: str) -> str:
    """Whitespace/case-insensitive normalization used ONLY to detect exact-
    ish duplicates - never used to merge bullets that are actually
    different, which is why this is normalize-then-exact-match, not a
    fuzzy/similarity comparison."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Removes exact (post-normalization) duplicates, keeping the first
    occurrence's original text and original order. A pure function with no
    Gemini/IO dependency - directly unit-testable."""
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = _normalize_for_dedup(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _coerce_str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in (value or []) if item is not None and str(item).strip()]


def refine_experience_bullets(
    company: str,
    role: str,
    points: list[str],
    tone: str = "Professional",
    min_bullets: int | None = None,
    max_bullets: int | None = None,
) -> list[str]:
    """Refines one job's raw bullet list (typically 20-80 raw bullets, but
    works for any count) into a small set of professional, ATS-ready
    bullets - merges duplicates/near-duplicates, compresses repeated
    responsibilities, preserves every achievement/metric/technology/
    leadership responsibility mentioned, every bullet starts with a strong
    action verb. `min_bullets`/`max_bullets` default to the role-tier-based
    range for `role` (always within 5-12) if not given explicitly.

    Falls back to the original (deduped) bullets on any Gemini failure -
    never raises, never invents content not already present in `points`.
    """
    cleaned_points = _dedupe_preserve_order(_coerce_str_list(points))
    if len(cleaned_points) < _MIN_POINTS_TO_REFINE:
        return cleaned_points

    lo, hi = (min_bullets, max_bullets) if min_bullets and max_bullets else bullet_range_for_role(role)
    try:
        result = call_gemini(build_experience_optimize_prompt(company, role, cleaned_points, lo, hi, tone=tone))
        refined = _dedupe_preserve_order(_coerce_str_list(result.get("points")))
        if not refined:
            raise ValueError("Gemini returned no usable refined bullets")
        return refined
    except (GeminiError, ValueError, TypeError) as exc:
        logger.warning(
            "Experience refinement failed for %r (%s) - keeping %d original (deduped) bullet(s) "
            "for this job.", company or "<unnamed>", exc, len(cleaned_points),
        )
        return cleaned_points


def refine_one_job(exp: dict, tone: str = "Professional") -> dict:
    """Returns a copy of `exp` with only "points" replaced by the refined
    bullets - every other field (company/role/duration/reason_for_leaving/
    notes/is_career_break/break_detail) is copied through untouched, never
    sent to Gemini, which makes "never remove a job" a structural
    guarantee. A career-break entry is left as-is - there's no
    responsibility list to refine for one."""
    if exp.get("is_career_break"):
        return dict(exp)
    refined = dict(exp)
    refined["points"] = refine_experience_bullets(
        exp.get("company", ""), exp.get("role", ""), exp.get("points") or [], tone
    )
    return refined


def refine_experience(experience: list[dict], tone: str = "Professional") -> list[dict]:
    """Refines every job in `experience` concurrently (mirrors Stage 1's
    own concurrent-calls pattern - one job's refinement never waits on
    another's). Returns a new list, one entry per input job, in the same
    order. See refine_one_job for the per-job guarantees."""
    if not experience:
        return []
    with ThreadPoolExecutor(max_workers=min(len(experience), 8)) as executor:
        return list(executor.map(lambda exp: refine_one_job(exp, tone), experience))
