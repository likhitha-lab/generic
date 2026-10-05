"""PII Masking Engine - Phase 2 of the PII Masking/Unmasking architecture
(see reference_docs/PII_MASKING_ARCHITECTURE.md). Consumes a report from
app/services/pii_detector.py and replaces every detected value with a
deterministic placeholder. Not wired into the pipeline yet - a standalone,
reusable pair of operations (mask / unmask) that a future integration
would call around the Gemini boundary.

Design:
  build_mapping(pii_report) -> {real_value: "<PLACEHOLDER_N>", ...}
    One counter per placeholder category, assigned in REPORT_KEYS order,
    first-seen order within each category's list. If the exact same value
    string is detected under more than one category (a real case seen this
    session - the same company name appearing as both an Employment
    company and a Project's client), it keeps the placeholder from
    whichever category was assigned FIRST (REPORT_KEYS order) rather than
    getting a second, different placeholder - "same value -> same
    placeholder" is a stronger, literal requirement than "one placeholder
    type per category label."

  mask_text(text, mapping) / unmask_text(masked_text, mapping)
    Operate on a raw string.

  mask_structured(structured, mapping) / unmask_structured(...)
    Walk an already-extracted structured dict (or list of dicts) and mask/
    unmask every string value found anywhere inside it - not just the
    dedicated fields (name, experience[].company, ...) but also inline
    mentions inside free-text fields (a company name mentioned inside a
    bullet point, not just its own field).

The mapping itself is a plain dict, returned to the caller - this module
performs no I/O of its own (no file, no DB, no logging of a value), so
"store only in memory" and "never write to disk" hold simply because
there is nothing here that could do otherwise; enforcing that remains the
caller's responsibility once this is wired into the real pipeline.
"""
import logging
import re

from app.services.pii_detector import REPORT_KEYS

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIXES: dict[str, str] = {
    "candidate_names": "CANDIDATE_NAME",
    "emails": "EMAIL",
    "phones": "PHONE",
    "linkedins": "LINKEDIN",
    "githubs": "GITHUB",
    "portfolios": "PORTFOLIO",
    "addresses": "ADDRESS",
    "companies": "COMPANY",
    "clients": "CLIENT",
    "projects": "PROJECT",
    "employee_ids": "EMPLOYEE_ID",
    "pans": "PAN",
    "aadhaars": "AADHAAR",
    "passports": "PASSPORT",
    "internal_ids": "INTERNAL_ID",
}


