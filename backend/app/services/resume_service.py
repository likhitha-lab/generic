"""Orchestrates the "generate/convert content, then persist it" flow shared
by resume creation and regeneration.

This is what actually implements the versioning requirement: every call
here appends a new ResumeVersion (with its own PDF/DOCX/JSON in blob
storage) rather than overwriting anything, so full history is always
preserved.

Session-lifetime note (why every function below calls `db.rollback()`
before doing any Gemini work): the `db: Session` passed in isn't fresh - by
the time these functions run, `get_current_user` (a FastAPI dependency that
runs BEFORE the route handler body) has already done a `db.get(User, ...)`
read on this same session, and for `regenerate_resume` the router has also
already loaded `resume` on it. Either read is enough for SQLAlchemy to
auto-begin a transaction. On Neon (serverless Postgres), a transaction that
sits open-but-idle for the whole duration of a Gemini call gets killed by
Neon itself (`psycopg.errors.IdleInTransactionSessionTimeout`), and the
write we do after Gemini then fails with a already-dead connection. Ending
that transaction with `db.rollback()` (nothing pending, so nothing lost)
right before the slow part, then letting SQLAlchemy autobegin a fresh one
only once we're back to writing, is what avoids that entirely.
"""
import json
import logging
import traceback
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.resume import Resume
from app.models.resume_version import ResumeVersion
from app.models.user import User
from app.schemas.resume import ResumeRequest
from app.services.extraction import (
    extract_docx_text,
    extract_pdf_text,
    retry_if_incomplete,
    validate_and_score,
)
from app.services.extraction_pipeline import extract_resume
from app.services.file_generator import build_docx_bytes, build_pdf_bytes
from app.services.gemini_client import (
    GeminiError,
    GeminiInvalidResponseError,
    GeminiTimeoutError,
    GeminiTruncatedError,
    GeminiUnavailableError,
    call_gemini,
)
from app.services.ats_intelligence import optimize_for_ats
from app.services.industry_intelligence import apply_industry_intelligence
from app.services.job_matcher import match_resume_to_job, tailor_resume_for_job
from app.services.normalization import enforce_limits, normalize_parsed
from app.services.optimization_explainer import explain_optimizations
from app.services.resume_comparison import compare_resumes
from app.services.resume_scoring_engine import score_resume
from app.services.person_extractor import is_forbidden_name, resolve_candidate_name
from app.services.prompts import build_generate_prompt
from app.services.resume_optimizer import optimize_resume
from app.services.resume_quality_checker import resume_has_minimum_viable_content
from app.services.resume_quality_engine import run_quality_engine
from app.services.canonical_model import to_legacy_dict, understand_resume
from app.services.entity_linking import link_entities
from app.services.evaluation_dashboard import build_evaluation_report
from app.services.identity_validation import ResumeValidationError, validate_identity
from app.services.section_recovery import recover_missing_sections
from app.services.section_validation import build_section_report, count_entities
from app.storage.base import StorageService
from app.utils.contact import extract_email, extract_github_url, extract_linkedin_url, extract_phone
from app.utils.files import generated_output_path, safe_slug, uploaded_original_path

logger = logging.getLogger(__name__)

_PDF_CONTENT_TYPE = "application/pdf"
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _debug_log(stage: str, content: str) -> None:
    if settings.DEBUG_RESUME_PIPELINE:
        logger.info("========== %s ==========\n%s", stage, content)


