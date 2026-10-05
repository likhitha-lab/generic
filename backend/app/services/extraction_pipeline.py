"""Redesigned resume-extraction pipeline for the upload/convert flow.

Replaces the old single monolithic Gemini call (one prompt asking for the
entire standardized resume at once) with 5 small, focused calls run
concurrently, then merged into the same standardized schema. This is what
actually fixes "502 - Gemini's response was truncated" on long resumes:

  - 4 of the 5 calls (contact/summary/skills-section/projects) have
    inherently small, bounded output regardless of resume length, so they
    essentially never truncate.
  - The one call whose output genuinely scales with resume length
    (experience - one entry per job, worse with more companies) is the only
    one that can still hit the token limit on a very long resume. When it
    does, `_extract_section_with_retry` below catches that specific failure
    and retries with the SOURCE TEXT split into two smaller, overlapping
    halves instead of failing the request - recursing (bounded by
    `_MAX_SPLIT_DEPTH`) until it fits, then merging the partial results.

The manual Resume Generator flow (`create_manual_resume`, ResumeRequest-based)
is untouched - this module only replaces what used to be `build_convert_prompt`
+ the single `call_gemini` call in the upload/regenerate-from-upload path.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from app.core.config import settings
from app.services.gemini_client import GeminiError, GeminiInvalidResponseError, GeminiTruncatedError, call_gemini
from app.services.normalization import clean_name, guess_name_from_text, is_plausible_name
from app.services.prompts import (
    build_contact_prompt,
    build_experience_prompt,
    build_extras_prompt,
    build_projects_prompt,
    build_skills_section_prompt,
    build_summary_prompt,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# A resume section this short is never going to usefully split further - if
# a chunk this small still truncates, something else is wrong (e.g. a
# pathologically dense table), and further splitting just wastes calls.
# Depth 4 (was 3) / floor 200 (was 400): the exhausted-retry path below
# returns an EMPTY section with no error surfaced anywhere - the single
# worst failure mode in this pipeline, since it can produce a "successfully"
# generated resume that's silently missing Experience/Education/
# Certifications. Giving the split more room before giving up reduces how
# often that actually happens for a long, detail-heavy resume.
_MIN_SPLITTABLE_CHARS = 200
_MAX_SPLIT_DEPTH = 4
# Overlap between the two halves of a split, so a job/project/entry that
# happens to sit right at the midpoint isn't silently cut in half and lost
# from both halves - the dedup step downstream absorbs the resulting
# duplicate mention from whichever half DOES capture it whole.
_SPLIT_OVERLAP_RATIO = 0.1

_EMPTY_CONTACT = {
    "name": "", "email": "", "phone": "", "linkedin": "", "github": "", "portfolio": "",
    "open_to_relocate": None, "open_to_remote": None,
}
_EMPTY_SKILLS_SECTION = {"skills": [], "education": [], "certifications": [], "tools": []}
_EMPTY_EXTRAS = {
    "achievements": [], "languages": [], "publications": [], "volunteer_experience": [], "leadership": [],
}


def _debug_log(stage: str, content: str) -> None:
    if settings.DEBUG_RESUME_PIPELINE:
        logger.info("========== %s ==========\n%s", stage, content)


def _debug_print_section_input(section_name: str, text: str) -> None:
    if not settings.DEBUG_RESUME_PIPELINE:
        return
    print(f"\n========== BEFORE GEMINI CALL: {section_name} ==========")
    print(f"len(text) = {len(text)}")
    print("--- first 500 chars ---")
    print(text[:500])
    print("--- last 500 chars ---")
    print(text[-500:])


def _debug_print_section_response(section_name: str, result: dict) -> None:
    if not settings.DEBUG_RESUME_PIPELINE:
        return
    print(f"\n========== GEMINI RESPONSE: {section_name} ==========")
    print(json.dumps(result, indent=2))


def _split_text(raw_text: str) -> tuple[str, str]:
    n = len(raw_text)
    mid = n // 2
    overlap = int(n * _SPLIT_OVERLAP_RATIO)
    first_half = raw_text[: mid + overlap]
    second_half = raw_text[max(0, mid - overlap):]
    return first_half, second_half


def _extract_section_with_retry(
    section_name: str,
    call_fn: Callable[[str], T],
    raw_text: str,
    merge_fn: Callable[[T, T], T],
    empty_value: T,
    depth: int = 0,
) -> T:
    """`call_fn(text)` builds the prompt AND calls Gemini for a slice of
    `text`, returning the already-parsed section. Recurses on
    GeminiTruncatedError OR GeminiInvalidResponseError by splitting `text`
    in half and retrying each half independently - malformed JSON (Gemini's
    own retry-with-correction in gemini_client.py already failed by the
    time this exception reaches here) is, like truncation, often really
    "this chunk asked Gemini to produce too much in one response"; asking
    for less by halving the source text is a reasonable recovery for both,
    not just truncation specifically. Any other GeminiError (unavailable/
    timeout) propagates - retrying with a smaller prompt wouldn't fix
    those, so the caller (extract_resume) surfaces them as a real error
    instead of silently producing an incomplete resume."""
    try:
        return call_fn(raw_text)
    except (GeminiTruncatedError, GeminiInvalidResponseError) as exc:
        if depth >= _MAX_SPLIT_DEPTH or len(raw_text) < _MIN_SPLITTABLE_CHARS:
            # .error, not .warning: this is the one place a whole section can
            # vanish from a "successfully" generated resume with no error
            # ever reaching the user - worth standing out in production logs
            # rather than blending in with routine retry-in-progress warnings.
            logger.error(
                "%s: still failing (%s) after %d split(s) on a %d-char chunk - giving up and "
                "returning EMPTY for this chunk. This section may now be incomplete in the "
                "generated resume with no error shown to the user - if this recurs, raise "
                "GEMINI_MAX_OUTPUT_TOKENS or investigate why this section's output is so large.",
                section_name, type(exc).__name__, depth, len(raw_text),
            )
            return empty_value
        logger.warning(
            "%s: Gemini call failed (%s) - splitting into 2 smaller chunks "
            "(depth %d -> %d) and retrying instead of failing the request.",
            section_name, type(exc).__name__, depth, depth + 1,
        )
        first_half, second_half = _split_text(raw_text)
        result_a = _extract_section_with_retry(section_name, call_fn, first_half, merge_fn, empty_value, depth + 1)
        result_b = _extract_section_with_retry(section_name, call_fn, second_half, merge_fn, empty_value, depth + 1)
        return merge_fn(result_a, result_b)


def _merge_contact(a: dict, b: dict) -> dict:
    """Contact fields are single values, not lists - "merge" means "prefer
    whichever half actually found a value" per field, not concatenation."""
    return {
        "name": a.get("name") or b.get("name") or "",
        "email": a.get("email") or b.get("email") or "",
        "phone": a.get("phone") or b.get("phone") or "",
        "linkedin": a.get("linkedin") or b.get("linkedin") or "",
        "github": a.get("github") or b.get("github") or "",
        "portfolio": a.get("portfolio") or b.get("portfolio") or "",
        "open_to_relocate": a.get("open_to_relocate") if a.get("open_to_relocate") is not None else b.get("open_to_relocate"),
        "open_to_remote": a.get("open_to_remote") if a.get("open_to_remote") is not None else b.get("open_to_remote"),
    }


