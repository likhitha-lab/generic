"""Builds the PDF and DOCX resume files as in-memory bytes.

Architecture decision - why this replaces the original Google Drive upload:

The original backend (`upload_to_drive`) authenticated with
`google.auth.default()` and pushed every generated file into one hardcoded
shared Drive folder belonging to the developer's own Google account, then
returned a public `drive.google.com/uc?id=...` link. For a multi-user product
that means:
  - every user's resume (name, work history, contact info) lands in the
    developer's personal Drive - a real data-handling/privacy problem
  - it only works if that folder is manually shared "anyone with the link"
  - it needs a service account with Drive API scope wired into Cloud Run
    just to hand the user a file they uploaded seconds ago
  - concurrent users with the same name collide on the same temp filename,
    and temp files were never cleaned up

Instead, both generators here return raw bytes. The routers base64-encode
those bytes directly into the JSON response, and the frontend turns that
back into a Blob and triggers a normal browser download. No third-party
storage, no shared credentials, no persisted PII, nothing to clean up.

Presentation only - this module never changes what's IN `parsed` (no
skill, bullet, entry, or word is ever added, removed, or reworded here;
that's Stage 1/the Resume Intelligence Engine's job, not this one's). What
IS this module's job: how that same data is laid out on the page - section
ORDER, heading text, page-fit, typography, spacing, margins, and bullet
indentation - all a presentation choice, not a data change, kept
consistent between the PDF and DOCX builders.

Section render order (see _DEFAULT_SECTION_SEQUENCE and the render loop in
build_pdf_bytes/build_docx_bytes) is the SINGLE STANDARD sequence approved
by the Talent Acquisition team, used for every generated resume regardless
of candidate experience, role, or seniority - Contact, Professional
Summary, Technical Skills, Educational Qualifications, Certifications,
Tools, Professional Experience, Project Details, Achievements, Languages,
Publications, Volunteer Experience, Leadership - rather than the arbitrary
order Stage 1's schema happens to define its dict keys in, any
dict/model_dump() iteration order, or (as of this revision) any adaptive/
seniority-based layout. Both builders iterate this one sequence
explicitly; a section is skipped only when its data is empty, and no
section is ever rendered twice. This governs render order only - the
underlying `parsed` dict's own key order is irrelevant and untouched. The
5 sections after Project Details are the newest additions - they render
exactly like Certifications/Tools (a heading + bullet list, entirely
skipped when empty), so a resume that doesn't have them renders IDENTICALLY
to before this addition.

ATS-safe formatting throughout: no tables (the previous version used one
for the company/date row - removed, since some ATS parsers drop or
reorder table-cell content entirely), no text boxes, no icons, no
multi-column layout, no images used to convey text (the logo is a fixed
brand mark unrelated to resume content, not a substitute for a text
heading). Every heading is real, distinctly-styled text (DOCX: actual
Heading 1/2/3 paragraph styles; PDF: consistently bold/larger paragraph
styles), and section headings use plain, standard names ("Skills",
"Experience", "Education", ...) rather than decorative or non-standard
phrasing, so both a human recruiter and a keyword-scanning ATS parser see
the same clean structure.

Page-count fitting (see _select_style_tier/_PDF_STYLE_TIERS): resumes for
candidates with more than 10 years of experience (estimated from the
year(s) mentioned in each job's `duration` string) are allowed up to 3
pages; everyone else is capped at 2 - unless a caller supplies
`max_pages_override` (see Phase 4 below), which takes priority over this
default entirely. PDF fitting is exact - ReportLab reports the real
rendered page count after each build attempt (`doc.page`), so this renders
once per style tier (normal, then compact if needed) and keeps the first
result that actually fits. DOCX has no equivalent: python-docx produces
XML only, with no rendering/pagination step and therefore no way to ask
"how many pages would Word actually show" without a real Word/LibreOffice
engine, which this project doesn't depend on. DOCX instead picks its tier
from the same content-volume estimate used as PDF's starting guess (see
_estimate_content_volume) - the same signal, just without the PDF's
follow-up real-page-count verification. Neither builder ever drops or
truncates real content to force a fit - if even the most compact tier
still overflows the page cap, that tier's output is returned as the best
available result rather than deleting something the candidate actually put
on their resume.

build_pdf_bytes/build_docx_bytes/build_resume_file still accept three
OPTIONAL parameters - `section_plan` (an ordered list of (section_key,
label) pairs), `extra_content` (a dict of section_key -> derived content
for a synthetic section key not present in `parsed`), and
`max_pages_override` - left in place as a generic passthrough mechanism,
but as of this revision NO caller in this codebase supplies them:
resume_service.py no longer computes or passes a per-candidate adaptive
plan (see that module's own docstring), so every real call always falls
through to `_DEFAULT_SECTION_SEQUENCE` above - the single, non-adaptive,
Talent-Acquisition-approved order, used for every resume regardless of
experience, role, or seniority. app/services/resume_structuring.py (the
former adaptive Resume Structuring Engine) still exists but is no longer
invoked anywhere in the generation flow - disabled, not deleted, so it
remains available if adaptive layouts are ever reinstated, without any
resume_service.py/file_generator.py change needed to do so.
"""
import datetime
import io
import logging
import os
import re
from xml.sax.saxutils import escape as _xml_escape

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageTemplate,
    Paragraph,
    Table,
    TableStyle,
)

from app.core.config import BASE_DIR, settings
from app.utils.contact import extract_linkedin_url

logger = logging.getLogger(__name__)

# --- Font resolution (Calibri, with a safe fallback) ------------------------
#
# Calibri is a Microsoft/Monotype font, not one of ReportLab's 14 built-in
# standard PDF fonts (Helvetica/Times/Courier) - a PDF can only render it if
# an actual Calibri TTF file is registered from disk. This checks a small,
# explicit list of locations where a genuinely-licensed copy could exist -
# a deployer-provided `static/fonts/` directory first (the correct place to
# add one in production, since this codebase doesn't ship/redistribute
# Microsoft's font files itself), then the OS's own installed-fonts
# location (legitimate use of a copy already licensed on that machine) -
# and registers it with ReportLab if found. python-docx never needs this:
# DOCX only stores the font NAME ("Calibri"), and Word/LibreOffice resolves
# the actual glyphs from whatever's installed on the machine that OPENS the
# file, same as any other font reference in a .docx. If no Calibri file is
# found anywhere, the PDF falls back to Helvetica (metrically similar,
# always available, never crashes) and logs a warning - DOCX is unaffected
# either way, so the two formats can only mismatch in this fallback case,
# which is exactly why the warning exists.
_CALIBRI_SEARCH_DIRS = (
    str(BASE_DIR / "static" / "fonts"),
    r"C:\Windows\Fonts",
    "/usr/share/fonts/truetype/msttcorefonts",
    "/usr/share/fonts/truetype/calibri",
    "/Library/Fonts",
)
_CALIBRI_REGULAR_FILENAMES = ("calibri.ttf", "Calibri.ttf")
_CALIBRI_BOLD_FILENAMES = ("calibrib.ttf", "Calibri Bold.ttf", "Calibri-Bold.ttf")

_calibri_registered: bool | None = None  # resolved once, lazily, on first PDF build


def _find_font_file(filenames: tuple[str, ...]) -> str | None:
    for directory in _CALIBRI_SEARCH_DIRS:
        for filename in filenames:
            candidate = os.path.join(directory, filename)
            if os.path.exists(candidate):
                return candidate
    return None


def _resolve_pdf_fonts() -> tuple[str, str]:
    """Returns (regular_font_name, bold_font_name) for ReportLab to use -
    "Calibri"/"Calibri-Bold" if a real TTF was found and registered,
    otherwise "Helvetica"/"Helvetica-Bold". Registration only happens once
    per process (ReportLab raises if the same font name is registered
    twice)."""
    global _calibri_registered
    if _calibri_registered is None:
        regular_path = _find_font_file(_CALIBRI_REGULAR_FILENAMES)
        bold_path = _find_font_file(_CALIBRI_BOLD_FILENAMES)
        if regular_path and bold_path:
            try:
                pdfmetrics.registerFont(TTFont("Calibri", regular_path))
                pdfmetrics.registerFont(TTFont("Calibri-Bold", bold_path))
                _calibri_registered = True
            except Exception:
                logger.warning("Found Calibri font files but ReportLab failed to register them - "
                                "falling back to Helvetica for the PDF.", exc_info=True)
                _calibri_registered = False
        else:
            logger.warning(
                "Calibri font files not found (checked %s) - PDF will render with Helvetica instead. "
                "To use true Calibri in production, place licensed calibri.ttf/calibrib.ttf under %s.",
                _CALIBRI_SEARCH_DIRS, _CALIBRI_SEARCH_DIRS[0],
            )
            _calibri_registered = False
    if _calibri_registered:
        return "Calibri", "Calibri-Bold"
    return "Helvetica", "Helvetica-Bold"

