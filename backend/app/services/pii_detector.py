"""PII Detection layer - Phase 1 of the PII Masking/Unmasking architecture
(see reference_docs/PII_MASKING_ARCHITECTURE.md). Detection only - this
module never rewrites, redacts, or replaces anything; it returns a
structured report of what it found and where. Masking is a separate,
not-yet-implemented phase that would consume this report.

Two entry points, matching the architecture doc's own two detection tiers:

  detect_pii_in_text(raw_text)
    Runs BEFORE the first Gemini call (Stage 1 extraction hasn't happened
    yet). Finds what's reliably detectable from raw source text alone:
    candidate name (reusing person_extractor.resolve_candidate_name's own
    local, non-Gemini signals), email/phone/LinkedIn/GitHub (reusing
    app/utils/contact.py's existing regexes), a best-effort portfolio URL,
    a best-effort address line, and label-anchored PAN/Aadhaar/Passport/
    Employee-ID/Client mentions. Deliberately does NOT attempt to detect
    Company or Project names here - free-text company/project names have
    no reliable pattern without either a local NER model or already-
    categorized structured data (see the architecture doc's Tier 2
    discussion) - guessing at them here would trade precision for recall
    in exactly the direction this task said not to.

  detect_pii_in_structured(structured)
    Runs on the ALREADY-EXTRACTED structured dict (after Stage 1
    extraction, before the Resume Optimizer's own Gemini rewrite calls).
    Companies/clients/projects are already cleanly separated fields at
    this point (experience[].company, experience[].notes' "Client: ..."
    convention, projects[].client, projects[].title) - detecting them here
    is exact, not heuristic, and carries none of detect_pii_in_text's
    Company/Project precision risk.

Callers are expected to run detect_pii_in_text before extraction and
detect_pii_in_structured after it, merging the two reports (merge_pii_
reports) into one full picture before any masking phase is ever built.
"""
import re

from app.services.person_extractor import resolve_candidate_name
from app.utils.contact import (
    extract_all_emails,
    extract_all_github_urls,
    extract_all_linkedin_urls,
    extract_all_phones,
)

# Every category this detector reports on, and the shape callers can
# always rely on - every key present, every value a list (possibly empty),
# never a missing key. Order matches the PERSONAL / COMPANY / OPTIONAL
# grouping in the objective this module was built against.
REPORT_KEYS: tuple[str, ...] = (
    "candidate_names", "emails", "phones", "linkedins", "githubs", "portfolios", "addresses",
    "companies", "clients", "projects", "employee_ids",
    "pans", "aadhaars", "passports", "internal_ids",
)


def _empty_report() -> dict[str, list[str]]:
    return {key: [] for key in REPORT_KEYS}


# --- Personal / Contact (raw-text mode) -------------------------------------

# Any bare http(s) URL that ISN'T already a LinkedIn/GitHub link - the only
# other kind of URL a resume commonly has is the candidate's own portfolio/
# personal site. No existing extractor for this anywhere in the codebase
# (LinkedIn/GitHub are the only URL-shaped fields this pipeline has today),
# so this is new, but deliberately the simplest possible rule rather than a
# domain allowlist that would need constant upkeep.
_URL_RE = re.compile(r"https?://[^\s,;()]+", re.IGNORECASE)

# Label-anchored only (never a bare pattern) - a full postal address has no
# universal shape, so this only fires on an explicit "Address:"-style line,
# trading recall for precision exactly as the task asked.
_ADDRESS_LABEL_RE = re.compile(r"(?im)^\s*(?:address|residence|location)\s*:\s*(.+)$")


def _detect_portfolio_urls(raw_text: str) -> list[str]:
    linkedin_and_github = set(extract_all_linkedin_urls(raw_text)) | set(extract_all_github_urls(raw_text))
    found = []
    for match in _URL_RE.finditer(raw_text):
        url = match.group(0).rstrip(".,;)")
        if not any(url in known or known in url for known in linkedin_and_github):
            found.append(url)
    return found


