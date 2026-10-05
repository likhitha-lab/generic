"""Identity Validation - Phase D of the Resume Intelligence Engine redesign.

Root cause this addresses: the ONLY gate before rendering used to be
`resume_quality_checker.resume_has_minimum_viable_content` (still true, and
still runs later, unchanged) - a deliberately low bar that OR's together 5
signals (name, email, experience, skills, education). A resume with real
experience/skills but a blank name PASSES that gate and renders anyway,
confirmed directly in real generated output: of 90 real resumes generated
from repeated runs of the same handful of real source documents, at least 4
came back with an empty name, and one produced a hallucinated-looking
garbage name (a Professional-Summary-opener fragment misidentified as the
name). There was no dedicated "Name is mandatory" check anywhere.

This module is that dedicated gate - hard-fail, never a silent pass-
through. "Fail validation" means exactly that: `validate_identity` raises
`ResumeValidationError`, and resume_service.py converts it to a clean HTTP
422 (see `_identity_validation_error_to_http`, the same typed-exception ->
clean-HTTP-status pattern already used for `GeminiError`/
`_gemini_error_to_http`) BEFORE the Resume Optimizer or any rendering ever
runs - never a document silently generated without its name.

Checks, in order:
  1. Name non-empty (after full Document Understanding, Entity Linking,
     and person_extractor.py's 5-signal resolution have all already run) -
     the ONE check with no source-presence precondition, since the user's
     own requirement is unconditional: "If the source resume contains a
     name, the generated resume MUST contain it" and this pipeline has no
     reliable way to positively confirm a document truly, 100% has no name
     at all (a scanned/garbled layout might) - so any resume that reaches
     this point still nameless is refused rather than guessed at.
  2. Email/Phone/LinkedIn/GitHub: each checked ONLY if a regex signal (the
     same extractors app/utils/contact.py's own fallback already uses)
     shows the raw source text actually contains one - refusing a resume
     that genuinely never listed a phone number, for instance, would be
     wrong; refusing one that visibly has a phone number but the field
     came back empty is exactly the "field must never disappear"
     requirement. Portfolio/Location have no comparable regex signal (an
     arbitrary personal-website domain, or the address text this pipeline
     deliberately never attempts to extract - see prompts.py) and are
     therefore NOT checked here; whatever value already reached this point
     is preserved by construction via the lossless Canonical Model
     round-trip (canonical_model.py), so there's nothing this check could
     usefully add for those two fields.
  3. No duplicate Employment/Project entities remain - a safety-net re-
     check reusing entity_linking.py's own merge predicates (not a second,
     divergent implementation); should be a no-op if Entity Linking (Phase
     C) already ran, and only ever fires if something bypassed it.

Each check is also exposed as its own non-raising function
(check_name_present, check_contact_preserved, find_duplicate_entities/
count_duplicate_entities) - `validate_identity` is a thin wrapper that
raises on the first one that fails, but the underlying checks are the
single source of truth also used by evaluation_dashboard.py (Phase G) to
report PASS/FAIL and counts for every generated resume without raising.
"""
from app.services.canonical_model import from_legacy_dict
from app.services.entity_linking import should_merge_employment, should_merge_project
from app.utils.contact import extract_email, extract_github_url, extract_linkedin_url, extract_phone


class ResumeValidationError(Exception):
    """Raised by validate_identity() when a resume fails a mandatory
    identity or duplication check and must NOT be rendered."""


# (legacy dict field, extractor, human-readable label) - each extractor is
# the exact same regex-based fallback app/utils/contact.py's
# `_apply_contact_fallback` already uses to fill a field FROM the raw text;
# reused here only to DETECT presence, never to fill anything in.
_SOURCE_PRESENCE_CHECKS: tuple[tuple[str, callable, str], ...] = (
    ("email", extract_email, "email address"),
    ("phone", extract_phone, "phone number"),
    ("linkedin", extract_linkedin_url, "LinkedIn profile"),
    ("github", extract_github_url, "GitHub profile"),
)


def check_name_present(structured: dict) -> bool:
    """True if `structured["name"]` is non-empty - the one check with no
    source-presence precondition (see module docstring for why). Shared by
    validate_identity (raises on False) and evaluation_dashboard.py (reports
    PASS/FAIL on the same boolean, never raising) - one definition, not two
    that could silently drift apart."""
    return bool(str(structured.get("name") or "").strip())


def check_contact_preserved(structured: dict, raw_text: str) -> bool:
    """True unless some contact field a regex signal shows IS present in
    `raw_text` came back empty in `structured` - see module docstring for
    why only email/phone/linkedin/github get this check. Shared by
    validate_identity and evaluation_dashboard.py."""
    for field, extractor, _label in _SOURCE_PRESENCE_CHECKS:
        if str(structured.get(field) or "").strip():
            continue
        if extractor(raw_text):
            return False
    return True


def find_duplicate_entities(structured: dict) -> list[str]:
    """Every Employment/Project PAIR that Entity Linking's own merge
    predicates would still consider duplicates, as human-readable
    descriptions (e.g. "employment 'Acme Corp' appears twice") - should be
    empty after a normal Entity Linking pass (Phase C); any entries mean
    something bypassed it or a merge rule has a gap. Shared by
    validate_identity (raises using the first one, if any) and
    evaluation_dashboard.py (reports len(...) as the duplicate count,
    never raising) - one definition, not two that could silently drift
    apart."""
    resume = from_legacy_dict(structured)
    found: list[str] = []
    employments = resume.employments
    for i in range(len(employments)):
        for j in range(i + 1, len(employments)):
            if should_merge_employment(employments[i], employments[j]):
                found.append(f"employment {employments[i].company!r} appears twice")
    projects = resume.projects
    for i in range(len(projects)):
        for j in range(i + 1, len(projects)):
            if should_merge_project(projects[i], projects[j]):
                found.append(f"project {projects[i].title!r} appears twice")
    return found


def count_duplicate_entities(structured: dict) -> int:
    return len(find_duplicate_entities(structured))


def validate_identity(structured: dict, raw_text: str) -> None:
    """Hard gate - raises ResumeValidationError on the first failed check
    (never collects multiple failures into one message; the caller stops
    processing on the first raise regardless, so there is no value in
    continuing past it). Never mutates `structured`."""
    if not check_name_present(structured):
        raise ResumeValidationError(
            "Candidate name could not be determined from this document. A resume cannot be "
            "generated without a name - please confirm the source document has a clearly "
            "readable name, or provide one via the manual entry flow."
        )

    if not check_contact_preserved(structured, raw_text):
        for field, extractor, label in _SOURCE_PRESENCE_CHECKS:
            if not str(structured.get(field) or "").strip() and extractor(raw_text):
                raise ResumeValidationError(
                    f"A {label} appears to be present in the source document but was lost during "
                    f"processing - refusing to generate an incomplete resume."
                )

    duplicates = find_duplicate_entities(structured)
    if duplicates:
        entity_kind = "employment" if duplicates[0].startswith("employment") else "project"
        raise ResumeValidationError(
            f"Duplicate {entity_kind} entries remain after Entity Linking ({duplicates[0]}) - "
            f"refusing to render a resume with duplicated content."
        )