# --- Shared, format-independent typography/layout constants ----------------
#
# Fixed typography spec (Talent Acquisition-approved formatting pass):
# Calibri throughout, 11pt body text, 14pt bold section headings, 20pt bold
# candidate name - these three sizes are CONSTANTS, never varied by style
# tier. The "normal"/"compact" style tiers below still exist (see
# build_pdf_bytes) for the same reason they always have - fitting a long
# candidate's resume within its page cap - but they now ONLY tighten
# whitespace (spacing, line height, entry gaps): font size and typeface are
# identical in both tiers, so a compact-tier resume is denser, never
# smaller-printed or different-looking.
_BODY_FONT_SIZE = 11
_HEADING_FONT_SIZE = 14
_NAME_FONT_SIZE = 20

# Slightly more generous than the previous 0.75in on every side - a cleaner,
# less cramped look - same margin for both formats and both style tiers, so
# tightening a long resume to fit its page cap never comes from shrinking
# the margins.
_MARGIN_INCHES = 0.85
_PDF_MARGIN = _MARGIN_INCHES * 72  # ReportLab works in points (72/inch)

# Layout constants for the canvas-drawn PDF header (name, contact line(s),
# divider, logo). Contact text is wrapped to fit `_PDF_LOGO_WIDTH +
# _PDF_LOGO_GAP` short of the page's usable width, so it can never run
# underneath the logo - that horizontal budget is reserved for the logo no
# matter how long the contact line or a URL in it is. The header's own
# sizing does NOT shrink by style tier - the candidate's name stays a fixed,
# prominent size regardless of how compact the body content needs to be.
_PDF_LOGO_WIDTH = 90
_PDF_LOGO_HEIGHT = 35
_PDF_LOGO_GAP = 12
_PDF_CONTACT_FONT_SIZE = 9.5
_PDF_CONTACT_LINE_HEIGHT = 13
_PDF_NAME_BASELINE_OFFSET = 42  # distance from page top to the name's baseline
_PDF_CONTACT_FIRST_BASELINE_OFFSET = 60  # distance from page top to first contact line's baseline
_PDF_DIVIDER_GAP = 10  # gap between last header line's baseline and the divider
_PDF_BODY_TOP_GAP = 30  # breathing room between the divider and the first body flowable

# Two style tiers, tried in order (see build_pdf_bytes): "normal" first,
# falling back to "compact" only if "normal" doesn't fit the page cap. Only
# SPACING/leading tightens between tiers - font size and typeface (see
# _BODY_FONT_SIZE/_HEADING_FONT_SIZE above) are fixed and identical in both.
#
# bullet_left_indent/bullet_indent (normal tier): 36pt/18pt - same 0.5in
# text-start / 0.25in bullet-to-text gap as the DOCX builder's matching
# tier (see _DOCX_STYLE_TIERS's own comment on where 0.5in/0.25in comes
# from), kept in lockstep so the two formats' bullets line up identically.
_PDF_STYLE_TIERS = [
    {
        "name": "normal",
        "leading": 15, "space_after": 6,
        "heading_space_before": 18, "heading_space_after": 8,
        "subheading_space_before": 10, "subheading_space_after": 4,
        "bullet_left_indent": 36, "bullet_indent": 18, "bullet_space_after": 4,
        "entry_gap": 12,
    },
    {
        "name": "compact",
        "leading": 13, "space_after": 3,
        "heading_space_before": 12, "heading_space_after": 5,
        "subheading_space_before": 7, "subheading_space_after": 2,
        "bullet_left_indent": 28.8, "bullet_indent": 14.4, "bullet_space_after": 2,
        "entry_gap": 7,
    },
]

# DOCX equivalent of the tiers above - a parallel table (rather than derived
# from the PDF one) since the two renderers need different units for some
# fields (Word's line_spacing is a multiplier, not an absolute leading
# value), but every SPACING value that's expressed in points is the exact
# same number as the matching PDF tier field above - the two are kept in
# lockstep on purpose (requirement: PDF and DOCX must be visually
# identical), not two independently-tuned tables.
#
# bullet_indent_in/bullet_hanging_indent_in (normal tier): 0.5in/0.25in -
# Word's own out-of-box "List Bullet" default (confirmed by inspecting
# word/numbering.xml's abstractNum in a real, professionally-formatted
# candidate resume: `w:ind w:left="720" w:hanging="360"` twips = exactly
# 0.5in/0.25in). Previously 0.35in/0.2in here, which produced a narrower
# bullet-to-text gap than that standard - widened to match.
_DOCX_STYLE_TIERS = [
    {
        "name": "normal",
        "line_spacing": 1.25,
        "space_after_pt": 6,
        "heading_space_before_pt": 18, "heading_space_after_pt": 8,
        "subheading_space_before_pt": 10, "subheading_space_after_pt": 4,
        "bullet_indent_in": 0.5, "bullet_hanging_indent_in": 0.25,
    },
    {
        "name": "compact",
        "line_spacing": 1.1,
        "space_after_pt": 3,
        "heading_space_before_pt": 12, "heading_space_after_pt": 5,
        "subheading_space_before_pt": 7, "subheading_space_after_pt": 2,
        "bullet_indent_in": 0.4, "bullet_hanging_indent_in": 0.2,
    },
]

# Professional, ATS-safe, widely-available sans-serif font for DOCX (Word's
# own modern default, so no font embedding/substitution risk) - matches the
# PDF builder's resolved font (see _resolve_pdf_fonts) so the two formats
# read as the same design instead of two different-looking documents. DOCX
# only ever stores this font NAME - see _resolve_pdf_fonts's own docstring
# for why DOCX needs no file-discovery step the way the PDF builder does.
_DOCX_FONT_NAME = "Calibri"
_DOCX_HEADING_COLOR = RGBColor(0x1A, 0x1A, 0x1A)  # near-black, not Word's default theme blue
_DOCX_NOTE_COLOR = RGBColor(0x59, 0x59, 0x59)

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)
# Real resumes commonly abbreviate a job's dates as "Aug'23"/"Nov'20"
# rather than a full 4-digit year - confirmed on a real candidate's resume
# where EVERY genuine job's duration used this shorthand, which _YEAR_RE
# alone can't see at all, computing 0 years of experience (and therefore
# the smallest page-cap bracket) for a candidate with a real 16+ year
# career. Bare "'23" is ambiguous only in the 1900s-vs-2000s sense -
# always resolved as 20XX here, a safe assumption for any resume someone
# is actively using today. Same fix, independently applied, in
# experience_intelligence.py's own years_of_experience.
# Straight AND curly apostrophe - a real resume's "Aug'23"-style shorthand
# PDF-extracts with a curly right-single-quote (U+2019), not a straight
# ASCII apostrophe, which a plain `'` alone silently never matched.
_APOSTROPHE_YEAR_RE = re.compile(r"['‘’](\d{2})\b")

# (section_key, label) pairs, rendered in this list's order - the SINGLE
# STANDARD section order/naming approved by the Talent Acquisition team,
# used for every generated resume regardless of candidate experience,
# role, or seniority (no adaptive/dynamic ordering - see
# resume_service.py, which no longer computes or passes a per-candidate
# `section_plan` at all; every caller effectively always uses this exact
# sequence now). Achievements/Languages/Publications/Volunteer Experience/
# Leadership render exactly like Certifications/Tools above (a heading +
# bullet list via add_bullet_section, entirely skipped whenever empty - see
# that function's own `if not items: return` guard) - a resume without this
# content renders IDENTICALLY to before these 5 were added.
_DEFAULT_SECTION_SEQUENCE: list[tuple[str, str]] = [
    ("summary", "Professional Summary"),
    ("skills", "Technical Skills"),
    ("education", "Educational Qualifications"),
    ("certifications", "Certifications"),
    ("tools", "Tools"),
    ("experience", "Professional Experience"),
    ("projects", "Project Details"),
    ("achievements", "Achievements"),
    ("languages", "Languages"),
    ("publications", "Publications"),
    ("volunteer_experience", "Volunteer Experience"),
    ("leadership", "Leadership"),
]

