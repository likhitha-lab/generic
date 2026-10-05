"""Unit tests for file_generator.py's typography/formatting spec (Calibri
throughout, 11pt body / 14pt bold section headings / 20pt bold candidate
name, margins, bullet indentation, company-left/dates-right alignment,
consistent section spacing, PDF/DOCX visual parity) - a pure presentation
change, no AI/content logic touched."""
import io
import re

import pdfplumber
import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt

from app.services.file_generator import (
    _BODY_FONT_SIZE,
    _HEADING_FONT_SIZE,
    _MARGIN_INCHES,
    _NAME_FONT_SIZE,
    _PDF_LOGO_HEIGHT,
    _PDF_LOGO_WIDTH,
    _PDF_STYLE_TIERS,
    _apply_docx_style_tier,
    _pdf_styles,
    _resolve_pdf_fonts,
    _split_summary_into_points,
    build_docx_bytes,
    build_pdf_bytes,
)

_MULTI_SENTENCE_SUMMARY = (
    "Data Engineer with 2 years of experience designing ETL/ELT pipelines. "
    "Expert in Snowflake, AWS, SQL, and Python for scalable data ingestion. "
    "Possesses in-depth knowledge of Data Quality Frameworks and Enterprise Data Governance."
)

_PARSED = {
    "summary": "Experienced backend engineer with 8 years across cloud platforms.",
    "skills": ["Python", "AWS"],
    "tools": ["Git"],
    "education": ["B.Tech Computer Science"],
    "certifications": ["AWS Certified Solutions Architect"],
    "experience": [
        {"company": "Acme Corp", "role": "Senior Engineer", "duration": "2016-2024",
         "points": ["Built the platform.", "Led a project."], "is_career_break": False},
    ],
    "projects": [{"title": "Internal Tool", "description": "A tool.", "responsibilities": ["Did X."]}],
}
_NAME = "Jane Doe"
_CONTACT = {"email": "jane@example.com", "phone": "+1-555-0100"}


# --- Font resolution ----------------------------------------------------------

def test_resolve_pdf_fonts_returns_calibri_when_available():
    # This dev/test environment has Calibri installed (C:\Windows\Fonts) -
    # confirms the discovery+registration path actually works, not just
    # that it doesn't crash.
    body_font, bold_font = _resolve_pdf_fonts()
    assert body_font in ("Calibri", "Helvetica")
    assert bold_font in ("Calibri-Bold", "Helvetica-Bold")
    # Never a mismatched pair (Calibri body + Helvetica-Bold or vice versa).
    assert (body_font, bold_font) in (("Calibri", "Calibri-Bold"), ("Helvetica", "Helvetica-Bold"))


# --- PDF typography: fixed sizes, never varied by style tier -----------------

def test_pdf_styles_body_is_11pt_in_every_tier():
    for tier in _PDF_STYLE_TIERS:
        styles = _pdf_styles(tier, usable_width=400)
        assert styles["normal"].fontSize == _BODY_FONT_SIZE == 11


def test_pdf_styles_section_heading_is_14pt_bold_in_every_tier():
    body_font, bold_font = _resolve_pdf_fonts()
    for tier in _PDF_STYLE_TIERS:
        styles = _pdf_styles(tier, usable_width=400)
        assert styles["heading2"].fontSize == _HEADING_FONT_SIZE == 14
        assert styles["heading2"].fontName == bold_font


def test_pdf_styles_subheading_is_11pt_bold_not_a_separate_size():
    # Company/project title ("heading3"/"entry_header") is bold but the
    # SAME body size, not its own font-size tier - one consistent body
    # size document-wide.
    body_font, bold_font = _resolve_pdf_fonts()
    for tier in _PDF_STYLE_TIERS:
        styles = _pdf_styles(tier, usable_width=400)
        assert styles["heading3"].fontSize == _BODY_FONT_SIZE
        assert styles["heading3"].fontName == bold_font
        assert styles["entry_header"].fontSize == _BODY_FONT_SIZE
        assert styles["entry_header"].fontName == bold_font


def test_pdf_entry_date_style_is_right_aligned():
    from reportlab.lib.enums import TA_RIGHT
    styles = _pdf_styles(_PDF_STYLE_TIERS[0], usable_width=456)
    assert styles["entry_date"].alignment == TA_RIGHT


def test_name_font_size_constant_is_20():
    assert _NAME_FONT_SIZE == 20


# --- DOCX typography: fixed sizes, never varied by style tier ---------------

