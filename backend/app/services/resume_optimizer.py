"""Resume Intelligence Engine.

New pipeline shape:

    Resume -> Extraction -> Normalization -> Resume Intelligence Engine
           -> Resume Builder -> DOCX -> PDF

This module is the "Resume Intelligence Engine" step: it receives Stage 1's
full-fidelity JSON (extraction_pipeline.extract_resume(), then
normalization.normalize_parsed()/enforce_limits() - all UNCHANGED by this
module) and returns an OPTIMIZED JSON, ready for the Resume Builder
(file_generator.py) to render into DOCX/PDF. Nothing upstream of this
module (auth, upload, extraction, the Gemini API wrapper, the database,
storage, deployment) is touched by it.

Domain-agnostic by design: nothing in here or in the prompts it uses
(prompts.py's build_*_optimize_prompt functions) hardcodes categories or
wording specific to any one profession. The same code path optimizes a
Software Engineer's, a Project Manager's, or a Content Writer's resume -
Gemini derives categories/phrasing that fit whatever the actual content is.

Architecture (mirrors the lesson learned from Stage 1 - extraction_pipeline.
py: small, focused, bounded calls, not one big one):

  1. Skills + tools categorization (_optimize_skills_and_tools) - one call,
     producing a CANDIDATE pool only. The final Technical Skills content is
     then decided entirely deterministically by the Skill Intelligence
     Engine (app/services/skill_intelligence.py, Phase 2) - see the
     comment at its call site in optimize_resume() below for why.
  2. Experience bullet consolidation - delegated entirely to
     experience_refiner.refine_experience(), one call PER JOB run
     concurrently. Gemini only ever sees/returns that job's bullet list -
     company/role/duration/reason_for_leaving/notes/career-break fields are
     carried over in Python, never sent to Gemini, which makes "never
     remove a job" a structural guarantee.
  3. Projects consolidation (_optimize_projects) - one call for all
     projects together (typically far less raw content than experience).
  4. Achievements (_derive_achievements) - one call, surfaces only
     ALREADY-STATED accomplishments; never invents one, returns [] if none.
  5. Summary - delegated entirely to summary_generator.generate_summary(),
     one small call, written from the candidate's actual work history and
     already-categorized skills, never copied from Stage 1's own summary.

Education/Certifications are NEVER sent through Gemini - the safest way to
guarantee fields required to always be fully preserved actually are is to
never put them through a rewrite step. They still get a conservative,
exact-match (not fuzzy/semantic) dedup pass in Python - see
_dedupe_preserve_order - since merging two DIFFERENT degrees/certifications
because they look similar would itself be an information-loss bug.

Every piece degrades independently on failure (see each _optimize_*/
_derive_* helper) - optimize_resume() never raises; a Gemini failure
anywhere in this module always means "that one piece kept its Stage 1
form", never "the whole resume fails".

_validate_and_dedupe applies a deterministic, unit-testable validation/dedup
pass (Step 10 of the redesign) across the whole result before it's
returned - exact-duplicate removal on every list field, so a consolidation
call that (despite instructions) leaves a near-identical bullet twice
doesn't reach the rendered resume.

Finally, _quality_audit runs as a Phase-1 quality-check pass: it hard-caps
any job/project that still overflows its target bullet count (a safe,
deterministic trim - never invents or reorders content) and LOGS (never
silently rewrites) anything requiring semantic judgment - weak opening
verbs, AI-sounding stock phrases, a too-short/generic summary, or bullets
within one job sharing the same opening wording. Rewriting wording
correctly needs another model call; silently mangling text deterministically
instead would risk exactly the hallucination/information-loss this whole
module is designed to avoid, so those findings are surfaced, not "fixed".
"""
import logging
import re
from typing import TypeVar