# Section keys that share the same underlying data (`parsed["skills"]`) but
# may be rendered under a different recruiter-appropriate label depending
# on the active section plan (e.g. "Core Expertise" for a Senior Engineer,
# "Architecture Skills" for an Architect, "Technology Portfolio" for a
# Manager) - see resume_structuring.py's section templates. The underlying
# skill list itself is never altered by relabeling; only the heading text
# differs.
_SKILLS_LIKE_KEYS = frozenset({
    "skills", "core_skills", "core_expertise", "core_competencies",
    "architecture_skills", "technology_portfolio",
})

# Section keys rendered with the structured job-entry layout
# (_experience_entry_flowables/_add_experience_entry) - "enterprise_experience"
# (Architect template) and "internships" (Fresher template) are the same
# underlying `parsed["experience"]` list under a different label, unless a
# plan's `extra_content` supplies an already-filtered subset (e.g. only the
# entries that look like internships).
_EXPERIENCE_SHAPED_KEYS = frozenset({"experience", "enterprise_experience", "internships"})

# Same idea for the structured project-entry layout - "major_projects"
# (Senior template) and "major_transformation_programs" (Architect
# template) both default to `parsed["projects"]` under a different label.
_PROJECT_SHAPED_KEYS = frozenset({"projects", "major_projects", "major_transformation_programs"})


def _section_items(key: str, parsed: dict, extra_content: dict | None):
    """Resolves what a given section KEY should render, in priority order:
    (1) an explicit override in `extra_content` (used for synthetic
    sections a plan derives that have no 1:1 `parsed` field, e.g.
    "leadership_achievements"), (2) the shared skills/experience/projects
    data for a relabeled-but-same-content key (see the key sets above), (3)
    a direct `parsed.get(key)` for anything else (tools/education/
    certifications/achievements/summary, or any key that happens to share
    its name with a `parsed` field). Returns None (section skipped, see
    add_bullet_section/add_skills_section's own emptiness checks) if
    nothing resolves."""
    extra_content = extra_content or {}
    if key in extra_content:
        return extra_content[key]
    if key in _SKILLS_LIKE_KEYS:
        # Prefer the Skill Intelligence Engine's grouped-by-category shape
        # (see skill_intelligence.build_technical_skills_grouped, wired in by
        # resume_optimizer.py as `parsed["skills_grouped"]`) when present and
        # non-empty - add_skills_section already renders a dict as category
        # sub-headings (see below), so this is the one place that decides
        # WHICH shape gets rendered; falls back to the flat `parsed["skills"]`
        # list for older persisted versions, the manual-entry flow, and
        # /api/render, none of which populate `skills_grouped`.
        grouped = parsed.get("skills_grouped")
        if isinstance(grouped, dict) and grouped:
            return grouped
        return parsed.get("skills")
    if key in _EXPERIENCE_SHAPED_KEYS:
        return parsed.get("experience")
    if key in _PROJECT_SHAPED_KEYS:
        return parsed.get("projects")
    return parsed.get(key)


def _years_of_experience(experience: list[dict] | None) -> float:
    """Estimated career span in years, from the years mentioned in each
    job's `duration` string (e.g. "2020-2024" -> 2020 and 2024; "Jan 2019 -
    Present" -> 2019 and this year; "Aug'23 - till date" -> 2023 and this
    year - see _APOSTROPHE_YEAR_RE). Deliberately a rough, free-text-driven
    estimate - it only decides which page-count cap applies (see
    _PAGE_BUDGET_BRACKETS), not anything else - and covers the whole
    calendar span including any career-break entries, not just active
    working years, since a break is still part of the candidate's overall
    timeline on the page."""
    current_year = datetime.date.today().year
    years: list[int] = []
    for exp in experience or []:
        duration = str(exp.get("duration") or "")
        if _PRESENT_RE.search(duration):
            years.append(current_year)
        years.extend(int(y) for y in _YEAR_RE.findall(duration))
        years.extend(2000 + int(y) for y in _APOSTROPHE_YEAR_RE.findall(duration))
    if not years:
        return 0.0
    return float(max(years) - min(years))


#  Years of experience -> page cap (Talent Acquisition-approved bracket):
#  under 5y -> 2 pages, 5-10y -> 3, 10-15y -> 4, 15+y -> 5. Each bracket's
#  own upper bound belongs to the NEXT tier up (i.e. "5-10 years" means
#  5 <= years < 10) - the ranges as given have ambiguous shared boundaries
#  (is exactly 5 years old or new tier?), so lower-bound-inclusive is the
#  one consistent, unambiguous reading. Anything under the lowest stated
#  bracket (a fresher, 0-2 years) gets the same 2-page floor as the 2-5y
#  tier - there's no bracket below it to fall through to, and 2 pages is
#  already the minimum a resume needs. experience_intelligence.py's
#  page_budget_for_years() computes this exact same bracket (kept as an
#  independent, self-contained reimplementation, not a cross-module import
#  - see that module's own docstring for why - so if these two ever need
#  to change, change both).
_PAGE_BUDGET_BRACKETS: tuple[tuple[float, int], ...] = (
    (5, 2), (10, 3), (15, 4),
)
_MAX_PAGE_BUDGET = 5


def _max_pages_allowed(experience: list[dict] | None, override: int | None = None) -> int:
    """Page cap from the candidate's years of experience (see
    _PAGE_BUDGET_BRACKETS) - unless `override` is given, in which case it
    wins outright."""
    if override is not None:
        return override
    years = _years_of_experience(experience)
    for threshold, pages in _PAGE_BUDGET_BRACKETS:
        if years < threshold:
            return pages
    return _MAX_PAGE_BUDGET


def _estimate_content_volume(parsed: dict) -> int:
    """Rough total-character count across every section that actually
    takes up page space - used to pick a starting PDF style tier (refined
    afterward by a real page-count check) and as DOCX's only signal, since
    DOCX has no equivalent real measurement available (see module
    docstring). Not exact (doesn't account for word-wrap/line-count, font
    metrics, etc.) - just a proxy for "roughly how much content is there."
    """
    chars = len(str(parsed.get("summary") or ""))

    skills = parsed.get("skills")
    if isinstance(skills, dict):
        chars += sum(len(str(item)) for items in skills.values() for item in items)
    else:
        chars += sum(len(str(item)) for item in (skills or []))

    for field in (
        "tools", "education", "certifications", "achievements",
        "languages", "publications", "volunteer_experience", "leadership",
    ):
        chars += sum(len(str(item)) for item in (parsed.get(field) or []))

    for exp in parsed.get("experience") or []:
        chars += len(str(exp.get("company") or "")) + len(str(exp.get("role") or ""))
        chars += sum(len(str(p)) for p in (exp.get("points") or []))

    for proj in parsed.get("projects") or []:
        chars += len(str(proj.get("title") or "")) + len(str(proj.get("description") or ""))
        chars += sum(len(str(r)) for r in (proj.get("responsibilities") or []))

    return chars


# Rough characters-per-page budget for the "normal" tier at A4 with a 2-page
# allowance - calibrated loosely, not derived from a precise typesetting
# model; only used to pick a reasonable STARTING tier; get this wrong and
# the PDF builder's real page-count check still corrects it, which is why
# it doesn't need to be exact.
_CHARS_PER_PAGE_NORMAL_TIER = 3200


def _select_style_tier(content_chars: int, max_pages: int) -> int:
    """Index into _PDF_STYLE_TIERS/_DOCX_STYLE_TIERS - 0 ("normal") if the
    content volume plausibly fits `max_pages` at the normal tier's
    character budget, otherwise 1 ("compact")."""
    budget = _CHARS_PER_PAGE_NORMAL_TIER * max_pages
    return 0 if content_chars <= budget else 1


# Recruiter-controlled visibility (display-only - see build_pdf_bytes/
# build_docx_bytes's own `visibility` parameter). Every flag defaults to
# True via `.get(key, True)` everywhere this is read, so a caller passing
# `None` (every caller before this feature existed, and every caller that
# still doesn't care) renders exactly as before - nothing here can ever
# remove data from `parsed`/`contact` themselves, only from what a given
# render pass chooses to draw.
_DEFAULT_VISIBILITY: dict = {
    "show_email": True, "show_phone": True, "show_linkedin": True,
    "show_address": True, "show_employment_dates": True,
}