def build_mapping(pii_report: dict[str, list[str]]) -> dict[str, str]:
    """Builds a deterministic real_value -> placeholder mapping from a
    pii_detector.py report. Same report in, same mapping out, every time -
    no randomness, no timestamps, no process-specific state."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    for category in REPORT_KEYS:
        prefix = _PLACEHOLDER_PREFIXES[category]
        for value in pii_report.get(category, []):
            if not value or value in mapping:
                continue
            counters[prefix] = counters.get(prefix, 0) + 1
            mapping[value] = f"<{prefix}_{counters[prefix]}>"
    return mapping


def _ordered_by_length_desc(values) -> list[str]:
    # Longer values first, so a shorter value that happens to be a
    # substring of a longer one (e.g. a bare company name that's also a
    # substring of a longer project title) never gets replaced FIRST and
    # corrupts the longer match's own replacement.
    return sorted(values, key=len, reverse=True)


def mask_text(text: str, mapping: dict[str, str]) -> str:
    """Replaces every occurrence of every mapped value with its
    placeholder. Never partial: each match is the FULL literal value, atomically
    swapped for its whole placeholder token in one substitution - there is
    no code path that replaces only part of a detected value. Never inside
    a larger word: `(?<!\\w)`/`(?!\\w)` require the character immediately
    before/after the match (if any) to NOT be a word character - this is
    deliberately not a `\\b...\\b` pair, since `\\b` itself fails to anchor
    at all when the value's own first/last character is already a
    non-word character (a phone number starting with "+", for example) -
    that would silently skip masking exactly the values most likely to
    start with a non-alnum character. Text outside a matched span is never
    touched. Exact character positions are NOT preserved (impossible in
    general - a placeholder is a different length than the value it
    replaces) - what IS preserved is precise, non-destructive span
    replacement: every match is resolved against the literal value's own
    real boundaries, never a blind/approximate string operation that could
    shift or corrupt unrelated text."""
    if not text or not mapping:
        return text
    for value in _ordered_by_length_desc(mapping):
        pattern = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
        text = pattern.sub(mapping[value], text)
    return text


def unmask_text(masked_text: str, mapping: dict[str, str]) -> str:
    """Inverse of mask_text - replaces every placeholder token back to its
    real value using the same mapping. Placeholders are unique, fully
    bracket-delimited tokens (never a prefix of another placeholder - e.g.
    "<COMPANY_1>" is not a substring of "<COMPANY_10>", the digit that
    follows "1" differs), so a literal (non-regex) replace is safe;
    processed longest-placeholder-first regardless, as an extra guard."""
    if not masked_text or not mapping:
        return masked_text
    reverse = {placeholder: value for value, placeholder in mapping.items()}
    for placeholder in _ordered_by_length_desc(reverse):
        masked_text = masked_text.replace(placeholder, reverse[placeholder])
    return masked_text


def _walk(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [_walk(item, transform) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, transform) for key, item in value.items()}
    return value


def mask_structured(structured: dict, mapping: dict[str, str]) -> dict:
    """Masks every string value anywhere inside an already-extracted
    structured resume dict - top-level fields (name, email, ...) AND every
    nested experience/project field (bullets, notes, descriptions) - so an
    entity mentioned inline inside free text is masked too, not just the
    handful of fields pii_detector.detect_pii_in_structured itself reads.
    Returns a new dict/list structure; never mutates the input in place."""
    return _walk(structured, lambda text: mask_text(text, mapping))


def unmask_structured(structured: dict, mapping: dict[str, str]) -> dict:
    """Inverse of mask_structured."""
    return _walk(structured, lambda text: unmask_text(text, mapping))


# =============================================================================
# Verified unmasking - Phase 3 (final PII Unmasking layer)
#
# unmask_text/unmask_structured above are, and remain, the plain restore
# operation gemini_client.call_gemini already uses - unchanged, since
# nothing about how a KNOWN placeholder gets restored needed to change.
# What's new here is a REPORTING layer on top: detecting a placeholder-
# SHAPED token that ISN'T an exact match in this request's own mapping
# (a malformed variant, or a stray token that was never this request's to
# begin with), and checking the mapping dict itself for internal
# corruption - without ever guessing a substitution for either case.
# =============================================================================

# Canonical, VALID placeholder shape - exactly what build_mapping ever
# produces: an all-caps prefix (letters/underscores), an underscore, then
# a positive integer with no leading zero.
_VALID_PLACEHOLDER_RE = re.compile(r"^<[A-Z][A-Z_]*_[1-9][0-9]*>$")

# Broad "shaped like an attempted placeholder" detector - deliberately
# looser than _VALID_PLACEHOLDER_RE (allows lowercase, a missing
# underscore before the digits, missing digits entirely, a leading zero)
# so it also catches every malformed variant this task names as an
# example: <CANDIDATE_NAME>, <CANDIDATE_NAME_01>, <CANDIDATE_NAME1>,
# <CANDIDATE_name_1>.
_PLACEHOLDER_SHAPED_RE = re.compile(r"<[A-Za-z][A-Za-z0-9_]*>")


def _scan_unknown_placeholders(text: str, known_placeholders: set[str]) -> list[str]:
    found: list[str] = []
    for match in _PLACEHOLDER_SHAPED_RE.finditer(text or ""):
        token = match.group(0)
        if token not in known_placeholders and token not in found:
            found.append(token)
    return found


def find_unknown_placeholders(text: str, mapping: dict[str, str]) -> list[str]:
    """Scans `text` for anything shaped like a placeholder that is NOT an
    exact, known value in this mapping - a malformed variant (wrong case,
    missing/extra digits, no underscore) or a token belonging to a
    different request entirely. Never substitutes a guess for these -
    only detects, logs one warning naming the malformed TOKEN itself
    (never any original value - a malformed bracket token isn't sensitive
    data), and returns them so a caller can record/surface them.
    Order-preserving, de-duplicated. Satisfies "never restore a
    placeholder not generated by this request" by construction: only this
    mapping's own values are ever considered known."""
    found = _scan_unknown_placeholders(text, set(mapping.values()))
    if found:
        logger.warning(
            "Unmasking found %d placeholder-shaped token(s) not in this request's own mapping - "
            "left unchanged, never guessed at: %s", len(found), found,
        )
    return found