def _check_quality_or_raise(parsed: dict, recovery_log: dict | None = None) -> tuple[dict, dict]:
    """Phase 5 + Phase 6 + Phase 7 - the Resume Quality & Validation
    Engine, the ATS Intelligence Engine, and the Industry Intelligence
    Engine's combined final gate, run at every call site that's about to
    generate a document. Runs resume_validator.py's structural clean/flag
    pass, Phase 5's own automatic fixes plus its 15-check report
    (resume_quality_engine.run_quality_engine), THEN Phase 6's ATS-specific
    fixes (technology-name standardization in bullet text, a duplicate-
    keyword re-check) plus its own ATS report (ats_intelligence.
    optimize_for_ats), THEN Phase 7's industry classification and its two
    safe content adjustments - a summary domain-focus clause and
    industry-weighted verb diversification (industry_intelligence.
    apply_industry_intelligence) - each phase runs on top of what the
    previous one already cleaned/standardized, not the pre-fix version.
    Logs all three reports, and - "only generate DOCX/PDF after validation
    passes" - refuses to proceed ONLY if there's essentially nothing worth
    rendering at all (see resume_has_minimum_viable_content's own
    docstring for the exact, deliberately low bar). A thin-but-real resume
    still gets generated, with its scores/warnings/suggestions logged
    rather than blocking it. Returns (cleaned_parsed, quality_report) -
    callers must use the returned `parsed`, since every engine's automatic
    fixes mutate it."""
    parsed, quality_report = run_quality_engine(parsed, recovery_log=recovery_log)
    _debug_log("QUALITY REPORT", json.dumps(quality_report, indent=2))

    parsed, ats_report = optimize_for_ats(parsed)
    _debug_log("ATS REPORT", json.dumps(ats_report, indent=2))

    parsed, industry_report = apply_industry_intelligence(parsed)
    _debug_log("INDUSTRY REPORT", json.dumps(industry_report, indent=2))

    # Phase 8 - Resume Scoring Engine: pure analysis only, never mutates
    # `parsed` (unlike every engine above) - logged for internal visibility,
    # never used to gate or alter generation.
    scoring_report = score_resume(parsed)
    _debug_log("SCORING REPORT", json.dumps(scoring_report, indent=2))

    # Stashed onto the SAME returned `quality_report` dict (not a new
    # return value - keeps this function's signature/2-tuple return
    # unchanged for every existing call site) so callers building the
    # Evaluation Dashboard (Phase G - see evaluation_dashboard.py) have the
    # ATS/scoring reports for this exact `parsed` without recomputing them.
    quality_report["ats"] = ats_report
    quality_report["industry"] = industry_report
    quality_report["scoring"] = scoring_report

    if not resume_has_minimum_viable_content(parsed):
        logger.error(
            "Resume has no usable content (no name, email, experience, skills, or education) - "
            "refusing to generate a document. Quality score: %d", quality_report["score"],
        )
        raise HTTPException(
            status_code=422,
            detail="Not enough resume content was found (no name, email, experience, skills, or "
            "education) to generate a document.",
        )
    return parsed, quality_report


def _gemini_error_to_http(exc: GeminiError) -> HTTPException:
    """Maps the typed Gemini failures to a clean, specific HTTP status - not
    a stack trace, not a bare 500. GeminiTruncatedError reaching here means
    extract_resume's internal split-retry already exhausted its budget
    (_MAX_SPLIT_DEPTH) for that section - rare, but still possible for a
    pathological document, and still shouldn't leak the raw exception."""
    if isinstance(exc, GeminiTimeoutError):
        return HTTPException(status_code=504, detail="Gemini request timed out. Please try again.")
    if isinstance(exc, GeminiTruncatedError):
        return HTTPException(
            status_code=502,
            detail="Gemini could not process this resume within the configured output size, "
            "even after retrying with smaller chunks. Please try again.",
        )
    if isinstance(exc, GeminiInvalidResponseError):
        return HTTPException(status_code=502, detail="Gemini returned an unexpected response. Please try again.")
    if isinstance(exc, GeminiUnavailableError):
        return HTTPException(status_code=502, detail="Gemini is currently unavailable. Please try again shortly.")
    return HTTPException(status_code=502, detail="AI extraction failed. Please try again.")


def _identity_validation_error_to_http(exc: ResumeValidationError) -> HTTPException:
    """Same typed-exception -> clean-HTTP-status pattern as
    _gemini_error_to_http above - Identity Validation (Phase D of the
    Resume Intelligence Engine redesign, see identity_validation.py) is a
    hard gate: Name/Email/Phone/LinkedIn/GitHub must never silently
    disappear, and no duplicate Employment/Project entities may survive
    Entity Linking. A 422 (not a 500) since this is a rejected document,
    not a server error."""
    return HTTPException(status_code=422, detail=str(exc))


def _apply_contact_fallback(structured: dict, raw_text: str) -> None:
    """Fill email/phone/LinkedIn/GitHub from a regex scan of the raw text
    when Gemini's own extraction came back empty - layout-independent
    (works regardless of columns/sidebars/footers), so it catches real
    contact details the model missed rather than only whatever it managed
    to find. Portfolio/website has no reliable regex pattern (no fixed
    domain to match on) and open_to_relocate/open_to_remote are boolean
    preferences, not URLs - neither gets a regex fallback; both rely on
    Gemini's own explicit-statement-only extraction (see prompts.py)."""
    if not structured.get("email"):
        structured["email"] = extract_email(raw_text)
    if not structured.get("phone"):
        structured["phone"] = extract_phone(raw_text)
    if not structured.get("linkedin"):
        structured["linkedin"] = extract_linkedin_url(raw_text)
    if not structured.get("github"):
        structured["github"] = extract_github_url(raw_text)