def _contact_line(contact: dict, visibility: dict | None = None) -> str:
    """Header contact line: Email, Phone, Address, LinkedIn, GitHub,
    Portfolio, relocate/remote preference - each shown only if present in
    `contact` (GitHub/Portfolio/relocate/remote have no dedicated
    visibility flag yet - unconditionally shown when present, same as
    Address) AND not hidden by `visibility` where one exists (defaults to
    "show everything" - see _DEFAULT_VISIBILITY). No city/state/country
    beyond whatever `contact["location"]` already holds."""
    visibility = visibility or {}
    parts = []
    if visibility.get("show_email", True) and contact.get("email"):
        parts.append(contact["email"])
    if visibility.get("show_phone", True) and contact.get("phone"):
        parts.append(contact["phone"])
    if visibility.get("show_address", True) and contact.get("location"):
        parts.append(contact["location"])
    if visibility.get("show_linkedin", True):
        linkedin = extract_linkedin_url(contact.get("links"))
        if linkedin:
            parts.append(linkedin)
    if contact.get("github"):
        parts.append(contact["github"])
    if contact.get("portfolio"):
        parts.append(contact["portfolio"])
    if contact.get("open_to_relocate"):
        parts.append("Open to Relocation")
    if contact.get("open_to_remote"):
        parts.append("Open to Remote")
    return "   |   ".join(parts)


def _apply_visibility_to_parsed(parsed: dict, visibility: dict | None) -> dict:
    """Returns `parsed` UNCHANGED (same dict, same object) if employment
    dates are shown (the common case - no copy made at all). Otherwise
    returns a shallow-copied dict with a NEW `experience` list where every
    entry is itself a shallow copy with `duration` blanked - display-only:
    the original `parsed`/`experience`/entry dicts are never mutated, so a
    caller's own reference (e.g. what's about to be serialized into
    content_json) is completely unaffected regardless of what this
    returns. Called once, right before the section-rendering loop, so
    `_max_pages_allowed`/`_estimate_content_volume` (called earlier, on the
    real `parsed`) always see the TRUE duration for page-fit purposes even
    when dates are hidden from display."""
    visibility = visibility or {}
    if visibility.get("show_employment_dates", True):
        return parsed
    filtered = dict(parsed)
    filtered["experience"] = [{**exp, "duration": ""} for exp in (parsed.get("experience") or [])]
    return filtered


def _wrap_text_to_width(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    """Greedy word-wrap so contact info (including long URLs) never overflows
    into/under the logo. The previous code drew the whole contact line with a
    single unwrapped `canvas.drawString`, which is exactly what let long
    links run straight underneath the logo instead of wrapping above/before
    it."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _pdf_styles(tier: dict, usable_width: float) -> dict:
    """Custom styles, not the raw stylesheet ones - spacing sized from
    `tier` (see _PDF_STYLE_TIERS), typography (font family/size) fixed at
    the module-level constants (_BODY_FONT_SIZE/_HEADING_FONT_SIZE) so it
    never varies between style tiers. `usable_width` (the page's content
    width, margins already excluded) sizes the company/dates row - see
    _entry_header_flowable.

    Root cause of the "huge/inconsistent gaps" symptom (historical): the
    old code used `getSampleStyleSheet()`'s Heading2/Heading3 as-is (which
    already carry their own spaceBefore/spaceAfter) *and* appended manual
    Spacer flowables after every heading and section. Those two spacing
    sources stacked additively and inconsistently, producing uneven,
    oversized gaps. One source of truth for spacing - here, the paragraph
    styles - with every manual Spacer removed, is what fixed it.

    `keepWithNext=True` on the heading styles is what stops a heading from
    being orphaned alone at the bottom of a page (Platypus won't break the
    page directly after a paragraph with this flag).
    """
    base = getSampleStyleSheet()
    body_font, bold_font = _resolve_pdf_fonts()
    return {
        "normal": ParagraphStyle(
            "ResumeNormal", parent=base["Normal"], fontName=body_font,
            fontSize=_BODY_FONT_SIZE, leading=tier["leading"], spaceAfter=tier["space_after"],
        ),
        "heading2": ParagraphStyle(
            "ResumeHeading2", parent=base["Heading2"], fontName=bold_font,
            fontSize=_HEADING_FONT_SIZE, spaceBefore=tier["heading_space_before"],
            spaceAfter=tier["heading_space_after"], textColor=colors.black, keepWithNext=True,
        ),
        # "Sub-heading" level (company/career-break/project title) - bold,
        # but the SAME 11pt body size as everything else (requirement: the
        # entire document uses one body size; bold weight, not a larger
        # font, is what visually distinguishes an entry's title line).
        "heading3": ParagraphStyle(
            "ResumeHeading3", parent=base["Heading3"], fontName=bold_font,
            fontSize=_BODY_FONT_SIZE, leading=tier["leading"],
            spaceBefore=tier["subheading_space_before"],
            spaceAfter=tier["subheading_space_after"], textColor=colors.black, keepWithNext=True,
        ),
        # Company-name-left / dates-right entry header - see
        # _entry_header_flowable for how these two styles combine into one
        # right-aligned row (ReportLab's Paragraph flowable has no tab-stop
        # support at all - a `tabs=` ParagraphStyle kwarg and a literal
        # "\t" in the text are both silently ignored, confirmed by
        # measuring actual rendered word positions; that's a genuinely
        # different mechanism than python-docx's tab stops, which DO work,
        # which is why the DOCX builder's equivalent code doesn't need this).
        "entry_header": ParagraphStyle(
            "ResumeEntryHeader", parent=base["Heading3"], fontName=bold_font,
            fontSize=_BODY_FONT_SIZE, leading=tier["leading"],
            spaceBefore=tier["subheading_space_before"], spaceAfter=tier["subheading_space_after"],
            textColor=colors.black, keepWithNext=True,
        ),
        "entry_date": ParagraphStyle(
            "ResumeEntryDate", parent=base["Normal"], fontName=body_font,
            fontSize=_BODY_FONT_SIZE, leading=tier["leading"], alignment=TA_RIGHT,
        ),
        "note": ParagraphStyle(
            "ResumeNote", parent=base["Normal"], fontName=body_font,
            fontSize=_BODY_FONT_SIZE, leading=tier["leading"], textColor=colors.grey,
            spaceBefore=tier["subheading_space_after"],
        ),
        "bullet_left_indent": tier["bullet_left_indent"],
        "bullet_indent": tier["bullet_indent"],
        "bullet_space_after": tier["bullet_space_after"],
        "entry_gap": tier["entry_gap"],
        "usable_width": usable_width,
    }


def _entry_header_flowable(company: str, duration: str, styles: dict):
    """Company name left-aligned, dates right-aligned on one row.
    Implemented as a single-row, 2-column, borderless/paddingless Table -
    NOT because a Table is the "no tables" exception, but because the ATS
    concern that rule protects against is specific to DOCX's `<w:tbl>` XML
    (some ATS parsers special-case and mis-order real DOCX table
    structures). A ReportLab Table has no equivalent survival in the
    OUTPUT: the compiled PDF's content stream contains only absolutely-
    positioned text, identical to what any other layout technique would
    produce - a PDF-text-extracting parser cannot tell this row was
    authored as a Table rather than two hand-positioned strings, so there
    is no extra ATS risk here (confirmed by measuring the actual rendered
    word positions - both cells extract as plain, correctly-ordered text).
    Falls back to a single left-aligned Paragraph when there's no
    duration to show on the right at all."""
    company_html = f"<b>{_xml_escape(company)}</b>"
    if not duration:
        return Paragraph(company_html, styles["entry_header"])
    usable_width = styles["usable_width"]
    table = Table(
        [[Paragraph(company_html, styles["entry_header"]), Paragraph(_xml_escape(duration), styles["entry_date"])]],
        colWidths=[usable_width * 0.65, usable_width * 0.35],
    )
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _split_summary_into_points(summary: str) -> list[str]:
    """Splits the Professional Summary (still generated/scored elsewhere as
    one plain string - see resume_scoring_engine._score_summary's own
    identical sentence-split regex, reused here rather than reimplemented)
    into one bullet point per sentence, for the Talent Acquisition-approved
    bullet-style Summary. Rendering-only: the underlying summary string,
    its generation, and its scoring are all untouched - this only changes
    how the same text is laid out on the page."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s.strip()]


def _bullet_list(items: list, normal_style, tier_styles: dict) -> ListFlowable:
    """One consistent bullet-list builder for every list in the document
    (skills, tools, education, certifications, experience points, project
    responsibilities, achievements) - explicit, uniform indentation
    (bulletIndent/leftIndent) and inter-bullet spacing everywhere, instead
    of each call site getting ReportLab's un-tuned ListFlowable defaults."""
    return ListFlowable(
        [ListItem(Paragraph(str(item), normal_style), spaceAfter=tier_styles["bullet_space_after"]) for item in items],
        bulletType="bullet",
        leftIndent=tier_styles["bullet_left_indent"],
        bulletIndent=tier_styles["bullet_indent"],
    )