def validate_mapping_integrity(mapping: dict[str, str]) -> list[str]:
    """Checks the mapping dict itself for internal problems - never
    raises, returns a list of human-readable issue descriptions (empty =
    healthy):
      - Corrupted: a value that isn't a well-formed placeholder at all
        (build_mapping never produces this - seeing one means the dict was
        altered after build_mapping created it, not a normal state).
      - Duplicate: two different original values assigned the SAME
        placeholder string - would make restoration ambiguous. build_
        mapping's own "already assigned" check prevents this by
        construction; this is a defensive re-check for a hand-built or
        externally-modified mapping, not something normally expected to
        fire. ("Missing mappings" - a placeholder present in TEXT with no
        matching mapping entry - is a property of a specific piece of text
        together with the mapping, not the mapping alone; that case is
        find_unknown_placeholders's job, not this one's.)"""
    issues: list[str] = []
    seen_placeholders: dict[str, str] = {}
    for real_value, placeholder in mapping.items():
        if not _VALID_PLACEHOLDER_RE.match(placeholder):
            issues.append(f"Corrupted mapping entry: value maps to a malformed placeholder {placeholder!r}.")
            continue
        if placeholder in seen_placeholders:
            issues.append(
                f"Duplicate mapping: placeholder {placeholder!r} is assigned to more than one original value."
            )
        seen_placeholders[placeholder] = real_value
    return issues


def unmask_text_verified(masked_text: str, mapping: dict[str, str]) -> dict:
    """Full, verified unmasking of a single string. Restoration itself is
    unchanged - calls unmask_text as-is - this adds what a plain call
    can't tell a caller on its own: any placeholder-shaped token left
    unrestored (because it isn't an exact match in this mapping) and any
    integrity problem in the mapping itself. Returns
    {"text", "unknown_placeholders", "mapping_issues"}."""
    mapping_issues = validate_mapping_integrity(mapping)
    unknown = find_unknown_placeholders(masked_text, mapping)
    return {"text": unmask_text(masked_text, mapping), "unknown_placeholders": unknown, "mapping_issues": mapping_issues}


def _collect_strings(value, collector) -> None:
    if isinstance(value, str):
        collector(value)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, collector)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, collector)


def unmask_structured_verified(structured, mapping: dict[str, str]) -> dict:
    """Full, verified unmasking of an already-extracted structured dict -
    same idea as unmask_text_verified, applied recursively (nested lists/
    dicts, repeated placeholders across multiple fields, all covered).
    Restoration itself is unchanged - calls unmask_structured as-is - this
    only adds aggregated reporting: every placeholder-shaped token found
    ANYWHERE in the structure that isn't in this mapping (logged once, not
    once per field), plus mapping-integrity issues. Returns
    {"structured", "unknown_placeholders", "mapping_issues"}."""
    mapping_issues = validate_mapping_integrity(mapping)
    known = set(mapping.values())
    unknown: list[str] = []

    def _record(text: str) -> None:
        for token in _scan_unknown_placeholders(text, known):
            if token not in unknown:
                unknown.append(token)

    _collect_strings(structured, _record)
    if unknown:
        logger.warning(
            "Unmasking found %d placeholder-shaped token(s) across this structure not in this "
            "request's own mapping - left unchanged, never guessed at: %s", len(unknown), unknown,
        )
    return {
        "structured": unmask_structured(structured, mapping),
        "unknown_placeholders": unknown,
        "mapping_issues": mapping_issues,
    }