def _contact_dict_for_render(structured: dict) -> dict:
    """The PDF/DOCX generator (file_generator.py, unchanged by this redesign)
    expects a contact dict keyed "links", not "linkedin" - this is the one
    place that maps the standardized schema's "linkedin" field to the
    generator's existing "links" key, so the generator never has to change.
    Also carries through "location" if present - only ever true for a
    manual-entry resume (the upload/convert pipeline deliberately never
    extracts an address at all, see prompts.py's own "Do NOT extract
    address" instruction); `structured.get("location")` is simply None for
    every uploaded resume, so this is a safe no-op for that flow.

    github/portfolio/open_to_relocate/open_to_remote (Contact Intelligence
    additions) are carried through as their own distinct keys - never
    smushed into "links" the way LinkedIn is, since a candidate may have
    all three of LinkedIn/GitHub/portfolio at once and each needs to render
    as its own line (see file_generator._contact_line)."""
    return {
        "email": structured.get("email"),
        "phone": structured.get("phone"),
        "location": structured.get("location"),
        "links": structured.get("linkedin"),
        "github": structured.get("github"),
        "portfolio": structured.get("portfolio"),
        "open_to_relocate": structured.get("open_to_relocate"),
        "open_to_remote": structured.get("open_to_remote"),
    }


# --- Recruiter-controlled visibility (display-only; never deletes data) ----
#
# Stored as JSON text on ResumeVersion.visibility_json - same "small
# settings blob, not one column per field" precedent as content_json
# itself (see that column's own comment). Every reader treats a missing/
# unset value as "show everything", so a version created before this
# feature existed (visibility_json is NULL) renders exactly as it always
# has, and a PARTIAL update (e.g. only show_email changed) never silently
# resets the other four flags to their default - the previous, sole
# writer of visibility_json is always merged with first, then overridden.
_DEFAULT_VISIBILITY: dict = {
    "show_email": True, "show_phone": True, "show_linkedin": True,
    "show_address": True, "show_employment_dates": True,
}


def get_version_visibility(version: ResumeVersion) -> dict:
    """Returns this version's current display-visibility settings - all-
    True (show everything) if none have ever been set."""
    if not version.visibility_json:
        return dict(_DEFAULT_VISIBILITY)
    stored = json.loads(version.visibility_json)
    return {**_DEFAULT_VISIBILITY, **stored}