def _detect_addresses(raw_text: str) -> list[str]:
    return [m.group(1).strip() for m in _ADDRESS_LABEL_RE.finditer(raw_text) if m.group(1).strip()]


# --- Optional Sensitive Information (label-anchored, both modes) -----------
#
# None of PAN/Aadhaar/Passport/Employee ID/Internal ID have a universal,
# self-evident shape the way an email address does - a bare pattern match
# for any of these risks matching an unrelated code/ID elsewhere on the
# resume (a certification ID, a ticket number). Every one of these is
# therefore anchored to an explicit label on the same line, maximizing
# precision at the cost of recall - exactly the tradeoff this task asked
# for. PAN is the one exception with a genuinely distinctive standalone
# format (5 letters + 4 digits + 1 letter is vanishingly unlikely to occur
# by chance in English resume text), so it's also matched unanchored.

_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR_LABELED_RE = re.compile(r"(?im)\b(?:aadhaar|aadhar|uid)\s*(?:no\.?|number)?\s*:?\s*([\d\s-]{12,17})")
# Unlabeled Aadhaar is only matched in its human-readable grouped-by-4 form
# (the near-universal way it's actually written/displayed) - a bare
# contiguous 12-digit run is NOT matched at all, since that shape is far
# too generic (could be almost any ID number) to attribute to Aadhaar
# without a label.
_AADHAAR_GROUPED_RE = re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}\b")
_PASSPORT_LABELED_RE = re.compile(r"(?im)\bpassport\s*(?:no\.?|number)?\s*:?\s*([A-Z][0-9]{7})\b")
_EMPLOYEE_ID_LABELED_RE = re.compile(
    r"(?im)\b(?:employee|emp\.?|associate)\s*(?:id|no\.?|number)\s*:?\s*([A-Za-z0-9-]{3,20})"
)
_INTERNAL_ID_LABELED_RE = re.compile(r"(?im)\binternal\s*id\s*:?\s*([A-Za-z0-9-]{3,20})")

# Client is label-anchored the same way (no reliable unlabeled pattern for
# an arbitrary company name), but the label itself IS a real, common
# convention confirmed across multiple real resumes this session (both a
# dedicated "Client:" project field and a "Note: Client: ..." experience-
# level note) - so, unlike Company/Project, this one IS safely detectable
# from raw text alone.
_CLIENT_LABELED_RE = re.compile(r"(?im)\bclient\s*:\s*(.+?)(?:\s*[(;]|$)")


def _labeled_matches(pattern: re.Pattern, text: str) -> list[str]:
    return [m.group(1).strip() for m in pattern.finditer(text) if m.group(1).strip()]