def _merge_summary(a: dict, b: dict) -> dict:
    combined = " ".join(s for s in [a.get("summary"), b.get("summary")] if s).strip()
    return {"summary": combined}


def _merge_list_field_dict(a: dict, b: dict, keys: list[str]) -> dict:
    merged = {}
    for key in keys:
        combined = (a.get(key) or []) + (b.get(key) or [])
        # Plain lists of strings - dedupe exact repeats (Gemini re-stating
        # the same skill in both halves because of the deliberate overlap),
        # preserving first-seen order.
        merged[key] = list(dict.fromkeys(combined))
    return merged


def _merge_experience(a: list, b: list) -> list:
    # No dedup here - normalize_parsed/enforce_limits (called right after
    # this pipeline returns) already dedupes experience entries on
    # (company, role, duration, break_detail, points), which is exactly what
    # absorbs a job that got captured whole by both overlapping halves.
    return list(a) + list(b)


def _merge_projects(a: list, b: list) -> list:
    merged = list(a) + list(b)
    # Projects have no downstream dedup step (unlike experience), so do a
    # light one here keyed on (title, description) - the two overlapping
    # halves are the only source of duplicate projects in this pipeline.
    seen = set()
    deduped = []
    for proj in merged:
        key = (proj.get("title"), proj.get("description")) if isinstance(proj, dict) else (str(proj),)
        if key not in seen:
            seen.add(key)
            deduped.append(proj)
    return deduped