def update_version_visibility(
    db: Session, storage: StorageService, version: ResumeVersion, updates: dict,
) -> ResumeVersion:
    """Updates ONE version's display-visibility settings and re-renders
    that SAME version's stored PDF/DOCX to reflect them immediately -
    overwrites only the two rendered FILES in blob storage (same paths,
    same version row, same version_number); `content_json` and every other
    stored field are never touched. This is what "hide only during
    rendering, never remove data from storage" means at the persistence
    layer: the real data survives untouched in content_json forever, only
    the derived, presentation-only PDF/DOCX artifacts change to match the
    new preferences. Partial `updates` (e.g. just `{"show_email": False}`)
    are merged onto the version's current settings, never replacing the
    other four flags."""
    new_visibility = {**get_version_visibility(version), **updates}
    parsed = get_version_content(version)
    name = parsed.get("name") or version.resume.title
    contact = _contact_dict_for_render(parsed)

    pdf_bytes = build_pdf_bytes(parsed, name, contact, visibility=new_visibility)
    docx_bytes = build_docx_bytes(parsed, name, contact, visibility=new_visibility)
    storage.upload_file(version.pdf_blob_path, pdf_bytes, _PDF_CONTENT_TYPE)
    storage.upload_file(version.docx_blob_path, docx_bytes, _DOCX_CONTENT_TYPE)

    version.visibility_json = json.dumps(new_visibility)
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def _persist_version(
    db: Session,
    storage: StorageService,
    resume: Resume,
    user: User,
    parsed: dict,
    name: str,
    contact: dict,
    tone: str,
    original_bytes: Optional[bytes] = None,
    original_filename: Optional[str] = None,
    raw_parsed: Optional[dict] = None,
    visibility: Optional[dict] = None,
    evaluation_report: Optional[dict] = None,
) -> ResumeVersion:
    version_number = (resume.latest_version.version_number + 1) if resume.versions else 1

    # Single standard section order (Talent Acquisition-approved) - no
    # adaptive/seniority/role-based layout. Previously this called the
    # Resume Structuring Engine (resume_structuring.build_structure_plan)
    # to compute a per-candidate section_plan/extra_content/max_pages;
    # that call has been removed, not just left unused, so it's obvious
    # from reading this function alone that no adaptive plan is computed
    # here anymore. build_pdf_bytes/build_docx_bytes are called with no
    # plan at all, so both fall through to file_generator.py's own
    # _DEFAULT_SECTION_SEQUENCE - the same fixed order/labels for every
    # resume, and the same years-of-experience page cap
    # (_max_pages_allowed) every resume used before the Structuring Engine
    # existed. resume_structuring.py itself is left in place, disabled
    # (unused) rather than deleted, in case adaptive layouts are wanted
    # again later.
    pdf_bytes = build_pdf_bytes(parsed, name, contact, visibility=visibility)
    docx_bytes = build_docx_bytes(parsed, name, contact, visibility=visibility)

    # Both the manual Resume Generator and the Resume Converter write here -
    # Resume_output/{user_id}/... - matching the bucket's existing folder.
    json_path = generated_output_path(user.id, version_number, "content.json")
    pdf_path = generated_output_path(user.id, version_number, "resume.pdf")
    docx_path = generated_output_path(user.id, version_number, "resume.docx")

    storage.upload_file(json_path, json.dumps(parsed).encode("utf-8"), "application/json")
    storage.upload_file(pdf_path, pdf_bytes, _PDF_CONTENT_TYPE)
    storage.upload_file(docx_path, docx_bytes, _DOCX_CONTENT_TYPE)

    if raw_parsed is not None:
        # The Resume Converter's Stage 1 output (extraction_pipeline.
        # extract_resume(), before resume_optimizer.py's Resume Intelligence
        # Engine condenses it for rendering) - persisted so "nothing is ever
        # lost internally"
        # holds at the storage layer, not just in-memory for the duration
        # of one request. Not tracked in a ResumeVersion column/exposed via
        # the API (that would need a DB migration + schema change, out of
        # scope here) - a blob-storage-only safety net for now.
        raw_json_path = generated_output_path(user.id, version_number, "raw_content.json")
        storage.upload_file(raw_json_path, json.dumps(raw_parsed).encode("utf-8"), "application/json")

    if evaluation_report is not None:
        # Evaluation Dashboard (Phase G - see evaluation_dashboard.py) -
        # the primary regression metric for this project's future
        # development, per explicit requirement: saved alongside every
        # generated resume's content.json/PDF/DOCX using the EXACT same
        # "sibling blob, no ResumeVersion column, no DB migration" pattern
        # already established above for raw_content.json.
        eval_path = generated_output_path(user.id, version_number, "evaluation_report.json")
        storage.upload_file(eval_path, json.dumps(evaluation_report).encode("utf-8"), "application/json")

    original_path = None
    if original_bytes is not None and original_filename is not None:
        original_path = uploaded_original_path(user.id, original_filename)
        storage.upload_file(original_path, original_bytes, "application/octet-stream")

    version = ResumeVersion(
        resume_id=resume.id,
        version_number=version_number,
        tone=tone,
        content_json=json.dumps(parsed),
        visibility_json=json.dumps(visibility) if visibility is not None else None,
        original_blob_path=original_path,
        json_blob_path=json_path,
        pdf_blob_path=pdf_path,
        docx_blob_path=docx_path,
        created_by_id=user.id,
    )
    db.add(version)
    db.flush()
    return version


# --- Enterprise-level features (versioning, comparison, job matching) -------
#
# All of these reuse EXISTING infrastructure rather than adding new DB
# columns/tables: ResumeVersion.content_json already duplicates each
# version's full parsed JSON directly in Postgres (see _persist_version
# above), so comparison/history/rollback never need a storage round-trip
# and never need a schema migration - "Resume versioning" itself was
# already fully implemented before this feature set (every create/
# regenerate call has always appended a new ResumeVersion, never
# overwriting one - see this module's own docstring).

def get_version_content(version: ResumeVersion) -> dict:
    """Reads a stored version's full optimized JSON directly from the
    already-persisted `content_json` column - no storage/blob round-trip
    needed."""
    return json.loads(version.content_json)


def compare_resume_versions(before_version: ResumeVersion, after_version: ResumeVersion) -> dict:
    """Enterprise feature - Before vs After comparison, and an AI
    explanation of every optimization between any two stored versions of
    the same resume (see resume_comparison.py/optimization_explainer.py -
    both pure, deterministic, and read-only; neither version's stored
    content is touched).
    """
    before = get_version_content(before_version)
    after = get_version_content(after_version)
    return {
        "comparison": compare_resumes(before, after),
        "explanations": explain_optimizations(before, after),
    }


def get_optimization_history(resume: Resume, persona: str = "general") -> list[dict]:
    """Enterprise feature - Optimization history. Computes resume_scoring_
    engine.py's Overall Resume Score (Phase 8, pure/read-only) for every
    stored version from its already-persisted content_json - a per-version
    score trend across the resume's whole edit history, computed on the
    fly rather than persisted (so it always reflects the current scoring
    algorithm, never a stale snapshot)."""
    history = []
    for version in sorted(resume.versions, key=lambda v: v.version_number):
        parsed = get_version_content(version)
        report = score_resume(parsed, persona=persona)
        history.append({
            "version_number": version.version_number,
            "version_id": version.id,
            "created_at": version.created_at,
            "score": report["score"],
            "breakdown": report["breakdown"],
        })
    return history