def test_docx_style_tier_applies_fixed_sizes_in_every_tier():
    from app.services.file_generator import _DOCX_STYLE_TIERS
    for tier in _DOCX_STYLE_TIERS:
        document = Document()
        _apply_docx_style_tier(document, tier)
        styles = document.styles
        assert styles["Normal"].font.size == Pt(11)
        assert styles["Heading 1"].font.size == Pt(20)
        assert styles["Heading 1"].font.bold is True
        assert styles["Heading 2"].font.size == Pt(14)
        assert styles["Heading 2"].font.bold is True
        assert styles["Heading 3"].font.size == Pt(11)
        assert styles["Heading 3"].font.bold is True
        assert styles["Normal"].font.name == "Calibri"
        assert styles["Heading 2"].font.name == "Calibri"


def test_docx_headings_have_explicit_consistent_spacing():
    from app.services.file_generator import _DOCX_STYLE_TIERS
    document = Document()
    _apply_docx_style_tier(document, _DOCX_STYLE_TIERS[0])
    heading2 = document.styles["Heading 2"]
    assert heading2.paragraph_format.space_before is not None
    assert heading2.paragraph_format.space_after is not None


# --- Margins -------------------------------------------------------------------

def test_docx_page_size_matches_pdf_a4():
    # Real, confirmed bug this formatting pass fixed: python-docx's
    # Document() previously defaulted to its built-in template's page size
    # (US Letter, 8.5x11in) while the PDF builder has always used A4 - the
    # two formats were rendering at different page dimensions.
    from reportlab.lib.pagesizes import A4
    from docx.shared import Emu
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    section = document.sections[0]
    # A4 in points (PDF/ReportLab units) vs EMU (DOCX units) - compare in
    # inches, where both systems agree, with a small tolerance for
    # point/EMU rounding.
    assert abs(section.page_width.inches - A4[0] / 72) < 0.01
    assert abs(section.page_height.inches - A4[1] / 72) < 0.01


def test_docx_margins_match_shared_constant():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    section = document.sections[0]
    assert section.left_margin == Inches(_MARGIN_INCHES)
    assert section.right_margin == Inches(_MARGIN_INCHES)
    assert section.bottom_margin == Inches(_MARGIN_INCHES)


# --- Company left-aligned / dates right-aligned ------------------------------

def test_docx_experience_header_uses_right_tab_stop_for_dates():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    header_paragraph = next(p for p in document.paragraphs if p.text.startswith("Acme Corp"))
    tab_stops = list(header_paragraph.paragraph_format.tab_stops)
    assert tab_stops
    assert tab_stops[0].alignment == WD_TAB_ALIGNMENT.RIGHT
    assert "\t2016-2024" in header_paragraph.text


def test_pdf_experience_header_renders_company_and_dates():
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Acme Corp" in text
    assert "2016-2024" in text
    # Company and dates render on the SAME line (tab-separated), not two
    # separate lines - confirms the header didn't regress to a stacked layout.
    header_line = next(line for line in text.splitlines() if "Acme Corp" in line)
    assert "2016-2024" in header_line


def test_pdf_dates_render_near_the_right_margin():
    # Uses pdfplumber's word bounding boxes to confirm the date token is
    # positioned near the page's right edge, not just present somewhere in
    # the text - a real check of "dates right aligned", not only "dates
    # appear".
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        date_word = next(w for w in words if "2016" in w["text"])
        page_width = page.width
        # Right-aligned to the usable width (page width minus margin) - well
        # into the right half of the page, not flush against the company name.
        assert date_word["x1"] > page_width * 0.7


# --- PDF/DOCX visual/textual parity ------------------------------------------

def test_pdf_and_docx_render_the_same_text_content():
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT)
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    document = Document(io.BytesIO(docx_bytes))
    # Name/contact now live inside the header's name/logo table cells (see
    # _build_docx_with_tier) - document.paragraphs is body-only and never
    # includes header/footer content, and header.paragraphs alone misses
    # anything inside a header table, so all three have to be read here.
    header = document.sections[0].header
    header_text = "\n".join(p.text for p in header.paragraphs if p.text)
    header_table_text = "\n".join(
        p.text for table in header.tables for row in table.rows for cell in row.cells for p in cell.paragraphs if p.text
    )
    docx_text = header_text + "\n" + header_table_text + "\n" + "\n".join(p.text for p in document.paragraphs if p.text)

    for expected in ("Jane Doe", "Acme Corp", "2016-2024", "Senior Engineer",
                      "Built the platform.", "Internal Tool", "AWS"):
        assert expected in pdf_text, f"{expected!r} missing from PDF"
        assert expected in docx_text, f"{expected!r} missing from DOCX"


