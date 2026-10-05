"""Regex-based contact-detail extraction.

Used as a fallback when Gemini doesn't return a field even though it's
present in the source text - LLM extraction can miss a detail depending on
how the document got chunked/laid out, but a regex over the full raw text
doesn't care about visual layout (columns, sidebars, footers) at all, so it
catches cases the model misses regardless of resume design.

Also the single source of truth for the LinkedIn-URL pattern, previously
duplicated in file_generator.py's header renderer.
"""
import re

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_LINKEDIN_RE = re.compile(r"(https?://)?(www\.)?linkedin\.com/\S+", re.IGNORECASE)
# GitHub PROFILE URL only (github.com/<user>), not an arbitrary repo/gist
# link - a bare "github.com" domain match would also catch a repository
# link the candidate mentions for unrelated reasons (e.g. citing an
# open-source project they used, not their own profile); this pattern still
# matches "github.com/<user>/<repo>" too (a profile link is a prefix of a
# repo link), which is an acceptable, common way candidates share it.
_GITHUB_RE = re.compile(r"(https?://)?(www\.)?github\.com/\S+", re.IGNORECASE)
# A "digit cluster": starts/ends on a digit, digits/spaces/parens/dots/dashes
# in between - deliberately loose about internal grouping (phone formatting
# varies too much by locale to hardcode a shape), filtered below instead of
# constrained upfront.
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().-]{6,17}\d")
# Resumes are full of "2020-2024"-shaped job/education date ranges, which is
# structurally identical to a plausible-looking phone number (digits-
# separator-digits) - explicitly exclude that one shape rather than trying
# to make the main pattern smart enough to tell them apart on its own.
_YEAR_RANGE_RE = re.compile(r"^(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}$")


def extract_email(text: str) -> str:
    match = _EMAIL_RE.search(text)
    return match.group(0) if match else ""


def extract_linkedin_url(text: str | None) -> str:
    if not text:
        return ""
    match = _LINKEDIN_RE.search(text)
    return match.group(0).rstrip(".,;") if match else ""


def extract_github_url(text: str | None) -> str:
    if not text:
        return ""
    match = _GITHUB_RE.search(text)
    return match.group(0).rstrip(".,;") if match else ""


def extract_phone(text: str) -> str:
    """Best-effort fallback, not a rigorous phone validator (no single regex
    correctly covers every country's format) - only used when Gemini's own
    extraction comes back empty."""
    for match in _PHONE_CANDIDATE_RE.finditer(text):
        candidate = match.group(0).strip()
        if _YEAR_RANGE_RE.match(candidate):
            continue
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 15:
            return candidate
    return ""


# --- "Find every match" variants (below) --------------------------------
#
# The four functions above only ever return the FIRST match - the right
# behavior for their one existing caller (a single-field fallback when
# Gemini's own extraction came back empty). A PII detector needs every
# occurrence, not just the first, so these reuse the exact same compiled
# patterns above rather than duplicating them - one source of truth for
# what an email/phone/LinkedIn/GitHub URL looks like, in both callers.

def extract_all_emails(text: str) -> list[str]:
    return [m.group(0) for m in _EMAIL_RE.finditer(text)]


def extract_all_linkedin_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;") for m in _LINKEDIN_RE.finditer(text)]


def extract_all_github_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;") for m in _GITHUB_RE.finditer(text)]


def extract_all_phones(text: str) -> list[str]:
    found = []
    for match in _PHONE_CANDIDATE_RE.finditer(text):
        candidate = match.group(0).strip()
        if _YEAR_RANGE_RE.match(candidate):
            continue
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 15:
            found.append(candidate)
    return found