def match_resume_version_to_job(version: ResumeVersion, job_description: str) -> dict:
    """Enterprise feature - Job Description matching, Missing Skill
    recommendations, and Resume tailoring (see job_matcher.py) for one
    stored version. Read-only - never mutates the version's stored
    content; tailoring returns a SUGGESTED skill order and text
    suggestions only, never an automatic rewrite."""
    parsed = get_version_content(version)
    return tailor_resume_for_job(parsed, job_description)


def rollback_resume_to_version(
    db: Session, storage: StorageService, resume: Resume, user: User, target_version: ResumeVersion,
) -> ResumeVersion:
    """Enterprise feature - Version rollback. Creates a NEW ResumeVersion
    (never deletes or overwrites `target_version` or anything in between -
    full history is always preserved) whose content is an exact copy of
    `target_version`'s already-stored JSON, freshly re-rendered into PDF/
    DOCX. Does NOT re-run Phase 5-8 optimization on it - "rollback" means
    reproducing that earlier state exactly, not re-optimizing it again -
    including its display-visibility settings (see get_version_visibility),
    which are carried forward onto the new version too.
    """
    parsed = get_version_content(target_version)
    name = parsed.get("name") or resume.title
    contact = _contact_dict_for_render(parsed)
    tone = target_version.tone or "Professional"
    visibility = get_version_visibility(target_version)
    version = _persist_version(db, storage, resume, user, parsed, name, contact, tone, visibility=visibility)
    db.commit()
    return version


def create_manual_resume(db: Session, storage: StorageService, user: User, data: ResumeRequest) -> Resume:
    # End the transaction `get_current_user`'s earlier read opened on this
    # session BEFORE the slow Gemini call - see module docstring. Nothing
    # has been written yet, so there's nothing to lose.
    db.rollback()
    try:
        parsed = call_gemini(build_generate_prompt(data))
    except GeminiError as exc:
        raise _gemini_error_to_http(exc) from exc
    parsed = normalize_parsed(parsed)
    parsed = enforce_limits(parsed)

    contact = {
        "email": data.email, "phone": data.phone, "location": data.location, "links": data.links,
        "github": data.github, "portfolio": data.portfolio,
        "open_to_relocate": data.open_to_relocate, "open_to_remote": data.open_to_remote,
    }
    parsed["name"] = data.name
    parsed.update({k: v for k, v in contact.items() if v})

    # Last checkpoint before the Resume Builder (file_generator.py) - runs
    # here too, not just in the Resume Converter's flow, since the manual
    # Resume Generator calls Gemini directly and never goes through
    # resume_optimizer.py's own internal dedup at all.
    parsed, _quality_report = _check_quality_or_raise(parsed)

    try:
        resume = Resume(
            user_id=user.id,
            title=data.name or "Untitled Resume",
            source_type="manual",
            source_input=json.dumps(data.model_dump()),
        )
        db.add(resume)
        db.flush()

        _persist_version(db, storage, resume, user, parsed, data.name, contact, data.tone)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(resume)
    return resume