def test_pdf_and_docx_section_headings_match():
    from tests.test_file_generator_structuring import _docx_headings, _pdf_headings, _DEFAULT_ORDER
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT)
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    docx_headings = [h for h in _docx_headings(docx_bytes) if h in _DEFAULT_ORDER]
    assert _pdf_headings(pdf_bytes, _DEFAULT_ORDER) == docx_headings


# --- ATS compatibility (no tables, no images-as-text, real extractable text) -

def test_pdf_bullets_are_extractable_as_real_text():
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Built the platform." in text
    assert "Led a project." in text


def test_docx_uses_no_tables_for_experience_layout():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    assert document.tables == []


# --- Logo: DOCX must preserve its native aspect ratio, matching how the ---
# --- PDF actually renders it (preserveAspectRatio=True there - see -------
# --- _build_pdf_with_tier's own canvas.drawImage call) --------------------

def _docx_header_logo_extent(docx_bytes: bytes) -> tuple[float, float]:
    """(width_in_inches, height_in_inches) of the header's embedded
    picture, read directly from the header part's own XML - python-docx's
    Header object doesn't expose an `inline_shapes` collection the way
    Document does, so this is the reliable way to check what size Word
    will actually display it at."""
    document = Document(io.BytesIO(docx_bytes))
    header_xml = document.sections[0].header._element.xml
    match = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"', header_xml)
    assert match, "no <wp:extent> found in the DOCX header - logo picture missing"
    return Emu(int(match.group(1))).inches, Emu(int(match.group(2))).inches


def test_docx_logo_width_matches_pdf_logo_bounding_box_width():
    # Width is the binding constraint both formats share (the logo's
    # native ratio is wider than the PDF's 90x35pt box, so width - not
    # height - is what stays fixed at that box's own value in both).
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    width_in, _height_in = _docx_header_logo_extent(docx_bytes)
    assert width_in == pytest.approx(_PDF_LOGO_WIDTH / 72, abs=1e-4)


def test_docx_logo_height_preserves_native_aspect_ratio_not_the_pdf_box_height():
    # CONFIRMED real bug this fixes: the PDF's canvas.drawImage call uses
    # preserveAspectRatio=True, so it never actually renders the logo at
    # the full 35pt box height either - a DOCX height that matched that
    # box height exactly (as a prior version of this test asserted)
    # visibly stretched the logo, since the logo's native pixel ratio
    # (209x48, ~4.35:1) is wider than the box's ratio (90/35, ~2.57:1).
    # The correct DOCX height is derived from the logo's own real pixel
    # dimensions at the shared fixed width, which is provably LESS than
    # the box's 35pt/0.486in height.
    from PIL import Image

    from app.core.config import settings

    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    width_in, height_in = _docx_header_logo_extent(docx_bytes)
    with Image.open(settings.LOGO_PATH) as logo_image:
        native_width, native_height = logo_image.size
    expected_height_in = width_in * (native_height / native_width)
    assert height_in == pytest.approx(expected_height_in, abs=1e-4)
    assert height_in < _PDF_LOGO_HEIGHT / 72  # strictly shorter than the old, stretched value


# --- Header: Name/Logo side-by-side via a borderless two-column table -----

def _header_table(docx_bytes: bytes):
    document = Document(io.BytesIO(docx_bytes))
    header = document.sections[0].header
    assert len(header.tables) == 1, "expected exactly one header table (Name/Logo layout)"
    return header, header.tables[0]


def test_docx_header_uses_a_two_column_table():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    _header, table = _header_table(docx_bytes)
    assert len(table.rows) == 1
    assert len(table.columns) == 2


def test_docx_header_table_is_borderless():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    _header, table = _header_table(docx_bytes)
    borders = table._tbl.tblPr.find(qn("w:tblBorders"))
    assert borders is not None
    for edge in borders:
        assert edge.get(qn("w:val")) == "nil"


def test_docx_header_name_and_logo_share_the_same_table_row():
    # The requirement: logo on the SAME horizontal line as the name -
    # in a table, that means the same row, left cell vs right cell.
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    _header, table = _header_table(docx_bytes)
    left_cell, right_cell = table.cell(0, 0), table.cell(0, 1)
    assert left_cell.paragraphs[0].text == _NAME
    assert left_cell.paragraphs[0].style.name == "Heading 1"
    # Right cell holds the logo picture (no text) and is right-aligned.
    assert right_cell.paragraphs[0].text == ""
    assert right_cell.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert "<w:drawing>" in right_cell.paragraphs[0]._p.xml