from app.services.experience_intelligence import (
    enhance_experience,
    enhance_projects,
    filter_non_genuine_career_breaks,
    page_budget_for_years,
    remove_experience_project_overlap,
    vary_repeated_generic_phrases,
    years_of_experience,
)
from app.services.experience_refiner import refine_experience
from app.services.gemini_client import GeminiError, call_gemini
from app.services.industry_intelligence import classify_industry, get_industry_terminology
from app.services.prompts import (
    build_achievements_prompt,
    build_projects_optimize_prompt,
    build_skills_tools_optimize_prompt,
)
from app.services.skill_categorizer import categorize_skills
from app.services.skill_intelligence import build_technical_skills, build_technical_skills_grouped
from app.services.summary_generator import generate_summary
from app.services.tool_classifier import cap_tools, classify_tools_and_technologies

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The optimizer never touches contact info - these four fields are carried
# over from Stage 1 unchanged, not requested from or returned by any call
# this module makes.
_CONTACT_FIELDS = ("name", "email", "phone", "linkedin")


def _normalize_for_dedup(text: str) -> str:
    """Whitespace/case-insensitive normalization used ONLY to detect exact-
    ish duplicates (e.g. "AWS Certified" vs "aws   certified") - never used
    to merge two items that are actually different, which is why this is a
    normalize-then-exact-match comparison, not a fuzzy/similarity one:
    fuzzy-merging two genuinely different degrees/certifications because
    they look similar would itself be an information-loss bug."""
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


def _coerce_skills(raw_skills) -> dict[str, list[str]]:
    """Defensive coercion, same spirit as normalization.py's
    normalize_parsed(): Gemini occasionally returns a shape slightly off
    from what was asked - this normalizes those instead of letting a
    slightly-off response crash rendering (file_generator.py's
    add_skills_section expects either a dict[str, list[str]] or a flat
    list)."""
    if not isinstance(raw_skills, dict):
        cleaned = [str(item).strip() for item in (raw_skills or []) if str(item).strip()]
        return {"Skills": cleaned} if cleaned else {}

    categorized: dict[str, list[str]] = {}
    for category, items in raw_skills.items():
        if isinstance(items, str):
            items = [items]
        cleaned = [str(item).strip() for item in (items or []) if str(item).strip()]
        if cleaned:
            categorized[str(category).strip()] = cleaned
    return categorized


def _coerce_str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in (value or []) if item is not None and str(item).strip()]


