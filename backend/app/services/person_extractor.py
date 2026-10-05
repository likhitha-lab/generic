"""Candidate-name resolution: a 5-signal priority chain.

Replaces the old single-signal approach (trust Gemini's contact extraction,
or clear to "") with a prioritized fallback chain, each signal tried only
if every higher-priority one failed or came back implausible:

  1. Resume header - Gemini's own contact-extraction result (already
     instructed to look at the top/most prominent line of the document -
     see prompts.build_contact_prompt), validated the same way
     _finalize_name always did (clean_name + is_plausible_name), backed up
     by a strict raw-text scan of just the first few lines as a second
     header-region check if Gemini's own answer isn't usable.
  2. Email owner - derived from the local part of the candidate's email
     address (e.g. "john.doe@gmail.com" -> "John Doe"), skipped for
     generic/departmental addresses ("info@", "hr@", ...).
  3. LinkedIn profile - derived from the profile-URL slug (e.g.
     "linkedin.com/in/john-doe-77a1" -> "John Doe").
  4. PDF/DOCX file metadata - the document's own Author property (PDF
     "Author" field / DOCX core-properties author) - often unset or a
     software/template name rather than the candidate's, so this is
     genuinely the lowest-confidence structured signal, tried only after
     1-3 have all failed.
  5. OCR / last-resort scan - a looser (more lines) pass over the same
     raw_text used in step 1. This is the closest approximation available
     to "specifically the OCR-derived text" WITHOUT extraction.py itself
     reporting OCR provenance (which this module deliberately does not
     require - see "without modifying the remaining pipeline"): any text
     that came from OCR is already merged into raw_text indistinguishably
     from natively-extracted text by the time this module sees it.

Every candidate from every signal is checked against a fixed blocklist of
outputs that must never be returned (see _FORBIDDEN_NAME_OUTPUTS) - if a
signal's result IS one of those (or nothing survives all 5 signals), the
result is "" ("leave blank instead of incorrect"), never a placeholder.

Does not modify extraction.py, extraction_pipeline.py, gemini_client.py, or
any other pipeline stage - this module only reads already-extracted values
(Gemini's contact result, raw_text, email, linkedin) plus, for signal 4, an
independent, read-only re-open of the original file bytes purely to read
document metadata (never touched by the main text-extraction pipeline).
"""
import io
import logging
import re

from docx import Document
import pdfplumber

from app.services.normalization import clean_name, is_plausible_name

logger = logging.getLogger(__name__)

# Outputs that must never be returned as a candidate's name, checked with
# ALL non-alphanumeric characters removed (not just whitespace) - clean_name
# (normalization.py) already strips underscores/punctuation from whatever
# Gemini returns before this module ever sees it, so "Converted_Resume"
# arrives here as "ConvertedResume". Comparing against an equally-squashed
# set is what still catches it (and "Resume.pdf", "resume", etc., all of
# which squash to the same handful of keys).
_FORBIDDEN_NAME_OUTPUTS = {
    "convertedresume", "resume", "unknown", "untitled",
}

# A plausible "Firstname Lastname"-shaped line - 2 to 4 capitalized words,
# each starting with a capital letter. Used for both the strict (signal 1)
# and loose (signal 5) raw-text scans - the only difference between them
# is how many lines from the top of raw_text are checked.
_NAME_LINE_RE = re.compile(r"^[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,3}$")

# Local-part prefixes that mean "this is a departmental/shared mailbox, not
# a specific person" - an email matching one of these is skipped entirely
# for signal 2 rather than mined for a (wrong) name.
_GENERIC_EMAIL_LOCAL_PARTS = {
    "info", "contact", "hr", "support", "noreply", "no-reply", "admin",
    "jobs", "careers", "recruiting", "recruitment", "hello", "team",
    "office", "sales", "mail", "help", "resume", "resumes",
}


def is_forbidden_name(name: str | None) -> bool:
    """True if `name` is (or reduces to, after stripping a .pdf/.docx
    extension and squashing all non-alphanumeric characters) one of the
    placeholder outputs that must never be used as a candidate's name."""
    if not name:
        return False
    stripped = re.sub(r"\.(pdf|docx?)$", "", name.strip().lower())
    squashed = re.sub(r"[^a-z0-9]", "", stripped)
    return squashed in _FORBIDDEN_NAME_OUTPUTS