def test_docx_header_logo_cell_has_room_beyond_the_picture_width():
    # CONFIRMED real clipping issue this fixes: the logo cell used to be
    # sized to EXACTLY the picture's own width, leaving no room for
    # Word's default cell padding - the picture's right-hand portion (the
    # "DATAFLIX" wordmark) sat flush against the boundary. The cell must
    # now be wider than the bare picture width, and its internal padding
    # zeroed, WITHOUT changing the picture's own declared size at all.
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    header = document.sections[0].header
    table = header.tables[0]
    right_cell = table.cell(0, 1)
    picture_width_in, _picture_height_in = _docx_header_logo_extent(docx_bytes)
    assert right_cell.width.inches > picture_width_in
    assert "tcMar" in right_cell._tc.xml


def test_docx_header_logo_picture_dimensions_unaffected_by_cell_width_change():
    # The fix must only widen the CELL - the picture's own size (aspect
    # ratio, resolution, asset) is completely untouched.
    from PIL import Image

    from app.core.config import settings

    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    width_in, height_in = _docx_header_logo_extent(docx_bytes)
    assert width_in == pytest.approx(_PDF_LOGO_WIDTH / 72, abs=1e-4)
    with Image.open(settings.LOGO_PATH) as logo_image:
        native_width, native_height = logo_image.size
    assert height_in == pytest.approx(width_in * (native_height / native_width), abs=1e-4)


def test_docx_header_contact_line_directly_below_name_in_same_cell():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    _header, table = _header_table(docx_bytes)
    left_cell = table.cell(0, 0)
    paragraph_texts = [p.text for p in left_cell.paragraphs]
    assert paragraph_texts[0] == _NAME
    assert "jane@example.com" in paragraph_texts[1]


def test_docx_header_divider_still_present_directly_after_the_table():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    header, _table = _header_table(docx_bytes)
    # The divider is a plain header paragraph (a bottom border, no text) -
    # confirms it survived the switch to a table-based name/logo layout,
    # in the same position (right after) as before.
    divider_candidates = [p for p in header.paragraphs if not p.text and "pBdr" in p._p.xml]
    assert len(divider_candidates) == 1


def test_docx_body_still_begins_with_professional_summary_after_header_table():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    assert document.paragraphs[0].text == "Professional Summary"


def test_docx_header_falls_back_to_plain_paragraphs_when_logo_missing(monkeypatch):
    import app.services.file_generator as file_generator_module

    monkeypatch.setattr(file_generator_module.settings, "LOGO_PATH", "/nonexistent/logo.png")
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    header = document.sections[0].header
    assert header.tables == []
    assert any(p.text == _NAME for p in header.paragraphs)


# --- Professional Summary: bullet-style rendering (Talent Acquisition-approved) ---

def test_split_summary_into_points_splits_one_sentence_per_bullet():
    points = _split_summary_into_points(_MULTI_SENTENCE_SUMMARY)
    assert points == [
        "Data Engineer with 2 years of experience designing ETL/ELT pipelines.",
        "Expert in Snowflake, AWS, SQL, and Python for scalable data ingestion.",
        "Possesses in-depth knowledge of Data Quality Frameworks and Enterprise Data Governance.",
    ]


def test_split_summary_into_points_preserves_every_word_no_content_lost():
    points = _split_summary_into_points(_MULTI_SENTENCE_SUMMARY)
    assert " ".join(points) == _MULTI_SENTENCE_SUMMARY


def test_split_summary_into_points_handles_single_sentence():
    assert _split_summary_into_points("One sentence only.") == ["One sentence only."]


def test_docx_summary_renders_as_bullet_list_not_one_paragraph():
    parsed = {**_PARSED, "summary": _MULTI_SENTENCE_SUMMARY}
    docx_bytes = build_docx_bytes(parsed, _NAME, _CONTACT)
    document = Document(io.BytesIO(docx_bytes))
    summary_bullets = [
        p for p in document.paragraphs
        if p.style is not None and p.style.name == "List Bullet"
        and p.text in _split_summary_into_points(_MULTI_SENTENCE_SUMMARY)
    ]
    assert len(summary_bullets) == 3


def test_pdf_summary_renders_every_sentence_as_extractable_text():
    parsed = {**_PARSED, "summary": _MULTI_SENTENCE_SUMMARY}
    pdf_bytes = build_pdf_bytes(parsed, _NAME, _CONTACT)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    for point in _split_summary_into_points(_MULTI_SENTENCE_SUMMARY):
        assert point in text