def _experience_entry_flowables(exp: dict, styles: dict) -> list:
    """Build the flowable list for one job or career-break entry.

    Layout for a normal job:
        Company Name                                   Dates      <- right-aligned dates, see _entry_header_flowable
        Role
        • bullet
        • bullet
        Reason for leaving: ...   (if present)
        Note: ...                 (if present)

    Layout for a career break (`is_career_break`):
        Career Break
        UI/UX Firm                                                <- break_detail
        • description
    """
    normal, heading3, note = styles["normal"], styles["heading3"], styles["note"]
    entry: list = []

    if exp.get("is_career_break"):
        entry.append(Paragraph(exp.get("company") or "Career Break", heading3))
        if exp.get("break_detail"):
            entry.append(Paragraph(exp["break_detail"], normal))
    else:
        entry.append(_entry_header_flowable(exp.get("company", ""), exp.get("duration", ""), styles))
        if exp.get("role"):
            entry.append(Paragraph(exp["role"], normal))

    points = exp.get("points", [])
    if points:
        entry.append(_bullet_list(points, normal, styles))

    if exp.get("reason_for_leaving"):
        entry.append(Paragraph(f"Reason for leaving: {exp['reason_for_leaving']}", note))
    if exp.get("notes"):
        entry.append(Paragraph(f"Note: {exp['notes']}", note))

    return entry


def _project_has_content(proj: dict) -> bool:
    """True if a project has anything at all worth rendering. Belt-and-
    suspenders on top of normalize_parsed's own equivalent filter (which
    already drops fully-empty projects before this module ever sees them) -
    cheap to check again here so this renderer doesn't have to blindly trust
    that every caller went through normalization first."""
    return bool(
        proj.get("title") or proj.get("role") or proj.get("description")
        or proj.get("technologies") or (proj.get("responsibilities") or [])
    )


def _project_entry_flowables(proj: dict, styles: dict) -> list:
    """Project Handled layout: Project Name, Role, Description, Technologies,
    Responsibilities (bullets) - each shown only if present."""
    normal, heading3 = styles["normal"], styles["heading3"]
    entry: list = [Paragraph(proj.get("title", ""), heading3)]

    if proj.get("role"):
        entry.append(Paragraph(proj["role"], normal))
    if proj.get("client"):
        entry.append(Paragraph(f"Client: {proj['client']}", normal))
    if proj.get("description"):
        entry.append(Paragraph(proj["description"], normal))
    if proj.get("technologies"):
        entry.append(Paragraph(f"Technologies: {proj['technologies']}", normal))

    responsibilities = proj.get("responsibilities") or []
    if responsibilities:
        entry.append(_bullet_list(responsibilities, normal, styles))

    return entry


def _build_pdf_with_tier(
    parsed: dict, name: str, contact: dict, tier: dict,
    section_plan: list[tuple[str, str]] | None = None,
    extra_content: dict | None = None,
    visibility: dict | None = None,
) -> tuple[bytes, int]:
    """Renders the PDF once at the given style tier. Returns (bytes,
    actual_page_count) - the caller (build_pdf_bytes) decides whether that
    page count fits the candidate's page cap or another tier is needed.
    `section_plan`/`extra_content` default to None - see module docstring's
    Phase 4 paragraph for what they do and why omitting them reproduces the
    exact previous fixed-order behavior. `visibility` defaults to None
    (show everything - see _DEFAULT_VISIBILITY/_contact_line/
    _apply_visibility_to_parsed) - reproduces the exact previous behavior
    for every caller that doesn't pass one."""
    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4)
    page_width, page_height = A4
    usable_width = page_width - 2 * _PDF_MARGIN
    body_font, bold_font = _resolve_pdf_fonts()
    # Re-bind `parsed` to the display-filtered copy (a no-op, same object,
    # when dates are shown) BEFORE anything below reads it for rendering -
    # everything above this point (build_pdf_bytes's page-cap/style-tier
    # estimation) already ran on the real, unfiltered `parsed`.
    parsed = _apply_visibility_to_parsed(parsed, visibility)

    contact_line = _contact_line(contact, visibility)
    contact_max_width = usable_width - _PDF_LOGO_WIDTH - _PDF_LOGO_GAP
    contact_lines = (
        _wrap_text_to_width(contact_line, body_font, _PDF_CONTACT_FONT_SIZE, contact_max_width)
        if contact_line else []
    )

    # Compute the header's real height from what's actually in it (name +
    # however many contact lines it wrapped to) instead of a fixed guess, so
    # the divider - and therefore the frame below it - always sits below the
    # ENTIRE header block, even when contact info wraps to 2-3 lines. With a
    # single-line contact (the common case) this reduces to the exact same
    # numbers the old hardcoded layout used, so the default look is unchanged.
    if contact_lines:
        last_baseline_offset = _PDF_CONTACT_FIRST_BASELINE_OFFSET + (len(contact_lines) - 1) * _PDF_CONTACT_LINE_HEIGHT
    else:
        last_baseline_offset = _PDF_NAME_BASELINE_OFFSET
    divider_offset = max(_PDF_NAME_BASELINE_OFFSET + _PDF_LOGO_HEIGHT, last_baseline_offset + _PDF_DIVIDER_GAP)
    header_reserved = divider_offset + _PDF_BODY_TOP_GAP

    # Frame geometry - not a leading Spacer - is what has to reserve the
    # header's space, because Platypus re-lays the same frame out on every
    # page a story overflows onto. A Spacer flowable only affects wherever it
    # sits in the story once; it doesn't come back at the top of page 2, 3...
    # which is exactly why body text used to start underneath/inside the
    # header on every page after the first.
    frame = Frame(_PDF_MARGIN, _PDF_MARGIN, usable_width, page_height - _PDF_MARGIN - header_reserved, id="normal")

    def header(canvas, _doc):
        width, height = A4
        canvas.setFont(bold_font, _NAME_FONT_SIZE)
        # No "Resume"/placeholder fallback here on purpose - see
        # person_extractor.py: an unresolved name must render blank, never
        # a generic placeholder string.
        canvas.drawString(_PDF_MARGIN, height - _PDF_NAME_BASELINE_OFFSET, name or "")

        canvas.setFont(body_font, _PDF_CONTACT_FONT_SIZE)
        for i, line in enumerate(contact_lines):
            baseline_offset = _PDF_CONTACT_FIRST_BASELINE_OFFSET + i * _PDF_CONTACT_LINE_HEIGHT
            canvas.drawString(_PDF_MARGIN, height - baseline_offset, line)

        # Divider always below the entire header block (name + every
        # wrapped contact line + the logo), computed above - never a fixed
        # y-coordinate that a long contact line or a tall logo could poke
        # through.
        canvas.line(_PDF_MARGIN, height - divider_offset, width - _PDF_MARGIN, height - divider_offset)

        if os.path.exists(settings.LOGO_PATH):
            canvas.drawImage(
                settings.LOGO_PATH,
                width - _PDF_MARGIN - _PDF_LOGO_WIDTH,
                height - _PDF_NAME_BASELINE_OFFSET - _PDF_LOGO_HEIGHT,
                width=_PDF_LOGO_WIDTH,
                height=_PDF_LOGO_HEIGHT,
                preserveAspectRatio=True,
                mask="auto",
            )

    doc.addPageTemplates([PageTemplate(id="Resume", frames=frame, onPage=header)])

    styles = _pdf_styles(tier, usable_width)
    normal, heading2, heading3 = styles["normal"], styles["heading2"], styles["heading3"]
    elements = []

    def add_bullet_section(title: str, items: list | None):
        if not items:
            return
        # KeepTogether around heading + entire list would force the *whole*
        # list onto one page and could reintroduce a large blank gap for
        # long lists. keepWithNext on the heading is enough to stop it being
        # orphaned, while still letting a long list paginate naturally.
        elements.append(Paragraph(title, heading2))
        elements.append(_bullet_list(items, normal, styles))

    def add_skills_section(skills: dict | list | None, label: str = "Technical Skills"):
        if not skills:
            return
        elements.append(Paragraph(label, heading2))
        if isinstance(skills, dict):
            # Optimized/categorized shape (see resume_optimizer.py) - a
            # sub-heading per category, each with its own bullet list.
            for category, items in skills.items():
                if not items:
                    continue
                elements.append(Paragraph(str(category), heading3))
                elements.append(_bullet_list(items, normal, styles))
        else:
            # Flat list (manual Resume Generator, or the Skill Intelligence
            # Engine's own final output - see skill_intelligence.py).
            elements.append(_bullet_list(skills, normal, styles))

    def add_experience_section(label: str, entries: list[dict] | None):
        if not entries:
            return
        elements.append(Paragraph(label, heading2))
        last_index = len(entries) - 1
        for i, exp in enumerate(entries):
            entry = _experience_entry_flowables(exp, styles)
            # Each entry is short and bounded, so KeepTogether here is safe -
            # it either fits on the current page or moves whole to the next,
            # which is exactly "don't split a job/career-break across a page
            # break". The divider goes outside the KeepTogether since it's
            # just a visual separator, not part of the entry itself.
            elements.append(KeepTogether(entry))
            if i != last_index:
                elements.append(HRFlowable(
                    width="100%", thickness=0.5, color=colors.lightgrey,
                    spaceBefore=styles["entry_gap"], spaceAfter=styles["entry_gap"],
                ))

    def add_projects_section(label: str, entries: list[dict] | None):
        if not entries:
            return
        elements.append(Paragraph(label, heading2))
        for proj in entries:
            if not _project_has_content(proj):
                continue
            elements.append(KeepTogether(_project_entry_flowables(proj, styles)))

    # Section order/labels: the DEFAULT sequence (_DEFAULT_SECTION_SEQUENCE)
    # unless a caller supplies its own `section_plan` (Phase 4 - see module
    # docstring) - either way, dispatched by key through the same four
    # renderers above, never derived from `parsed`'s own key order, and a
    # section is skipped whenever its resolved content is empty (see each
    # renderer's own `if not ...: return` guard).
    for key, label in (section_plan or _DEFAULT_SECTION_SEQUENCE):
        items = _section_items(key, parsed, extra_content)
        if key == "summary":
            if items:
                elements.append(Paragraph(label, heading2))
                elements.append(_bullet_list(_split_summary_into_points(items), normal, styles))
        elif key in _SKILLS_LIKE_KEYS:
            add_skills_section(items, label)
        elif key in _EXPERIENCE_SHAPED_KEYS:
            add_experience_section(label, items)
        elif key in _PROJECT_SHAPED_KEYS:
            add_projects_section(label, items)
        else:
            add_bullet_section(label, items)

    doc.build(elements)
    return buffer.getvalue(), doc.page


