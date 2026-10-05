"""Pre-render validation for the fully-optimized resume JSON.

Runs immediately before build_pdf_bytes()/build_docx_bytes() (see
resume_service.py) - the last checkpoint before the Resume Builder
(file_generator.py) turns `parsed` into an actual document. Deliberately
runs for BOTH pipeline paths: the Resume Converter (which already goes
through resume_optimizer.py's own internal dedup) AND the manual Resume
Generator (which calls Gemini directly and never touches
resume_optimizer.py at all) - this is the one place every resume passes
through regardless of which flow produced it.

Two jobs, never confused with each other:

  1. Auto-clean MINOR issues that are always safe to fix mechanically -
     exact-match duplicate bullets/skills/tools/certifications/jobs/
     projects. "Exact match" only (whitespace/case-insensitive, never
     fuzzy) - the same conservative rule resume_optimizer.py's own dedup
     uses, for the same reason: fuzzy-merging two things that only LOOK
     similar (two different certification levels, two different jobs at
     companies with similar names) would be a real information-loss bug,
     not a cleanup.
  2. Flag everything else as a warning in a structured ValidationReport -
     missing name/email/experience, empty sections, invalid date ranges -
     real, worth surfacing, but NOT safe to silently invent a fix for (a
     missing email should stay missing, never guessed here).

Never raises - see validate_and_clean_resume's per-check try/except. This
module's entire purpose is making sure something ALWAYS reaches the Resume
Builder; a bug in one check must never block generating the rest of the
resume.
"""
import logging
import re

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)

# Sections a resume is normally expected to have - checked for emptiness.
# Projects/Achievements are deliberately excluded: both are legitimately
# optional sections throughout this pipeline (file_generator.py already
# treats a missing/empty one as "just don't render this section", not an
# error), so flagging them as empty here would just be noise.
_CORE_LIST_SECTIONS = ("skills", "tools", "education", "certifications")


class ValidationReport:
    """`warnings` are human-readable descriptions of an issue found -
    including, for anything auto-cleaned, what was actually done about it.
    `cleaned` is True if this pass modified `parsed` in any way. Never
    holds more of the candidate's actual data than needed to describe an
    issue (a duplicate skill's name, a company name for a repeated job),
    and never records a "fix" for anything that was only ever logged as a
    warning - the two are kept structurally distinct in every check
    function below.
    """

    def __init__(self):
        self.warnings: list[str] = []
        self.cleaned: bool = False

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.warning("Resume validation: %s", message)

    def mark_cleaned(self) -> None:
        self.cleaned = True

    def to_dict(self) -> dict:
        return {"warning_count": len(self.warnings), "cleaned": self.cleaned, "warnings": list(self.warnings)}