def _title_case_name_parts(parts: list[str]) -> str | None:
    words = [p for p in parts if p and p.isalpha()]
    if len(words) < 2:
        # A single word is a first name at best - not enough to trust as
        # "the candidate's name" on its own; leave it for a lower-priority
        # signal (or blank) rather than output half a name.
        return None
    return " ".join(w.capitalize() for w in words[:4])


def _name_from_header_lines(raw_text: str, max_lines: int) -> str | None:
    for line in raw_text.splitlines()[:max_lines]:
        candidate = clean_name(line)
        if candidate and _NAME_LINE_RE.match(candidate):
            return candidate
    return None


def _name_from_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local_part = email.split("@", 1)[0].lower()
    local_part = re.sub(r"\d+", "", local_part)  # strip digits: "john.doe123" -> "john.doe"
    parts = [p for p in re.split(r"[._\-+]", local_part) if p]
    if not parts or parts[0] in _GENERIC_EMAIL_LOCAL_PARTS:
        return None
    return _title_case_name_parts(parts)


def _name_from_linkedin(linkedin_url: str | None) -> str | None:
    if not linkedin_url:
        return None
    match = re.search(r"linkedin\.com/in/([^/?]+)", linkedin_url, re.IGNORECASE)
    if not match:
        return None
    slug = match.group(1).lower()
    slug = re.sub(r"-[0-9a-f]{4,}$", "", slug)  # trailing profile-id suffix, e.g. "-77a1b2c3"
    slug = re.sub(r"\d+$", "", slug)
    parts = [p for p in re.split(r"[-_]", slug) if p]
    return _title_case_name_parts(parts)


def _name_from_pdf_metadata(file_bytes: bytes) -> str | None:
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            author = (pdf.metadata or {}).get("Author")
    except Exception as exc:
        logger.warning("Could not read PDF metadata for name resolution (%s).", exc)
        return None
    return clean_name(author) if author else None


def _name_from_docx_metadata(file_bytes: bytes) -> str | None:
    try:
        author = Document(io.BytesIO(file_bytes)).core_properties.author
    except Exception as exc:
        logger.warning("Could not read DOCX metadata for name resolution (%s).", exc)
        return None
    return clean_name(author) if author else None


def resolve_candidate_name(
    gemini_name: str | None,
    summary: str,
    raw_text: str,
    email: str | None,
    linkedin: str | None,
    file_bytes: bytes | None = None,
    suffix: str | None = None,
) -> str:
    """Resolves the candidate's name via the 5-signal priority chain
    described in the module docstring, stopping at the first signal that
    produces a usable, non-forbidden candidate. Returns "" if every signal
    fails - never a placeholder.

    `file_bytes`/`suffix` are optional (signal 4 is simply skipped without
    them - e.g. regenerate_resume() only has the already-extracted
    raw_text, not the original file, and re-fetching it from storage just
    for this would be a bigger pipeline change than this signal is worth).
    """
    # 1. Resume header
    candidate = clean_name(gemini_name)
    if candidate and is_plausible_name(candidate, summary) and not is_forbidden_name(candidate):
        logger.info("Candidate name resolved via Gemini contact/header extraction: %r", candidate)
        return candidate

    candidate = _name_from_header_lines(raw_text, max_lines=3)
    if candidate and not is_forbidden_name(candidate):
        logger.info("Candidate name resolved via strict header-line scan: %r", candidate)
        return candidate

    # 2. Email owner
    candidate = _name_from_email(email)
    if candidate and not is_forbidden_name(candidate):
        logger.info("Candidate name resolved from email address: %r", candidate)
        return candidate

    # 3. LinkedIn profile
    candidate = _name_from_linkedin(linkedin)
    if candidate and not is_forbidden_name(candidate):
        logger.info("Candidate name resolved from LinkedIn profile URL: %r", candidate)
        return candidate

    # 4. PDF/DOCX file metadata
    if file_bytes and suffix:
        candidate = _name_from_pdf_metadata(file_bytes) if suffix == ".pdf" else _name_from_docx_metadata(file_bytes)
        if candidate and not is_forbidden_name(candidate):
            logger.info("Candidate name resolved from file metadata: %r", candidate)
            return candidate

    # 5. OCR / last-resort loose scan
    candidate = _name_from_header_lines(raw_text, max_lines=8)
    if candidate and not is_forbidden_name(candidate):
        logger.info("Candidate name resolved via loose last-resort scan: %r", candidate)
        return candidate

    logger.warning("Could not confidently resolve a candidate name from any of the 5 signals - leaving blank.")
    return ""