def build_pdf_bytes(
    parsed: dict, name: str, contact: dict,
    section_plan: list[tuple[str, str]] | None = None,
    extra_content: dict | None = None,
    max_pages_override: int | None = None,
    visibility: dict | None = None,
) -> bytes:
    """Renders the PDF, automatically fitting it to the candidate's page
    cap (2 pages, or 3 for 10+ years of experience, or `max_pages_override`
    if given - see _max_pages_allowed) by trying each style tier in order
    and keeping the first one whose REAL rendered page count (ReportLab's
    `doc.page`, not an estimate) fits. Never truncates content to force a
    fit - if even the most compact tier still overflows, that tier's result
    is returned as the best available rendering. `section_plan`/
    `extra_content` default to None - see module docstring's Phase 4
    paragraph. `visibility` (show/hide email/phone/LinkedIn/address/
    employment dates - see _DEFAULT_VISIBILITY) defaults to None, meaning
    "show everything" - reproduces the exact previous behavior for every
    caller that doesn't pass one. Page-fit estimation below always uses
    the REAL `parsed` (untouched even when dates are hidden from display -
    see _apply_visibility_to_parsed's own docstring for why).
    """
    max_pages = _max_pages_allowed(parsed.get("experience"), max_pages_override)
    start_tier = _select_style_tier(_estimate_content_volume(parsed), max_pages)

    result_bytes = None
    for tier in _PDF_STYLE_TIERS[start_tier:]:
        result_bytes, page_count = _build_pdf_with_tier(
            parsed, name, contact, tier, section_plan=section_plan, extra_content=extra_content,
            visibility=visibility,
        )
        if page_count <= max_pages:
            return result_bytes
    return result_bytes


def _keep_with_next(paragraph) -> None:
    """Word's equivalent of ReportLab's `keepWithNext` - stops this paragraph
    from being the last thing on a page (i.e. stops a heading being orphaned
    at the bottom, or a job/project entry from splitting mid-block)."""
    paragraph.paragraph_format.keep_with_next = True


def _chain_keep_together(paragraphs: list) -> None:
    """Word has no single "keep this group of paragraphs together" flag like
    ReportLab's KeepTogether flowable - the equivalent is chaining keepNext
    on every paragraph but the last, so no page break can land between any
    of them."""
    for paragraph in paragraphs[:-1]:
        _keep_with_next(paragraph)


def _apply_docx_style_tier(document: Document, tier: dict) -> None:
    """Overrides Word's default Normal/Heading1/2/3 styles with explicit
    font, size, color, and spacing - matching the PDF builder's typography
    instead of leaving whatever Word's default theme happens to use (which
    varies in size and defaults to a bright theme blue for headings,
    visually inconsistent with the PDF). Font size is FIXED at the same
    module-level constants the PDF builder uses (_BODY_FONT_SIZE/
    _HEADING_FONT_SIZE/_NAME_FONT_SIZE) - never varied by `tier`, which now
    only supplies spacing/line-height/indentation, so a compact-tier resume
    is denser, never smaller-printed, in either format.
    """
    styles = document.styles

    normal = styles["Normal"]
    normal.font.name = _DOCX_FONT_NAME
    normal.font.size = Pt(_BODY_FONT_SIZE)
    normal.paragraph_format.space_after = Pt(tier["space_after_pt"])
    normal.paragraph_format.line_spacing = tier["line_spacing"]

    # Heading 1 (candidate name) is fixed at _NAME_FONT_SIZE; Heading 2
    # (section titles - "Professional Summary", "Technical Skills", ...)
    # fixed at _HEADING_FONT_SIZE; Heading 3 (company/project title) is
    # bold at the SAME _BODY_FONT_SIZE as everything else - one consistent
    # body size document-wide, bold weight is what distinguishes a title
    # line, not a larger font (matches the PDF builder's "heading3" style).
    heading_sizes = {"Heading 1": _NAME_FONT_SIZE, "Heading 2": _HEADING_FONT_SIZE, "Heading 3": _BODY_FONT_SIZE}
    heading_spacing = {
        "Heading 1": (0, tier["heading_space_after_pt"]),
        "Heading 2": (tier["heading_space_before_pt"], tier["heading_space_after_pt"]),
        "Heading 3": (tier["subheading_space_before_pt"], tier["subheading_space_after_pt"]),
    }
    for style_name, size in heading_sizes.items():
        heading_style = styles[style_name]
        heading_style.font.name = _DOCX_FONT_NAME
        heading_style.font.size = Pt(size)
        heading_style.font.color.rgb = _DOCX_HEADING_COLOR
        heading_style.font.bold = True
        space_before, space_after = heading_spacing[style_name]
        heading_style.paragraph_format.space_before = Pt(space_before)
        heading_style.paragraph_format.space_after = Pt(space_after)
        heading_style.paragraph_format.line_spacing = tier["line_spacing"]

    list_bullet = styles["List Bullet"]
    list_bullet.font.name = _DOCX_FONT_NAME
    list_bullet.font.size = Pt(_BODY_FONT_SIZE)
    list_bullet.paragraph_format.space_after = Pt(tier["space_after_pt"])
    list_bullet.paragraph_format.line_spacing = tier["line_spacing"]
    # Explicit, consistent bullet indentation - matches the PDF builder's
    # _bullet_list indentation in spirit (a clear hanging indent, not
    # whatever Word's "List Bullet" style defaults to, which can look
    # cramped or inconsistent with the rest of the document's margins).
    list_bullet.paragraph_format.left_indent = Inches(tier["bullet_indent_in"])
    list_bullet.paragraph_format.first_line_indent = Inches(-tier["bullet_hanging_indent_in"])


def _add_horizontal_rule(document, tier: dict) -> None:
    """DOCX equivalent of the PDF's HRFlowable divider between experience
    entries: an empty paragraph with a bottom border. Adds no visible text
    (so it can't confuse an ATS parser), just a thin rule."""
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(tier["space_after_pt"])
    paragraph.paragraph_format.space_after = Pt(tier["space_after_pt"] * 2)
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "BFBFBF")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _docx_logo_height_inches(target_width_in: float) -> float:
    """The DOCX equivalent of the PDF builder's own `preserveAspectRatio=
    True` (see the `canvas.drawImage(...)` call in _build_pdf_with_tier) -
    python-docx's `add_picture(width=, height=)` has no aspect-preserving
    mode of its own: it always deforms the image to exactly fill whatever
    width/height are passed. Passing the PDF's 90x35pt BOUNDING BOX
    dimensions directly (as a prior pass here did) stretches the logo,
    because the logo's own native pixel ratio is wider than that box's
    ratio - the PDF never actually renders it at the full 35pt height
    either, `preserveAspectRatio=True` there fits it width-first and
    leaves the box's remaining height empty. This computes the equivalent:
    width stays the PDF's own fixed value, height is derived from the
    logo file's actual pixel dimensions (read fresh each call - "highest
    available resolution", not a hardcoded pixel count that could go
    stale if the asset is ever replaced) so the two formats render the
    logo at the same true proportions."""
    with Image.open(settings.LOGO_PATH) as logo_image:
        native_width, native_height = logo_image.size
    return target_width_in * (native_height / native_width)


