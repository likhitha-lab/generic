"""Pre-Gemini validation of the raw extracted text.

Checks whether the fields/sections a resume is expected to have (Contact,
Email, Phone, LinkedIn, Skills, Experience, Education, Certifications,
Projects) actually appear in raw_text before a Gemini call is spent on it -
if the content genuinely isn't in the extracted text, that loss already
happened at extraction time (docx_parser.py/pdf_parser.py), not in Gemini,
and no prompt/model change downstream could recover it anyway.

This module never changes HOW text is extracted - it only inspects the
result and, via reader.py, decides whether extraction is worth retrying.
"""
import logging
import re

from app.core.config import settings
from app.utils.contact import extract_email, extract_linkedin_url, extract_phone

logger = logging.getLogger(__name__)

# Keyword presence checks for the sections that have no dedicated regex
# (unlike email/phone/linkedin, which utils/contact.py already parses
# precisely). Deliberately loose (a false "found" from the word
# "experience" appearing in running prose is fine - this is a pre-Gemini
# sanity check, not a strict section parser) since the cost of a false
# negative (an unnecessary retry) is much lower than the cost of a false
# positive (skipping a retry that would have helped).
_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "skills": ("skill", "proficient", "technical expertise", "technologies"),
    "experience": ("experience", "employment", "work history"),
    "education": ("education", "university", "college", "degree", "b.tech", "bachelor", "master"),
    "certifications": ("certif", "license", "credential"),
    "projects": ("project",),
}

# A plausible "Firstname Lastname"-shaped line near the top of the document -
# used only to check that SOME name-like line exists at all (the actual name
# Gemini/normalization.py ends up using is decided later, independently -
# this is just "is there anything here that looks like a contact line").
_NAME_LINE_RE = re.compile(r"^[A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+){1,3}$")

# Fixed field order, shared with scorer.py's weight table - keep the two in
# sync (scorer.py asserts its weights cover exactly this set).
FIELD_ORDER = ("contact", "email", "phone", "linkedin", "skills", "experience", "education", "certifications", "projects")


class SectionValidationResult:
    """`present` maps every field in FIELD_ORDER to whether it was found;
    `missing` is the same information as an ordered list of names, for
    logging/comparison convenience."""

    def __init__(self, present: dict[str, bool]):
        self.present = present
        self.missing = [name for name in FIELD_ORDER if not present.get(name)]

    @property
    def is_complete(self) -> bool:
        return not self.missing


def _has_contact_name_line(raw_text: str) -> bool:
    for line in raw_text.splitlines()[:10]:
        if _NAME_LINE_RE.match(line.strip()):
            return True
    return False


def validate_sections(raw_text: str) -> SectionValidationResult:
    """Checks `raw_text` for Contact/Email/Phone/LinkedIn (via the same
    regexes utils/contact.py uses for its own post-Gemini fallback) and
    Skills/Experience/Education/Certifications/Projects (via keyword
    presence). Always logs a warning when something's missing (a real,
    always-relevant signal, not just a debug detail); the full per-field
    FOUND/MISSING breakdown is only logged under DEBUG_RESUME_PIPELINE,
    same as the rest of this pipeline's verbose logs.
    """
    lowered = raw_text.lower()
    present = {
        "contact": _has_contact_name_line(raw_text),
        "email": bool(extract_email(raw_text)),
        "phone": bool(extract_phone(raw_text)),
        "linkedin": bool(extract_linkedin_url(raw_text)),
    }
    for section, keywords in _SECTION_KEYWORDS.items():
        present[section] = any(keyword in lowered for keyword in keywords)

    result = SectionValidationResult(present)

    if settings.DEBUG_RESUME_PIPELINE:
        logger.info(
            "========== SECTION VALIDATION (before Gemini) ==========\n%s",
            "\n".join(f"  {name}: {'FOUND' if present[name] else 'MISSING'}" for name in FIELD_ORDER),
        )
    if result.missing:
        logger.warning("Section validation found missing field(s) before Gemini: %s", result.missing)

    return result