def _normalize_for_dedup(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _dedupe_preserve_order(items: list) -> list:
    """Exact-match-only (post-normalization) dedup, keeping first-seen
    order and original text - same conservative rule used throughout this
    pipeline (see resume_optimizer.py's _dedupe_preserve_order): never
    merges two items that are only similar, just true duplicates."""
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = _normalize_for_dedup(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _is_invalid_date_range(duration: str) -> bool:
    """True only for a HIGH-CONFIDENCE invalid range: two 4-digit years
    where the first is clearly after the second (e.g. "2024-2020"). Never
    flags a single year, "Present"/"Current", or a non-numeric format as
    invalid - resumes use too many legitimate free-text date styles to
    enforce a strict format here; this narrow check only catches a
    genuinely backwards range."""
    if _PRESENT_RE.search(duration):
        return False
    years = [int(y) for y in _YEAR_RE.findall(duration)]
    return len(years) >= 2 and years[0] > years[-1]


def _check_contact(parsed: dict, report: ValidationReport) -> None:
    if not str(parsed.get("name") or "").strip():
        report.warn("Missing candidate name - the resume will render without a name in the header.")
    if not str(parsed.get("email") or "").strip():
        report.warn("Missing candidate email - the resume will render without a contact email.")


def _check_and_clean_skills(parsed: dict, report: ValidationReport) -> None:
    skills = parsed.get("skills")
    if not skills:
        report.warn("Empty Skills section.")
        return

    if isinstance(skills, dict):
        for category in list(skills.keys()):
            items = skills.get(category) or []
            deduped = _dedupe_preserve_order(items)
            if len(deduped) != len(items):
                report.warn(f"Removed {len(items) - len(deduped)} duplicate skill(s) in category {category!r}.")
                report.mark_cleaned()
            if deduped:
                skills[category] = deduped
            else:
                del skills[category]
        if not skills:
            report.warn("Empty Skills section (every category was empty after dedup).")
    else:
        deduped = _dedupe_preserve_order(skills)
        if len(deduped) != len(skills):
            report.warn(f"Removed {len(skills) - len(deduped)} duplicate skill(s).")
            report.mark_cleaned()
        parsed["skills"] = deduped
        if not deduped:
            report.warn("Empty Skills section.")


def _check_and_clean_flat_list_section(parsed: dict, report: ValidationReport, field: str, label: str) -> None:
    """Shared logic for the simple flat-list sections (tools, education,
    certifications) - dedupe, report what was removed, flag if empty."""
    items = parsed.get(field) or []
    deduped = _dedupe_preserve_order(items)
    if len(deduped) != len(items):
        report.warn(f"Removed {len(items) - len(deduped)} repeated {label} entry/entries.")
        report.mark_cleaned()
    parsed[field] = deduped
    if not deduped:
        report.warn(f"Empty {label} section.")


def _job_identity_key(exp: dict) -> tuple:
    return (
        _normalize_for_dedup(exp.get("company") or ""),
        _normalize_for_dedup(exp.get("role") or ""),
        _normalize_for_dedup(exp.get("duration") or ""),
    )


def _check_and_clean_experience(parsed: dict, report: ValidationReport) -> None:
    experience = parsed.get("experience") or []
    if not experience:
        report.warn("Missing Professional Experience - no work history entries found.")
        return

    seen_jobs: set[tuple] = set()
    deduped_jobs = []
    for exp in experience:
        key = _job_identity_key(exp)
        if any(key) and key in seen_jobs:
            report.warn(
                f"Removed a repeated job entry: {exp.get('company') or '(no company)'} - "
                f"{exp.get('role') or '(no role)'}.",
            )
            report.mark_cleaned()
            continue
        if any(key):
            seen_jobs.add(key)

        points = exp.get("points") or []
        deduped_points = _dedupe_preserve_order(points)
        if len(deduped_points) != len(points):
            report.warn(
                f"Removed {len(points) - len(deduped_points)} duplicate bullet(s) for "
                f"{exp.get('company') or '(no company)'}.",
            )
            report.mark_cleaned()
        exp["points"] = deduped_points

        duration = str(exp.get("duration") or "")
        if duration and _is_invalid_date_range(duration):
            report.warn(f"Invalid date range for {exp.get('company') or '(no company)'}: {duration!r}.")

        deduped_jobs.append(exp)

    parsed["experience"] = deduped_jobs


def _check_and_clean_projects(parsed: dict, report: ValidationReport) -> None:
    projects = parsed.get("projects") or []
    if not projects:
        return  # projects are an optional section throughout this pipeline - not flagged as "missing"

    seen_titles: set[str] = set()
    deduped_projects = []
    for proj in projects:
        key = _normalize_for_dedup(proj.get("title") or "")
        if key and key in seen_titles:
            report.warn(f"Removed a repeated project entry: {proj.get('title')!r}.")
            report.mark_cleaned()
            continue
        if key:
            seen_titles.add(key)

        responsibilities = proj.get("responsibilities") or []
        deduped_resp = _dedupe_preserve_order(responsibilities)
        if len(deduped_resp) != len(responsibilities):
            report.warn(
                f"Removed {len(responsibilities) - len(deduped_resp)} duplicate bullet(s) in "
                f"project {proj.get('title')!r}.",
            )
            report.mark_cleaned()
        proj["responsibilities"] = deduped_resp

        deduped_projects.append(proj)

    parsed["projects"] = deduped_projects


# Every check below receives (parsed, report) and may mutate `parsed` in
# place (auto-clean) and/or call report.warn(). Run in this fixed order so
# the report's warnings always appear in the same, predictable sequence.
_CHECKS = (
    _check_contact,
    _check_and_clean_skills,
    lambda parsed, report: _check_and_clean_flat_list_section(parsed, report, "tools", "Tools"),
    lambda parsed, report: _check_and_clean_flat_list_section(parsed, report, "education", "Education"),
    lambda parsed, report: _check_and_clean_flat_list_section(parsed, report, "certifications", "Certifications"),
    _check_and_clean_experience,
    _check_and_clean_projects,
)


def validate_and_clean_resume(parsed: dict) -> tuple[dict, ValidationReport]:
    """Runs every check/auto-clean pass over `parsed` (mutated in place)
    and returns (parsed, report). Never raises: each check runs in its own
    try/except, so a bug in any single check is itself logged as a
    warning and skipped rather than blocking every other check - or resume
    generation - from proceeding.
    """
    report = ValidationReport()
    for check in _CHECKS:
        try:
            check(parsed, report)
        except Exception as exc:  # deliberately broad - see docstring: never crash
            check_name = getattr(check, "__name__", "validation check")
            report.warn(f"{check_name} failed unexpectedly ({exc}) - skipped, resume generation continues.")

    if report.warnings:
        logger.warning(
            "Resume validation complete: %d warning(s), cleaned=%s", len(report.warnings), report.cleaned,
        )
    else:
        logger.info("Resume validation complete: no issues found.")

    return parsed, report