def create_uploaded_resume(
    db: Session, storage: StorageService, user: User, filename: str, file_bytes: bytes, suffix: str
) -> Resume:
    # Same reasoning as create_manual_resume - end the dangling transaction
    # from the auth lookup before text extraction/Gemini, which together can
    # take a lot longer than Neon's idle-in-transaction timeout.
    db.rollback()

    logger.info("Step 1: Extracting text from uploaded file (%s)", suffix)
    try:
        raw_text = extract_pdf_text(file_bytes) if suffix == ".pdf" else extract_docx_text(file_bytes)
    except Exception:
        logger.exception("Step 1 FAILED: text extraction raised\n%s", traceback.format_exc())
        raise
    if settings.DEBUG_RESUME_PIPELINE:
        print("\n========== RAW EXTRACTED TEXT (first 50 lines) ==========\n")
        print("\n".join(raw_text.splitlines()[:50]))
        print("\n===========================================================\n")
    if not raw_text.strip():
        raise ValueError("Could not extract any text from the uploaded file")
    logger.info("Step 1 complete: extracted %d characters", len(raw_text))

    raw_text, _validation_result, _score = retry_if_incomplete(raw_text, suffix, file_bytes)

    logger.info("Step 2: Stage 1 Gemini extraction (extraction_pipeline.extract_resume)")
    try:
        structured = extract_resume(raw_text)
    except GeminiError as exc:
        logger.exception("Step 2 FAILED: Stage 1 Gemini extraction raised %s\n%s", type(exc).__name__, traceback.format_exc())
        raise _gemini_error_to_http(exc) from exc
    logger.info("Step 2 complete: Stage 1 extraction returned")

    structured = normalize_parsed(structured)
    if settings.DEBUG_RESUME_PIPELINE:
        print("\n========== AFTER NORMALIZE ==========")
        print(json.dumps(structured, indent=2))
    structured = enforce_limits(structured)
    if settings.DEBUG_RESUME_PIPELINE:
        print("\n========== AFTER ENFORCE LIMITS ==========")
        print(json.dumps(structured, indent=2))
    _apply_contact_fallback(structured, raw_text)

    # Section Recovery Engine (see app/services/section_recovery.py): if a
    # section appears present in the raw source text but Stage 1 came back
    # with it empty, retry that section's extraction once, then fall back
    # to deterministic inference, before giving up - "never silently
    # generate an incomplete resume". Runs BEFORE the Resume Intelligence
    # Engine so any recovered content flows through optimize_resume() like
    # anything Stage 1 found directly.
    structured, recovery_log = recover_missing_sections(raw_text, structured)
    if recovery_log:
        logger.info("Section Recovery Engine: %s", recovery_log)

    # Document Understanding + Entity Linking (Phases B/C of the Resume
    # Intelligence Engine redesign - see canonical_model.py/
    # entity_linking.py): builds a Canonical Resume Model (every entity
    # gets a stable id), then merges duplicate Employment/Project/
    # Certification/Achievement/Career-Break entities by similarity - the
    # direct fix for the confirmed real-world defect where the same real
    # job survived 2-4+ times in a rendered resume because Stage 1's
    # split-retry (on a long resume) re-extracts overlapping content and
    # Gemini rewords it slightly differently each time.
    canonical_resume = understand_resume(structured)
    pre_linking_counts = count_entities(canonical_resume)
    canonical_resume = link_entities(canonical_resume)
    post_linking_counts = count_entities(canonical_resume)
    structured = to_legacy_dict(canonical_resume)
    _debug_log("NORMALIZED JSON", json.dumps(structured, indent=2))

    # `structured["name"]` is the JSON that gets saved/rendered. Resolved
    # via person_extractor.py's 5-signal priority chain (header/Gemini,
    # email, LinkedIn, file metadata, last-resort scan) rather than trusting
    # Gemini's contact extraction alone - if every signal fails, it stays ""
    # rather than being replaced with an invented/placeholder value (never
    # "Converted_Resume", "Unknown", etc. - see person_extractor.py).
    structured["name"] = resolve_candidate_name(
        structured.get("name"), structured.get("summary", ""), raw_text,
        structured.get("email"), structured.get("linkedin"),
        file_bytes=file_bytes, suffix=suffix,
    )

    # Identity Validation (Phase D of the Resume Intelligence Engine
    # redesign - see identity_validation.py): hard-fails (422) rather than
    # silently rendering a document missing its Name, or missing an Email/
    # Phone/LinkedIn/GitHub that was actually present in the source, or
    # still containing a duplicate Employment/Project entity Entity
    # Linking should already have merged.
    try:
        validate_identity(structured, raw_text)
    except ResumeValidationError as exc:
        raise _identity_validation_error_to_http(exc) from exc

    title = structured["name"]
    contact = _contact_dict_for_render(structured)
    _debug_log("STAGE 1 - FULL EXTRACTION (nothing discarded)", json.dumps(structured, indent=2))

    # Resume Intelligence Engine: consolidate/categorize/rewrite Stage 1's
    # full-fidelity JSON into a recruiter-ready resume (see
    # resume_optimizer.py) - `structured` itself is untouched by this and is
    # what gets persisted as the raw, nothing-lost JSON a few lines down
    # (`raw_parsed=structured`).
    #
    # optimize_resume() never raises: it's internally split into several
    # small, bounded Gemini calls (skills/tools, one per job, projects,
    # achievements, summary), and each one degrades independently to its
    # Stage 1 form on failure - see resume_optimizer.py's module docstring
    # for why a single monolithic call reliably truncated (and therefore
    # 502'd) on a real, detailed multi-job resume, and why per-piece
    # degradation replaced the old all-or-nothing "revert to the raw Stage 1
    # dump" fallback.
    logger.info("Step 3: Resume Intelligence Engine (resume_optimizer.optimize_resume)")
    polished = optimize_resume(structured, tone="Professional")
    logger.info("Step 3 complete: resume optimization returned")
    _debug_log("STAGE 2 - POLISHED RESUME (for rendering)", json.dumps(polished, indent=2))

    # Last checkpoint before the Resume Builder (file_generator.py) - see
    # resume_quality_engine.py: auto-cleans safe exact/near-duplicate
    # issues, applies its own automatic fixes, flags everything else
    # (missing name/email, empty sections, invalid dates, ...) as a
    # warning without inventing a fix for it. `recovery_log` (Section
    # Recovery Engine, run earlier on `structured`) is passed through so
    # it's surfaced in the same quality report, not just application logs.
    polished, quality_report = _check_quality_or_raise(polished, recovery_log=recovery_log)

    # Section Validation (Phase E - see section_validation.py): compares
    # entity counts across Document Understanding -> Entity Linking ->
    # final rendered output for every section, surfaced in the same
    # quality report so "how many duplicates did Entity Linking merge" and
    # "did anything disappear after Entity Linking" are inspectable, not
    # just implied by application logs.
    quality_report["section_validation"] = build_section_report(
        pre_linking_counts, post_linking_counts, polished, recovery_log,
    )
    _debug_log("SECTION VALIDATION REPORT", json.dumps(quality_report["section_validation"], indent=2))

    # Evaluation Dashboard (Phase G - see evaluation_dashboard.py): one
    # consolidated PASS/FAIL + percentage report for this exact generated
    # resume, saved alongside content.json/PDF/DOCX (see _persist_version
    # below) - the primary regression metric per explicit requirement.
    evaluation_report = build_evaluation_report(
        polished, raw_text, quality_report["section_validation"], recovery_log,
        quality_report["ats"], quality_report["scoring"],
    )
    _debug_log("EVALUATION REPORT", json.dumps(evaluation_report, indent=2))

    try:
        logger.info("Step 4: Creating Resume model")
        resume = Resume(
            user_id=user.id,
            title=title,
            source_type="upload",
            source_input=raw_text,
        )
        db.add(resume)
        db.flush()
        logger.info("Step 4 complete: Resume model created (id=%s)", resume.id)

        original_name = f"original_{safe_slug(filename)}"
        logger.info("Step 5: Generating DOCX/PDF and uploading to storage")
        _persist_version(
            db, storage, resume, user, polished, title, contact, tone="Professional",
            original_bytes=file_bytes, original_filename=original_name, raw_parsed=structured,
            evaluation_report=evaluation_report,
        )
        logger.info("Step 5 complete: files generated and uploaded")

        logger.info("Step 6: Committing database transaction")
        db.commit()
        logger.info("Step 6 complete: transaction committed")
    except Exception:
        logger.exception("Steps 4-6 FAILED - rolling back\n%s", traceback.format_exc())
        db.rollback()
        raise
    db.refresh(resume)
    logger.info("Step 7: Returning created Resume (id=%s)", resume.id)
    return resume