def extract_resume(raw_text: str, tone: str = "Professional") -> dict:
    """Run all 6 section extractions concurrently, retry-and-split any that
    truncate, and merge into the standardized schema:

        {"name", "email", "phone", "linkedin", "github", "portfolio",
         "open_to_relocate", "open_to_remote", "summary", "skills",
         "education", "certifications", "tools", "experience", "projects",
         "achievements", "languages", "publications", "volunteer_experience",
         "leadership"}

    Raises GeminiError (unavailable/timeout/invalid-JSON) if any section
    fails for a reason retrying-smaller can't fix - callers should convert
    that to a clean HTTP error, not let it propagate as a raw exception.
    """

    _debug_log("RAW EXTRACTED TEXT", raw_text[:1000])

    def call_contact(text: str, strict_name_retry: bool = False) -> dict:
        _debug_print_section_input("contact", text)
        prompt = build_contact_prompt(text, strict_name_retry=strict_name_retry)
        _debug_log("CONTACT PROMPT", prompt)
        result = call_gemini(prompt)
        _debug_print_section_response("contact", result)
        return result

    def call_summary(text: str) -> dict:
        _debug_print_section_input("summary", text)
        prompt = build_summary_prompt(text, tone=tone)
        _debug_log("GEMINI PROMPT (summary)", prompt)
        result = call_gemini(prompt)
        _debug_print_section_response("summary", result)
        return result

    def call_skills_section(text: str) -> dict:
        _debug_print_section_input("skills_section", text)
        prompt = build_skills_section_prompt(text)
        _debug_log("GEMINI PROMPT (skills_section)", prompt)
        result = call_gemini(prompt)
        _debug_print_section_response("skills_section", result)
        return result

    def call_experience(text: str) -> list:
        _debug_print_section_input("experience", text)
        prompt = build_experience_prompt(text, tone=tone)
        _debug_log("GEMINI PROMPT (experience)", prompt)
        logger.info("Experience Prompt Size: %d chars", len(prompt))
        result = call_gemini(prompt)
        response_size = len(json.dumps(result))
        logger.info("Experience Response Size: %d chars", response_size)
        _debug_print_section_response("experience", result)
        return result.get("experience", [])

    def call_projects(text: str) -> list:
        _debug_print_section_input("projects", text)
        prompt = build_projects_prompt(text, tone=tone)
        _debug_log("GEMINI PROMPT (projects)", prompt)
        result = call_gemini(prompt)
        _debug_print_section_response("projects", result)
        return result.get("projects", [])

    def call_extras(text: str) -> dict:
        _debug_print_section_input("extras", text)
        prompt = build_extras_prompt(text)
        _debug_log("GEMINI PROMPT (extras)", prompt)
        result = call_gemini(prompt)
        _debug_print_section_response("extras", result)
        return result

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            "contact": executor.submit(
                _extract_section_with_retry, "contact", call_contact, raw_text, _merge_contact, _EMPTY_CONTACT,
            ),
            "summary": executor.submit(
                _extract_section_with_retry, "summary", call_summary, raw_text, _merge_summary, {"summary": ""},
            ),
            "skills_section": executor.submit(
                _extract_section_with_retry, "skills_section", call_skills_section, raw_text,
                lambda a, b: _merge_list_field_dict(a, b, ["skills", "education", "certifications", "tools"]),
                _EMPTY_SKILLS_SECTION,
            ),
            "experience": executor.submit(
                _extract_section_with_retry, "experience", call_experience, raw_text, _merge_experience, [],
            ),
            "projects": executor.submit(
                _extract_section_with_retry, "projects", call_projects, raw_text, _merge_projects, [],
            ),
            "extras": executor.submit(
                _extract_section_with_retry, "extras", call_extras, raw_text,
                lambda a, b: _merge_list_field_dict(
                    a, b, ["achievements", "languages", "publications", "volunteer_experience", "leadership"]
                ),
                _EMPTY_EXTRAS,
            ),
        }
        results: dict = {}
        errors: dict[str, GeminiError] = {}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except GeminiError as exc:
                errors[key] = exc

    if "experience" in errors:
        # Experience is deliberately fault-tolerant in a way the other four
        # sections aren't: it's the one section whose output size scales
        # with resume length (more jobs, more bullets), so it's the most
        # likely to exhaust even the split-retry above on a long, detailed
        # career history. Losing Experience specifically is bad, but it's
        # far better than failing the whole upload and losing Contact/
        # Summary/Skills/Education/Certifications/Tools/Projects too, all of
        # which already succeeded by this point.
        logger.error(
            "Experience extraction failed even after retries (%s) - continuing with an empty "
            "experience list rather than aborting the whole resume. Every other section "
            "(contact/summary/skills/education/certifications/tools/projects) still generates "
            "normally.", errors.pop("experience"),
        )
        results["experience"] = []

    if "extras" in errors:
        # Achievements/Languages/Publications/Volunteer/Leadership are a
        # genuine enhancement, not core resume content the way Experience
        # is - a candidate's resume is still complete without them. Losing
        # this one call must never fail the whole upload.
        logger.warning(
            "Extras (achievements/languages/publications/volunteer/leadership) extraction failed "
            "even after retries (%s) - continuing with all five empty rather than aborting the "
            "whole resume.", errors.pop("extras"),
        )
        results["extras"] = dict(_EMPTY_EXTRAS)

    if errors:
        # Any failure OTHER than Experience (handled above) or truncation
        # (handled by the split-retry) is a real problem - Gemini being
        # unavailable, timing out, or returning garbage isn't something a
        # smaller prompt fixes, so silently continuing with an empty section
        # would just produce a resume that's wrong in a way nobody notices.
        # Surface it.
        section, exc = next(iter(errors.items()))
        logger.error("Resume extraction failed in the %r section: %s", section, exc)
        raise exc

    contact = results["contact"]
    # Reuses the same name-plausibility check/retry-once-with-a-stricter-
    # prompt strategy already proven for the old single-call pipeline -
    # applies equally well here since the contact call is self-contained.
    if not is_plausible_name(contact.get("name"), results["summary"].get("summary", "")):
        logger.warning(
            "Gemini returned an implausible name (%r) - retrying the contact "
            "extraction once with a stricter prompt.", contact.get("name"),
        )
        try:
            retry_contact = call_contact(raw_text, strict_name_retry=True)
        except GeminiError as exc:
            logger.warning("Contact retry failed (%s) - falling back to raw-text name guessing.", exc)
            retry_contact = {**contact, "name": ""}
        if is_plausible_name(retry_contact.get("name"), results["summary"].get("summary", "")):
            contact = retry_contact
        else:
            contact = {**contact, "name": ""}

    name = clean_name(contact.get("name")) or  ""

    skills_section = results["skills_section"]
    extras = results["extras"]
    merged = {
        "name": name,
        "email": (contact.get("email") or "").strip(),
        "phone": (contact.get("phone") or "").strip(),
        "linkedin": (contact.get("linkedin") or "").strip(),
        "github": (contact.get("github") or "").strip(),
        "portfolio": (contact.get("portfolio") or "").strip(),
        "open_to_relocate": contact.get("open_to_relocate"),
        "open_to_remote": contact.get("open_to_remote"),
        "summary": results["summary"].get("summary", ""),
        "skills": skills_section.get("skills", []),
        "education": skills_section.get("education", []),
        "certifications": skills_section.get("certifications", []),
        "tools": skills_section.get("tools", []),
        "experience": results["experience"],
        "projects": results["projects"],
        "achievements": extras.get("achievements", []),
        "languages": extras.get("languages", []),
        "publications": extras.get("publications", []),
        "volunteer_experience": extras.get("volunteer_experience", []),
        "leadership": extras.get("leadership", []),
    }
    if settings.DEBUG_RESUME_PIPELINE:
        print("\n========== FINAL MERGED JSON ==========")
        print(json.dumps(merged, indent=2))
    _warn_if_section_likely_dropped(raw_text, merged)
    return merged


# Keyword a section's heading is virtually always some variant of, if the
# source document has that section at all - used only to flag a suspicious
# empty result for investigation, never to change behavior. False positives
# are expected and fine (e.g. the word "experience" appearing in running
# prose without an actual work-history section) - this is a diagnostic, not
# a correctness check.
_SECTION_KEYWORDS = {
    "experience": ("experience", "employment"),
    "education": ("education", "university", "college", "degree", "b.tech", "bachelor", "master"),
    "certifications": ("certif", "license", "credential"),
}


def _warn_if_section_likely_dropped(raw_text: str, merged: dict) -> None:
    """Pure logging - catches exactly the failure mode BUG 2 (Experience/
    Education/Certifications silently missing from an otherwise-successful
    resume) reports, so it shows up in logs instead of only in a user's
    complaint. Does not change what gets stored or rendered."""
    lowered_text = raw_text.lower()
    for field, keywords in _SECTION_KEYWORDS.items():
        if not merged.get(field) and any(kw in lowered_text for kw in keywords):
            logger.warning(
                "Resume text appears to mention %r (matched keyword search) but the extracted "
                "%r came back empty - possible silent data loss, worth checking this document "
                "manually or re-running with DEBUG_RESUME_PIPELINE=true.", field, field,
            )