def detect_pii_in_text(raw_text: str) -> dict[str, list[str]]:
    """Detects PII directly from raw source text, before Stage 1 extraction
    (and therefore before the first Gemini call) has run. See module
    docstring for exactly which categories this can and can't reliably
    cover at this stage."""
    report = _empty_report()
    if not raw_text or not raw_text.strip():
        return report

    emails = extract_all_emails(raw_text)
    linkedins = extract_all_linkedin_urls(raw_text)
    # A 4-4-4 space/hyphen-grouped 12-digit run also satisfies contact.py's
    # generic phone-candidate shape (digit count 7-15) - but that exact
    # grouping is a far more distinctive Aadhaar convention than a phone
    # one, so it's excluded here (reported once, as Aadhaar, not twice)
    # rather than left ambiguous across both categories.
    phones = [p for p in extract_all_phones(raw_text) if not _AADHAAR_GROUPED_RE.fullmatch(p)]

    # resolve_candidate_name's own local (non-Gemini) signals still work
    # with gemini_name=None - see the module's own docstring: signal 1's
    # Gemini-header check is simply skipped, falling through to the
    # strict/loose header-line scans, which are pure raw_text logic.
    name = resolve_candidate_name(
        gemini_name=None, summary="", raw_text=raw_text,
        email=emails[0] if emails else None, linkedin=linkedins[0] if linkedins else None,
    )

    report["candidate_names"] = [name] if name else []
    report["emails"] = emails
    report["phones"] = phones
    report["linkedins"] = linkedins
    report["githubs"] = extract_all_github_urls(raw_text)
    report["portfolios"] = _detect_portfolio_urls(raw_text)
    report["addresses"] = _detect_addresses(raw_text)
    report["clients"] = _labeled_matches(_CLIENT_LABELED_RE, raw_text)
    report["employee_ids"] = _labeled_matches(_EMPLOYEE_ID_LABELED_RE, raw_text)
    report["internal_ids"] = _labeled_matches(_INTERNAL_ID_LABELED_RE, raw_text)
    report["pans"] = [m.group(0) for m in _PAN_RE.finditer(raw_text)]
    report["aadhaars"] = list(dict.fromkeys(
        _labeled_matches(_AADHAAR_LABELED_RE, raw_text) + [m.group(0) for m in _AADHAAR_GROUPED_RE.finditer(raw_text)]
    ))
    report["passports"] = _labeled_matches(_PASSPORT_LABELED_RE, raw_text)
    # companies/projects intentionally left [] - see module docstring.
    return report


# --- Company / Client / Project (structured mode) --------------------------

def detect_pii_in_structured(structured: dict) -> dict[str, list[str]]:
    """Detects Company/Client/Project names (plus anything the raw-text
    pass could also find, re-derived here for a self-contained report) from
    the ALREADY-EXTRACTED structured dict - exact field reads, not
    heuristic pattern matching, so this carries none of the free-text
    Company/Project precision risk detect_pii_in_text intentionally avoids."""
    report = _empty_report()
    if not structured:
        return report

    name = str(structured.get("name") or "")
    report["candidate_names"] = [name] if name else []
    email = str(structured.get("email") or "")
    report["emails"] = [email] if email else []
    phone = str(structured.get("phone") or "")
    report["phones"] = [phone] if phone else []
    linkedin = str(structured.get("linkedin") or "")
    report["linkedins"] = [linkedin] if linkedin else []
    github = str(structured.get("github") or "")
    report["githubs"] = [github] if github else []
    portfolio = str(structured.get("portfolio") or "")
    report["portfolios"] = [portfolio] if portfolio else []
    location = str(structured.get("location") or "")
    report["addresses"] = [location] if location else []

    companies: list[str] = []
    clients: list[str] = []
    employee_ids: list[str] = []
    for exp in structured.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        company = str(exp.get("company") or "").strip()
        if company and company.lower() != "career break":
            companies.append(company)
        notes = str(exp.get("notes") or "")
        clients.extend(_labeled_matches(_CLIENT_LABELED_RE, notes))
        employee_ids.extend(_labeled_matches(_EMPLOYEE_ID_LABELED_RE, notes))

    projects: list[str] = []
    for proj in structured.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        title = str(proj.get("title") or "").strip()
        if title:
            projects.append(title)
        client = str(proj.get("client") or "").strip()
        if client:
            clients.append(client)

    report["companies"] = list(dict.fromkeys(companies))
    report["clients"] = list(dict.fromkeys(clients))
    report["projects"] = list(dict.fromkeys(projects))
    report["employee_ids"] = list(dict.fromkeys(employee_ids))
    return report


def merge_pii_reports(*reports: dict[str, list[str]]) -> dict[str, list[str]]:
    """Combines multiple detection reports (e.g. detect_pii_in_text's
    pre-extraction pass and detect_pii_in_structured's post-extraction
    pass) into one, de-duplicating within each category while preserving
    first-seen order."""
    merged = _empty_report()
    for report in reports:
        for key in REPORT_KEYS:
            for value in report.get(key, []):
                if value not in merged[key]:
                    merged[key].append(value)
    return merged