def regenerate_resume(db: Session, storage: StorageService, user: User, resume: Resume, tone_override: Optional[str]) -> ResumeVersion:
    # `resume` was already loaded by the router (_get_owned_resume) before
    # this function was even called, on the same session the auth
    # dependency's user lookup opened a transaction on - so there's already
    # something to end here too, not just in the two create_* functions.
    #
    # Everything read off `resume` that the Gemini call needs is cached into
    # plain local variables BEFORE `db.rollback()` runs: rollback expires
    # every ORM object in the session (their attributes become "stale,
    # reload on next access"), so accessing `resume.xxx` again immediately
    # after would silently re-open a transaction and put us right back
    # where we started - reading everything needed first avoids that.
    source_type = resume.source_type
    # Only the upload/convert branch below has a Stage 1 (raw, nothing-
    # discarded) JSON distinct from what gets persisted/rendered - the
    # manual flow's `parsed` already IS what should be persisted, so this
    # stays None for that branch and _persist_version simply skips writing
    # the extra raw-JSON blob.
    raw_parsed_for_persist: Optional[dict] = None
    # Same reasoning as raw_parsed_for_persist above: the Evaluation
    # Dashboard (Phase G) needs a source document/raw_text to compare
    # against, which only the upload/convert branch has - stays None for
    # the manual flow, and _persist_version simply skips writing that blob.
    evaluation_report: Optional[dict] = None

    if source_type == "manual":
        source_input = resume.source_input
        db.rollback()

        data = ResumeRequest(**json.loads(source_input))
        if tone_override:
            data.tone = tone_override

        try:
            parsed = call_gemini(build_generate_prompt(data))
        except GeminiError as exc:
            raise _gemini_error_to_http(exc) from exc
        parsed = normalize_parsed(parsed)
        parsed = enforce_limits(parsed)

        contact = {
            "email": data.email, "phone": data.phone, "location": data.location, "links": data.links,
            "github": data.github, "portfolio": data.portfolio,
            "open_to_relocate": data.open_to_relocate, "open_to_remote": data.open_to_remote,
        }
        parsed["name"] = data.name
        parsed.update({k: v for k, v in contact.items() if v})

        parsed, _quality_report = _check_quality_or_raise(parsed)

        persist_args = (parsed, data.name, contact, data.tone)
    else:
        raw_text = resume.source_input
        tone = tone_override or (resume.latest_version.tone if resume.latest_version else "Professional")
        fallback_name = resume.title
        db.rollback()

        # No original file bytes are available here (only the already-
        # extracted text stored at upload time) - retry_if_incomplete needs
        # the bytes to re-run extraction, so this only validates and logs,
        # the same as create_uploaded_resume does before its own retry.
        validate_and_score(raw_text)

        try:
            structured = extract_resume(raw_text, tone=tone)
        except GeminiError as exc:
            raise _gemini_error_to_http(exc) from exc

        structured = normalize_parsed(structured)
        structured = enforce_limits(structured)
        _apply_contact_fallback(structured, raw_text)
        structured, recovery_log = recover_missing_sections(raw_text, structured, tone)
        if recovery_log:
            logger.info("Section Recovery Engine: %s", recovery_log)
        canonical_resume = understand_resume(structured)
        pre_linking_counts = count_entities(canonical_resume)
        canonical_resume = link_entities(canonical_resume)
        post_linking_counts = count_entities(canonical_resume)
        structured = to_legacy_dict(canonical_resume)
        _debug_log("NORMALIZED JSON", json.dumps(structured, indent=2))

        # No original file bytes here either (see the retry_if_incomplete
        # comment above) - signal 4 (file metadata) is simply skipped.
        # Beyond the 5-signal chain, fall back to the resume's existing
        # title (already validated, heading-free, at original creation
        # time, cached above before the rollback) if every signal fails -
        # if Gemini regresses on a re-run, keep the name that's already
        # known good, rather than losing it. Guard against `fallback_name`
        # itself being a legacy placeholder from before this fix existed.
        name = resolve_candidate_name(
            structured.get("name"), structured.get("summary", ""), raw_text,
            structured.get("email"), structured.get("linkedin"),
        )
        if not name and not is_forbidden_name(fallback_name):
            name = fallback_name
        structured["name"] = name

        # Identity Validation (Phase D - see identity_validation.py):
        # same hard gate as create_uploaded_resume's matching call.
        try:
            validate_identity(structured, raw_text)
        except ResumeValidationError as exc:
            raise _identity_validation_error_to_http(exc) from exc

        contact = _contact_dict_for_render(structured)
        _debug_log("STAGE 1 - FULL EXTRACTION (nothing discarded)", json.dumps(structured, indent=2))

        # See create_uploaded_resume's matching comment: optimize_resume()
        # never raises - each of its internal per-piece Gemini calls
        # degrades independently to its Stage 1 form on failure.
        polished = optimize_resume(structured, tone=tone)
        _debug_log("RESUME INTELLIGENCE ENGINE OUTPUT (for rendering)", json.dumps(polished, indent=2))

        polished, quality_report = _check_quality_or_raise(polished, recovery_log=recovery_log)
        quality_report["section_validation"] = build_section_report(
            pre_linking_counts, post_linking_counts, polished, recovery_log,
        )
        evaluation_report = build_evaluation_report(
            polished, raw_text, quality_report["section_validation"], recovery_log,
            quality_report["ats"], quality_report["scoring"],
        )

        raw_parsed_for_persist = structured
        persist_args = (polished, name, contact, tone)

    # `resume`'s attributes (id, versions, latest_version, ...) needed by
    # _persist_version below are read fresh here - this is the "open a new
    # transaction to write" half; SQLAlchemy autobegins it on first access.
    try:
        version = _persist_version(
            db, storage, resume, user, *persist_args, raw_parsed=raw_parsed_for_persist,
            evaluation_report=evaluation_report,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(version)
    return version


def delete_resume_files(storage: StorageService, resume: Resume) -> None:
    """Delete every blob (original upload + generated PDF+DOCX+JSON)
    across every version of `resume`. Called from the delete endpoints
    alongside the DB soft-delete - without this, blobs were never removed
    from storage and accumulated forever regardless of what happened in the
    database.

    `storage.delete_file` is idempotent (never raises on a missing object),
    so this is safe to call even if a previous delete attempt partially
    succeeded.
    """
    for version in resume.versions:
        for path in (version.original_blob_path, version.json_blob_path, version.pdf_blob_path, version.docx_blob_path):
            if path:
                storage.delete_file(path)