def _optimize_skills_and_tools(
    skills: list[str], tools: list[str]
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Returns (categorized_skills_for_context, candidate_skill_pool,
    filtered_tools).

    `categorized_skills_for_context` is used ONLY as context for
    summary_generator.py's prompt ("skills, by category") - it is no
    longer the final Technical Skills content (see optimize_resume below,
    Phase 2's Skill Intelligence Engine now owns that decision entirely).

    `candidate_skill_pool` is a flat, UNFILTERED-by-category-matching list
    of every candidate skill string this step could find - deliberately
    not pre-filtered through skill_categorizer.py's own (narrower) keyword
    dictionary on the fallback path, so the Skill Intelligence Engine's own
    richer normalization/alias tables get a chance to recognize a raw form
    skill_categorizer.py itself wouldn't (e.g. "C Sharp" - not in
    skill_categorizer.py's keyword list, so its own categorize_skills()
    would silently drop it a step too early; skill_intelligence.py's
    normalization dictionary knows it as "C#"). On any Gemini failure, the
    context dict still comes from skill_categorizer.py's deterministic
    keyword-based categorization (still genuinely useful for the summary
    prompt), but the candidate pool bypasses that filter entirely.
    """
    if not skills and not tools:
        return {}, [], []
    try:
        result = call_gemini(build_skills_tools_optimize_prompt(skills, tools))
        categorized = _coerce_skills(result.get("skills"))
        filtered_tools = _coerce_str_list(result.get("tools"))
        if not categorized and skills:
            raise ValueError("Gemini returned no usable skills categorization")
        candidate_pool = [item for items in categorized.values() for item in items]
        return categorized, candidate_pool, filtered_tools
    except (GeminiError, ValueError, TypeError) as exc:
        logger.warning(
            "Skills/tools optimization failed (%s) - falling back to keyword-based "
            "classification (tool_classifier.py + skill_categorizer.py) instead of "
            "Gemini's domain-adaptive one.", exc,
        )
        # Stage 1's raw "tools" list often mixes in technologies/platforms
        # (e.g. "Azure", "Kubernetes") that don't belong in a Tools section -
        # re-triage it so those move into skills instead of staying
        # miscategorized just because the primary Gemini call failed.
        real_tools, misclassified_technologies = classify_tools_and_technologies(_coerce_str_list(tools))
        fallback_skills = _coerce_str_list(skills) + misclassified_technologies
        categorized_for_context = categorize_skills(fallback_skills)
        return categorized_for_context, fallback_skills, real_tools


def _optimize_projects(projects: list[dict], tone: str) -> list[dict]:
    """Returns the consolidated projects list. On any Gemini failure,
    degrades to Stage 1's own project list unchanged."""
    if not projects:
        return []
    try:
        result = call_gemini(build_projects_optimize_prompt(projects, tone=tone))
        optimized = result.get("projects")
        if not isinstance(optimized, list) or not optimized:
            raise ValueError("Gemini returned no usable projects")
        cleaned: list[dict] = []
        for proj in optimized:
            if not isinstance(proj, dict):
                continue
            cleaned.append(
                {
                    "title": str(proj.get("title") or "").strip(),
                    "role": str(proj.get("role") or "").strip(),
                    "description": str(proj.get("description") or "").strip(),
                    "technologies": str(proj.get("technologies") or "").strip(),
                    "responsibilities": _dedupe_preserve_order(_coerce_str_list(proj.get("responsibilities"))),
                }
            )
        if not cleaned:
            raise ValueError("Gemini's projects response had no usable entries")
        return cleaned
    except (GeminiError, ValueError, TypeError) as exc:
        logger.warning("Projects optimization failed (%s) - keeping Stage 1's unconsolidated projects.", exc)
        return list(projects)


def _derive_achievements(
    summary: str, experience: list[dict], projects: list[dict], stage1_achievements: list[str] | None = None
) -> list[str]:
    """Returns already-stated achievements, or [] - both on genuine "nothing
    found" and on any Gemini failure. Never invents an achievement; an empty
    list is a normal, expected result, not a degraded one.

    Two sources, combined: (1) `stage1_achievements` - Stage 1's own
    explicit extraction (extraction_pipeline.py's 6th call,
    build_extras_prompt) of an Awards/Achievements/Recognition section the
    source document actually had, taken as-is (already extracted, not
    re-derived); (2) this function's own Gemini call, which infers a
    achievement from summary/experience/project text even when the source
    had no dedicated Awards section at all (e.g. a metric or promotion
    mentioned inline in a bullet point). Combining both is what fixes a
    real defect: previously only (2) ran, so a document with an explicit
    Awards section that Stage 1 was told to ignore (see prompts.py) never
    reached either step."""
    stage1_achievements = _coerce_str_list(stage1_achievements)
    experience_points = [p for exp in experience for p in (exp.get("points") or [])]
    project_descriptions = [
        text for proj in projects for text in ([proj.get("description")] + (proj.get("responsibilities") or []))
        if text
    ]
    derived: list[str] = []
    if summary or experience_points or project_descriptions:
        try:
            result = call_gemini(build_achievements_prompt(summary, experience_points, project_descriptions))
            derived = _coerce_str_list(result.get("achievements"))
        except (GeminiError, ValueError, TypeError) as exc:
            logger.warning(
                "Achievement derivation failed (%s) - continuing with Stage 1's explicitly-extracted "
                "achievements only (never invented).", exc,
            )
    return _dedupe_preserve_order(stage1_achievements + derived)


# --- Quality audit (Phase 1's "quality check" step) -------------------------
#
# Runs last, after _validate_and_dedupe, as a deterministic, non-LLM safety
# net over the fully-assembled result. Two kinds of finding, handled
# differently on purpose:
#   1. Structural overflow (too many bullets in a job/project) - safe to
#      auto-fix deterministically: trim to the target ceiling, keeping the
#      first N in order. Never invents or reorders content, so this can
#      never introduce a factual error - purely a length cap.
#   2. Wording-quality issues (a weak opening verb, an AI-sounding stock
#      phrase, a generic summary, repeated sentence-opening patterns within
#      one job) - detected and LOGGED, never silently rewritten. Rewriting
#      wording correctly requires judgment (another Gemini call), and this
#      module's one hard rule all session has been "never invent/never
#      hallucinate" - a deterministic text-mangling "fix" risks corrupting
#      real, factual content, which is a worse outcome than surfacing the
#      finding as a warning for a human (or a future phase) to act on.
_MAX_EXPERIENCE_BULLETS_HARD_CAP = 12  # mirrors experience_refiner.py's overall ceiling
_MAX_PROJECT_BULLETS = 5

_WEAK_VERB_STARTS = (
    "worked", "responsible for", "handled", "helped", "participated", "was involved",
    "involved in", "assisted", "tasked with",
)
_AI_SOUNDING_PHRASES = (
    "dynamic self-starter", "results-driven synergy", "passionate about",
    "detail-oriented team player", "proven track record of excellence",
    "leverage synergies", "think outside the box", "go-getter attitude",
    "hit the ground running", "wear many hats",
)


def _first_words(text: str, count: int) -> str:
    return " ".join(text.strip().split()[:count]).lower()


def _quality_audit(result: dict) -> dict:
    """Deterministic Phase-1 quality check, run once over the fully-
    assembled, already-deduped result. See module comment above for what
    gets auto-fixed vs. only logged."""
    for exp in result["experience"]:
        points = exp.get("points") or []
        if len(points) > _MAX_EXPERIENCE_BULLETS_HARD_CAP:
            logger.warning(
                "Quality audit: %r had %d bullets after refinement (over the %d-bullet ceiling) - "
                "trimming to the first %d.", exp.get("company") or "(no company)", len(points),
                _MAX_EXPERIENCE_BULLETS_HARD_CAP, _MAX_EXPERIENCE_BULLETS_HARD_CAP,
            )
            exp["points"] = points[:_MAX_EXPERIENCE_BULLETS_HARD_CAP]
            points = exp["points"]

        weak = [p for p in points if p.strip().lower().startswith(_WEAK_VERB_STARTS)]
        if weak:
            logger.warning(
                "Quality audit: %r has %d bullet(s) opening with a weak verb: %r",
                exp.get("company") or "(no company)", len(weak), weak,
            )

        openings = [_first_words(p, 3) for p in points if p.strip()]
        repeated_openings = {o for o in openings if o and openings.count(o) > 1}
        if repeated_openings:
            logger.warning(
                "Quality audit: %r has bullets sharing the same opening wording: %r - "
                "consider varying sentence structure across bullets.",
                exp.get("company") or "(no company)", repeated_openings,
            )

    for proj in result["projects"]:
        responsibilities = proj.get("responsibilities") or []
        if len(responsibilities) > _MAX_PROJECT_BULLETS:
            logger.warning(
                "Quality audit: project %r had %d bullets (over the %d-bullet ceiling) - "
                "trimming to the first %d.", proj.get("title") or "(untitled)", len(responsibilities),
                _MAX_PROJECT_BULLETS, _MAX_PROJECT_BULLETS,
            )
            proj["responsibilities"] = responsibilities[:_MAX_PROJECT_BULLETS]

    summary = str(result.get("summary") or "")
    summary_lower = summary.lower()
    found_ai_phrases = [phrase for phrase in _AI_SOUNDING_PHRASES if phrase in summary_lower]
    if found_ai_phrases:
        logger.warning("Quality audit: summary contains AI-sounding stock phrase(s): %r", found_ai_phrases)
    if summary and len(summary.split()) < 15:
        logger.warning("Quality audit: summary looks too short/generic (%d words).", len(summary.split()))

    return result


def _validate_and_dedupe(result: dict) -> dict:
    """Redesign Step 10 - a deterministic, unit-testable validation/dedup
    pass applied to the fully-assembled result before it's returned. Only
    ever REMOVES an exact-duplicate entry or logs a warning about an empty
    section - never invents content, and never fuzzy-merges two entries
    that aren't exact (post-normalization) duplicates of each other.

    `result["skills"]` is already the final, flat, capped Technical Skills
    list by the time this runs (see optimize_resume - finalize_technical_
    skills already deduped it once), so this is a cheap, harmless second
    pass, not the primary place skills get deduped."""
    result["skills"] = _dedupe_preserve_order(result["skills"])
    result["tools"] = _dedupe_preserve_order(result["tools"])
    result["education"] = _dedupe_preserve_order(result["education"])
    result["certifications"] = _dedupe_preserve_order(result["certifications"])
    result["achievements"] = _dedupe_preserve_order(result["achievements"])
    result["languages"] = _dedupe_preserve_order(result["languages"])
    result["publications"] = _dedupe_preserve_order(result["publications"])
    result["volunteer_experience"] = _dedupe_preserve_order(result["volunteer_experience"])
    result["leadership"] = _dedupe_preserve_order(result["leadership"])

    for exp in result["experience"]:
        exp["points"] = _dedupe_preserve_order(exp.get("points") or [])

    seen_project_titles: set[str] = set()
    deduped_projects = []
    for proj in result["projects"]:
        key = _normalize_for_dedup(proj.get("title") or "")
        if key and key in seen_project_titles:
            continue
        if key:
            seen_project_titles.add(key)
        deduped_projects.append(proj)
    result["projects"] = deduped_projects

    for field in ("skills", "tools", "experience", "education", "certifications", "projects"):
        if not result[field]:
            logger.warning(
                "Optimized resume has no content in %r - not fabricating a placeholder; this "
                "section will simply be omitted from the rendered resume.", field,
            )

    return result


def optimize_resume(structured: dict, tone: str = "Professional") -> dict:
    """Produces the recruiter-ready version of Stage 1's `structured` JSON -
    the "Resume Intelligence Engine" step of the pipeline. Never raises:
    every Gemini call this makes degrades independently to its Stage 1
    equivalent on failure (see the per-piece helpers above), so callers
    always get a complete, renderable result back, even in the worst case
    where every single call fails (which just reproduces Stage 1's own
    output for every field).
    """
    result = {field: structured.get(field, "") for field in _CONTACT_FIELDS}

    # Industry-specific templates: classify the candidate's industry EARLY,
    # from Stage 1's raw `structured` data, and reuse it as a content-
    # RANKING signal only (Skill Prioritization below, Project Selection
    # further down) - never to invent or alter a fact. This is a separate,
    # earlier classification than Phase 7's own (industry_intelligence.
    # apply_industry_intelligence, run later in resume_service.py on the
    # fully-optimized resume) - the two can occasionally disagree if
    # skills/bullets change enough in between, which is fine: Phase 7's
    # classification runs on the FINAL content and is what actually drives
    # the rendered summary/wording, so it stays authoritative for anything
    # user-visible; this early one only nudges ranking/ordering, and
    # resolves to a complete no-op (see build_technical_skills' own
    # `industry_keywords` docstring) for any skill that doesn't happen to
    # match the classified industry's own keywords.
    industry = classify_industry(structured)
    industry_keywords = get_industry_terminology(industry)["priority_keywords"]

    categorized_skills, candidate_skills, filtered_tools = _optimize_skills_and_tools(
        structured.get("skills") or [], structured.get("tools") or []
    )
    # Phase 2 - Skill Intelligence Engine: `candidate_skills` (the raw,
    # unfiltered-by-category pool _optimize_skills_and_tools gathered,
    # whether Gemini or the keyword fallback produced it) is run through
    # the deterministic Skill Intelligence Engine
    # (skill_intelligence.build_technical_skills), which is now the single
    # final authority for Technical Skills content: cleaning, normalization,
    # alias resolution, duplicate detection, noise removal, classification,
    # ranking, and the size cap all happen there, "regardless of source" -
    # the same deterministic result for the same candidates whether Gemini
    # or the fallback produced them. This is what "reduces reliance on the
    # LLM" - Gemini's occasionally-inconsistent naming/dedup/ranking no
    # longer directly determines the rendered output. Passing a flat list
    # (not a dict) for "skills" is what makes file_generator.py's existing
    # add_skills_section render a single un-subsectioned section; no
    # rendering code changes needed. `categorized_skills` (the dict, by
    # category) is still used below for the summary prompt's context only.
    # `relevance_context` (Skill Prioritization) lets the ranking stage
    # order same-category skills by how strongly THIS candidate's own
    # summary/experience/projects evidence each one (frequency, job-title
    # mentions, recency) instead of plain first-seen order - see
    # skill_intelligence.py's own docstring. Uses Stage 1's raw `structured`
    # (the only text available at this point in the pipeline) - still
    # genuine evidence from the candidate's own resume, never external data.
    result["skills"] = build_technical_skills(
        candidate_skills, relevance_context=structured, industry_keywords=industry_keywords
    )
    # Additive, backward-compatible field: the SAME final skill set as
    # `result["skills"]` above, grouped by category (Programming Languages,
    # Frameworks, Cloud, ..., "Other Skills" catch-all) for a renderer that
    # wants actual category subheadings (see file_generator.py's
    # _section_items, which prefers this over the flat list when present).
    # Nothing that reads `result["skills"]` as a flat list needs to change -
    # this is a new key, not a shape change to an existing one.
    result["skills_grouped"] = build_technical_skills_grouped(
        candidate_skills, relevance_context=structured, industry_keywords=industry_keywords
    )
    result["tools"] = cap_tools(filtered_tools)

    # Phase 3 - Experience Intelligence Engine: refine_experience()/
    # _optimize_projects() each still run their own per-entry Gemini call
    # exactly as before (unchanged) - enhance_experience()/enhance_projects()
    # (app/services/experience_intelligence.py) then run as a deterministic
    # pass ACROSS the whole assembled list, which is the one thing no
    # single per-job/per-project Gemini call can do (see that module's
    # docstring for why): classify each entry's dominant theme, diversify a
    # repeated opening verb, merge any residual near-duplicate bullets,
    # surface bullets that already state a metric/outcome, enforce the
    # established bullet-count targets, and log (never silently rewrite)
    # anything that needs semantic judgment. Achievement derivation below
    # runs on the ENHANCED bullets, so it reflects exactly what ends up in
    # the rendered resume.
    refined_experience = refine_experience(structured.get("experience") or [], tone)
    optimized_projects = _optimize_projects(structured.get("projects") or [], tone)

    # Experience/Projects overlap removal + Career Break genuine-evidence
    # gate both run BEFORE enhance_experience(), not after - deliberately:
    # enhance_job_experience's recency-weighted bullet cap (STEP 1) indexes
    # jobs by their POSITION in the list, so a raw list still full of
    # phantom duplicate/non-genuine-break entries would compress the
    # candidate's REAL jobs based on the wrong position (e.g. a candidate's
    # 3rd real job compressed as if it were the 6th, because 3 phantom rows
    # sat ahead of it). Cleaning the list first means every downstream
    # stage - recency compression, cross-company dedup, achievement
    # derivation - operates on the same, final job list the reader sees.
    refined_experience, overlap_removed = remove_experience_project_overlap(refined_experience, optimized_projects)
    if overlap_removed:
        logger.info(
            "Resume Intelligence Engine: removed %d Experience entr%s duplicating a Projects Handled "
            "entry already covering the same engagement.", overlap_removed,
            "y" if overlap_removed == 1 else "ies",
        )
    refined_experience, breaks_removed = filter_non_genuine_career_breaks(refined_experience)
    if breaks_removed:
        logger.info(
            "Resume Intelligence Engine: removed %d non-genuine Career Break entr%s (no stated reason, "
            "gap under the genuine-break threshold).", breaks_removed, "y" if breaks_removed == 1 else "ies",
        )

    # Resume Length / Project Selection now both scale to the SAME years-
    # of-experience page-budget bracket file_generator.py's page cap uses
    # (under 5y->2 pages, 5-10y->3, 10-15y->4, 15+y->5) - computed ONCE
    # here, from the already-cleaned `refined_experience` (real jobs only,
    # phantom/non-genuine-break entries already removed above), so bullet
    # compression and project count agree with each other and with what
    # the candidate will actually see rendered.
    candidate_years = years_of_experience(refined_experience)
    page_budget = page_budget_for_years(candidate_years)

    result["experience"] = enhance_experience(refined_experience, max_pages=page_budget)
    # Projects Handled is never count-capped: CONFIRMED real-world
    # regression (a real 8.7-year candidate with 8 genuinely distinct,
    # different-client projects had 4 of them silently dropped by a
    # years-scaled rank_and_select_projects() cap) - this directly
    # violated the "100% Project Preservation" requirement and the
    # Evaluation Dashboard's own projects_preservation_pct threshold.
    # Projects now get the SAME guarantee Experience already has (every
    # job/employer is always included - see prompts.py rule 7): length is
    # controlled by compressing each project's own bullet count
    # (enhance_project's fixed 3-5 range), never by dropping whole
    # projects. rank_and_select_projects/max_projects_for_years remain
    # available and unit-tested (test_experience_intelligence.py) for any
    # future caller that still needs a hard count ceiling - just no
    # longer wired into this pipeline's main path.
    result["projects"] = enhance_projects(optimized_projects)

    # Experience Deduplication (cross-entry narrative variety): enhance_
    # experience()/enhance_projects() each already vary a REPEATED OPENING
    # VERB within their own section - this additionally varies a small set
    # of generic, commonly copy-pasted RESPONSIBILITY PHRASES ("Requirements
    # analysis", "Technical guidance", ...) that show up verbatim across
    # DIFFERENT jobs/projects, so a candidate's later entries don't read as
    # a copy-paste of an earlier one. Combining both lists into one call is
    # what makes this also catch a project literally repeating an
    # Experience bullet's phrasing (Project Improvement's "avoid copying
    # the same bullets [as Experience]") - see
    # vary_repeated_generic_phrases's own docstring for why this is always
    # a synonym substitution, never a fact/technology/metric change.
    vary_repeated_generic_phrases(result["experience"] + result["projects"])

    result["achievements"] = _derive_achievements(
        str(structured.get("summary") or ""), result["experience"], result["projects"],
        stage1_achievements=structured.get("achievements"),
    )
    result["summary"] = generate_summary(structured, categorized_skills, tone)

    # Never sent through Gemini at all - see module docstring. Still gets
    # the same conservative exact-match dedup as everything else, applied
    # below in _validate_and_dedupe.
    result["education"] = _coerce_str_list(structured.get("education"))
    result["certifications"] = _coerce_str_list(structured.get("certifications"))
    # Languages/Publications/Volunteer Experience/Leadership - Stage 1's own
    # explicit extraction (extraction_pipeline.py's 6th call), passed
    # through unchanged for the same reason Education/Certifications are:
    # these are short, factual, low-risk-of-improvement-but-high-risk-of-
    # distortion lists, not prose worth an LLM rewrite pass.
    result["languages"] = _coerce_str_list(structured.get("languages"))
    result["publications"] = _coerce_str_list(structured.get("publications"))
    result["volunteer_experience"] = _coerce_str_list(structured.get("volunteer_experience"))
    result["leadership"] = _coerce_str_list(structured.get("leadership"))

    result = _validate_and_dedupe(result)
    return _quality_audit(result)