def _remove_table_borders(table) -> None:
    """Strips every visible grid line from a table - used only for the
    header's name/logo side-by-side layout table, which exists purely for
    positioning (so the logo can sit on the same line as the candidate
    name) and must never look like a visible table when rendered."""
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    table._tbl.tblPr.append(borders)


# Extra room (beyond the logo picture's own declared width) given to the
# header table's right/logo cell - see its own call site's comment for the
# real, confirmed clipping issue this fixes.
_LOGO_CELL_RIGHT_BUFFER_IN = 0.15


def _zero_cell_margins(cell) -> None:
    """Removes Word's default table-cell internal padding (~0.08in each
    side) from one cell - used only for the header's logo cell, so the
    logo picture (already sized to fill the cell's own declared width -
    see _LOGO_CELL_RIGHT_BUFFER_IN) has the full width to render in
    without default padding stealing space from it and pushing it toward
    the cell boundary."""
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for edge in ("left", "right"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:w"), "0")
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    tc_pr.append(margins)


def _add_experience_entry(document, exp: dict, usable_width: float) -> None:
    entry = []

    if exp.get("is_career_break"):
        heading = document.add_paragraph(exp.get("company") or "Career Break")
        heading.runs[0].bold = True
        entry.append(heading)
        if exp.get("break_detail"):
            entry.append(document.add_paragraph(exp["break_detail"]))
    else:
        header_paragraph = document.add_paragraph()
        header_paragraph.paragraph_format.tab_stops.add_tab_stop(usable_width, WD_TAB_ALIGNMENT.RIGHT)
        company_run = header_paragraph.add_run(exp.get("company", ""))
        company_run.bold = True
        if exp.get("duration"):
            header_paragraph.add_run(f"\t{exp['duration']}")
        entry.append(header_paragraph)
        if exp.get("role"):
            entry.append(document.add_paragraph(exp["role"]))

    # `or []`, not just the `.get(..., [])` default: Gemini can return
    # "points": null explicitly, and `.get` only falls back to the default
    # when the key is MISSING, not when its value is None - `for point in
    # None` would crash with TypeError. (In practice normalize_parsed
    # already guarantees a list by the time this runs, but this loop
    # shouldn't have to trust that to stay safe.)
    for point in (exp.get("points") or []):
        entry.append(document.add_paragraph(point, style="List Bullet"))

    if exp.get("reason_for_leaving"):
        note = document.add_paragraph(f"Reason for leaving: {exp['reason_for_leaving']}")
        note.runs[0].italic = True
        note.runs[0].font.size = Pt(_BODY_FONT_SIZE)
        note.runs[0].font.color.rgb = _DOCX_NOTE_COLOR
        entry.append(note)
    if exp.get("notes"):
        note = document.add_paragraph(f"Note: {exp['notes']}")
        note.runs[0].italic = True
        note.runs[0].font.size = Pt(_BODY_FONT_SIZE)
        note.runs[0].font.color.rgb = _DOCX_NOTE_COLOR
        entry.append(note)

    # Keep this one entry's paragraphs glued together across a page break,
    # same intent as the PDF builder's KeepTogether(entry).
    _chain_keep_together(entry)


def _add_project_entry(document, proj: dict) -> None:
    # `.add_run(text)` on a paragraph ALWAYS creates a run object, even for
    # empty text - unlike `document.add_paragraph(text)`, which only creates
    # a run when text is truthy. A project can legitimately have an empty
    # title and still survive normalize_parsed's filter (kept if any of
    # title/description/responsibilities is present), so indexing
    # `.runs[0]` after `add_paragraph("")` crashed with IndexError whenever
    # that happened. Creating the run explicitly and bolding it directly
    # sidesteps the empty-runs-list case entirely.
    title_paragraph = document.add_paragraph()
    title_run = title_paragraph.add_run(proj.get("title", ""))
    title_run.bold = True
    entry = [title_paragraph]

    if proj.get("role"):
        entry.append(document.add_paragraph(proj["role"]))
    if proj.get("client"):
        entry.append(document.add_paragraph(f"Client: {proj['client']}"))
    if proj.get("description"):
        entry.append(document.add_paragraph(proj["description"]))
    if proj.get("technologies"):
        entry.append(document.add_paragraph(f"Technologies: {proj['technologies']}"))
    # Same None-vs-missing-key gap as `_add_experience_entry`'s points loop.
    for responsibility in (proj.get("responsibilities") or []):
        entry.append(document.add_paragraph(responsibility, style="List Bullet"))

    _chain_keep_together(entry)


def _build_docx_with_tier(
    parsed: dict, name: str, contact: dict, tier: dict,
    section_plan: list[tuple[str, str]] | None = None,
    extra_content: dict | None = None,
    visibility: dict | None = None,
) -> bytes:
    # Same display-only filtering as the PDF builder (see
    # _apply_visibility_to_parsed/_contact_line) - `parsed` here is already
    # rebound to the filtered copy (a no-op when dates are shown) before
    # anything below reads it, so both renderers share one filtering rule.
    parsed = _apply_visibility_to_parsed(parsed, visibility)
    buffer = io.BytesIO()
    document = Document()
    _apply_docx_style_tier(document, tier)

    section = document.sections[0]
    # Real, confirmed mismatch this formatting pass fixes: python-docx's
    # Document() defaults to its built-in template's page size (US Letter,
    # 8.5x11in) - the PDF builder has always used A4 (see `from
    # reportlab.lib.pagesizes import A4` above), so the two formats were
    # rendering at DIFFERENT page dimensions (confirmed by inspecting a
    # real generated DOCX's `section.page_width` - 8.5in, not A4's
    # 8.27in). Matching A4 explicitly here is what "PDF and DOCX must be
    # visually identical" actually requires - same page size, not just the
    # same margins/fonts on top of two different-sized pages.
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    # Same margin as the PDF builder (_MARGIN_INCHES) on every side except
    # top, which additionally reserves room for the logo and the (now 20pt)
    # candidate name - explicit rather than left at Word's defaults: the
    # previous code only set the logo's *width* (1.2in) and let height
    # auto-scale to the source image's aspect ratio. A differently-shaped
    # logo (e.g. a tall/square one) could auto-scale taller than the
    # default header_distance (0.5in) and get clipped into/overlapping the
    # body text, since Word reserves a fixed header band independent of
    # what's actually drawn in it. Pinning both the picture height and
    # header_distance/top_margin removes that failure mode entirely
    # instead of relying on this one logo's proportions.
    section.left_margin = Inches(_MARGIN_INCHES)
    section.right_margin = Inches(_MARGIN_INCHES)
    section.bottom_margin = Inches(_MARGIN_INCHES)
    section.header_distance = Inches(0.5)
    section.top_margin = Inches(1.2)
    usable_width = section.page_width - section.left_margin - section.right_margin

    header = section.header
    contact_line = _contact_line(contact, visibility)

    if os.path.exists(settings.LOGO_PATH):
        # Two-column borderless table: Name/Contact on the left, Logo
        # right-aligned on the right - the only way to put the logo on
        # the SAME horizontal line as the Name in Word (a plain sequence
        # of header paragraphs can only ever stack vertically). This
        # table exists purely for that side-by-side positioning - no
        # visible border, no data-grid meaning - so it's unrelated to the
        # body's own "avoid tables" ATS-safety rule (see
        # _add_experience_entry's own comment on what that rule actually
        # protects against: real DOCX <w:tbl> data-table structures in the
        # rendered CONTENT some ATS parsers mis-order - a borderless,
        # textless-structure header layout scaffold isn't that).
        logo_width_in = _PDF_LOGO_WIDTH / 72
        header_table = header.add_table(rows=1, cols=2, width=usable_width)
        header_table.autofit = False
        left_cell, right_cell = header_table.cell(0, 0), header_table.cell(0, 1)
        # Right cell gets a bit more room than the logo's own bare width
        # (_LOGO_CELL_RIGHT_BUFFER_IN) - real, confirmed issue this fixes:
        # a cell sized to EXACTLY the picture's width left no room for
        # Word's own default cell padding (~0.08in each side, never
        # explicitly zeroed before this fix - see _zero_cell_margins
        # below, which now removes it too, belt-and-suspenders), so the
        # image sat flush against the cell/page boundary and its
        # right-hand portion (where the "DATAFLIX" wordmark sits) was
        # getting clipped. The extra width comes out of the left cell
        # (table's total width - and therefore the divider/margins/
        # everything else about the header - is unchanged), which visibly
        # shifts the right-aligned logo's rendered position left, without
        # touching the picture's own width/height/aspect ratio at all.
        right_cell.width = Inches(logo_width_in + _LOGO_CELL_RIGHT_BUFFER_IN)
        left_cell.width = usable_width - right_cell.width
        header_table.columns[0].width = left_cell.width
        header_table.columns[1].width = right_cell.width
        _remove_table_borders(header_table)
        _zero_cell_margins(right_cell)
        left_cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        right_cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP

        # No "Resume"/placeholder fallback here on purpose - see
        # person_extractor.py: an unresolved name must render blank, never
        # a generic placeholder string.
        name_heading = left_cell.paragraphs[0]
        name_heading.style = document.styles["Heading 1"]
        name_heading.add_run(name or "")
        _keep_with_next(name_heading)

        if contact_line:
            contact_paragraph = left_cell.add_paragraph(contact_line)
            # Same size as the PDF builder's header contact line
            # (_PDF_CONTACT_FONT_SIZE) - not the 11pt body size - so the
            # two formats' header blocks match.
            contact_paragraph.runs[0].font.size = Pt(_PDF_CONTACT_FONT_SIZE)

        logo_paragraph = right_cell.paragraphs[0]
        logo_run = logo_paragraph.add_run()
        # Aspect-ratio-preserving height (see _docx_logo_height_inches'
        # own docstring for why a fixed height copied from the PDF's
        # bounding box stretched the logo) - never independently-chosen
        # width/height values.
        logo_run.add_picture(
            settings.LOGO_PATH,
            width=Inches(logo_width_in),
            height=Inches(_docx_logo_height_inches(logo_width_in)),
        )
        logo_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    else:
        # No logo asset - the whole reason for the table (side-by-side
        # positioning with the logo) doesn't apply, so fall back to plain
        # stacked header paragraphs.
        name_heading = header.add_paragraph(name or "", style="Heading 1")
        _keep_with_next(name_heading)
        if contact_line:
            contact_paragraph = header.add_paragraph(contact_line)
            contact_paragraph.runs[0].font.size = Pt(_PDF_CONTACT_FONT_SIZE)

    # Same divider the PDF draws as a full-width line under its header
    # band (see the `canvas.line(...)` call in _build_pdf_with_tier) -
    # reuses _add_horizontal_rule as-is, positioned directly below the
    # table/paragraphs above exactly as it was before this change.
    _add_horizontal_rule(header, tier)

    def add_summary_section(label: str, summary: str | None):
        if not summary:
            return
        heading = document.add_heading(label, level=2)
        _keep_with_next(heading)
        for point in _split_summary_into_points(summary):
            document.add_paragraph(point, style="List Bullet")

    def add_skills_section(label: str, skills: dict | list | None):
        if not skills:
            return
        heading = document.add_heading(label, level=2)
        _keep_with_next(heading)
        if isinstance(skills, dict):
            # Optimized/categorized shape (see resume_optimizer.py) - a
            # sub-heading per category, each with its own bullet list.
            for category, items in skills.items():
                if not items:
                    continue
                category_heading = document.add_heading(str(category), level=3)
                _keep_with_next(category_heading)
                for item in items:
                    if item:
                        document.add_paragraph(str(item), style="List Bullet")
        else:
            # Flat list (manual Resume Generator, or the Skill Intelligence
            # Engine's own final output - see skill_intelligence.py).
            for item in skills:
                if item:
                    document.add_paragraph(str(item), style="List Bullet")

    def add_bullet_section(label: str, items: list | None):
        if not items:
            return
        heading = document.add_heading(label, level=2)
        _keep_with_next(heading)
        for item in items:
            if item:
                document.add_paragraph(str(item), style="List Bullet")

    def add_experience_section(label: str, entries: list[dict] | None):
        if not entries:
            return
        heading = document.add_heading(label, level=2)
        _keep_with_next(heading)
        last_index = len(entries) - 1
        for i, exp in enumerate(entries):
            _add_experience_entry(document, exp, usable_width)
            if i != last_index:
                _add_horizontal_rule(document, tier)

    def add_projects_section(label: str, entries: list[dict] | None):
        if not entries:
            return
        heading = document.add_heading(label, level=2)
        _keep_with_next(heading)
        for proj in entries:
            if not _project_has_content(proj):
                continue
            _add_project_entry(document, proj)

    # Section order/labels: the DEFAULT sequence (_DEFAULT_SECTION_SEQUENCE)
    # unless a caller supplies its own `section_plan` (Phase 4 - see module
    # docstring) - dispatched by key through the renderers above, same
    # pattern as the PDF builder just above. A section is skipped whenever
    # its resolved content is empty (see each renderer's own guard).
    for key, label in (section_plan or _DEFAULT_SECTION_SEQUENCE):
        items = _section_items(key, parsed, extra_content)
        if key == "summary":
            add_summary_section(label, items)
        elif key in _SKILLS_LIKE_KEYS:
            add_skills_section(label, items)
        elif key in _EXPERIENCE_SHAPED_KEYS:
            add_experience_section(label, items)
        elif key in _PROJECT_SHAPED_KEYS:
            add_projects_section(label, items)
        else:
            add_bullet_section(label, items)

    document.save(buffer)
    return buffer.getvalue()


def build_docx_bytes(
    parsed: dict, name: str, contact: dict,
    section_plan: list[tuple[str, str]] | None = None,
    extra_content: dict | None = None,
    max_pages_override: int | None = None,
    visibility: dict | None = None,
) -> bytes:
    """Renders the DOCX at a style tier chosen from the same content-volume
    estimate used as the PDF builder's starting guess (see module
    docstring for why DOCX can't get the PDF's follow-up real-page-count
    verification - there's no page-count oracle for a DOCX file without an
    actual Word/LibreOffice rendering engine). `section_plan`/
    `extra_content`/`max_pages_override` default to None - see module
    docstring's Phase 4 paragraph. `visibility` defaults to None (show
    everything) - see build_pdf_bytes's matching parameter.
    """
    max_pages = _max_pages_allowed(parsed.get("experience"), max_pages_override)
    tier_index = _select_style_tier(_estimate_content_volume(parsed), max_pages)
    return _build_docx_with_tier(
        parsed, name, contact, _DOCX_STYLE_TIERS[tier_index],
        section_plan=section_plan, extra_content=extra_content, visibility=visibility,
    )


# (format name, content type) - used by routers so they don't hardcode MIME
# types or duplicate the "which builder for which format" branching.
_FORMAT_BUILDERS = {
    "pdf": (build_pdf_bytes, "application/pdf"),
    "docx": (
        build_docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


def build_resume_file(
    parsed: dict, name: str, contact: dict, file_format: str,
    section_plan: list[tuple[str, str]] | None = None,
    extra_content: dict | None = None,
    max_pages_override: int | None = None,
    visibility: dict | None = None,
) -> tuple[bytes, str, str]:
    """Build a single resume file and return (bytes, filename, content_type).

    Only ever builds the one requested format - callers that need both PDF
    and DOCX call this twice (once per format), rather than this module
    always building both regardless of what's needed. `section_plan`/
    `extra_content`/`max_pages_override` default to None - see module
    docstring's Phase 4 paragraph; a caller that never passes these (every
    caller before Phase 4) gets the exact same fixed layout as before.
    `visibility` defaults to None (show everything) - see build_pdf_bytes's
    matching parameter; a caller that never passes one renders exactly as
    it always has.
    """
    if file_format not in _FORMAT_BUILDERS:
        raise ValueError(f"Unsupported format: {file_format!r}")

    builder, content_type = _FORMAT_BUILDERS[file_format]
    file_bytes = builder(
        parsed, name, contact,
        section_plan=section_plan, extra_content=extra_content, max_pages_override=max_pages_override,
        visibility=visibility,
    )

    # A download needs SOME filename (unlike the rendered document's name
    # field, this can't just be blank) - "document" rather than "resume",
    # since "Resume"/"Resume.pdf" are explicitly disallowed placeholder
    # outputs (see person_extractor.py).
    safe_name = (name or "document").strip().replace(" ", "_") or "document"
    filename = f"{safe_name}.{file_format}"

    return file_bytes, filename, content_type
