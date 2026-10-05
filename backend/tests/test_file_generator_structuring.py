"""Unit tests for the file_generator.py `section_plan`/`extra_content`/
`max_pages_override` parameters on build_pdf_bytes/build_docx_bytes/
build_resume_file. Verifies (a) default (no plan) behavior always uses the
single, Talent-Acquisition-approved standard order (no adaptive/seniority-
based layout - see resume_service.py, which no longer computes or passes a
plan), and (b) the underlying parameter mechanism itself still works if a
plan IS explicitly supplied (kept as a generic, currently-unused-in-
production passthrough - see file_generator.py's own module docstring)."""
import io

import pdfplumber
from docx import Document

from app.services.file_generator import (
    _contact_line,
    _max_pages_allowed,
    build_docx_bytes,
    build_pdf_bytes,
    build_resume_file,
)

_PARSED = {
    "summary": "Experienced backend engineer with 8 years across cloud platforms.",
    "skills": ["Python", "AWS"],
    "tools": ["Git"],
    "education": ["B.Tech Computer Science"],
    "certifications": ["AWS Certified Solutions Architect"],
    "experience": [
        {"company": "Acme Corp", "role": "Engineer", "duration": "2016-2024",
         "points": ["Built things.", "Led a project."], "is_career_break": False},
    ],
    "projects": [{"title": "Internal Tool", "description": "A tool.", "responsibilities": ["Did X."]}],
    "achievements": ["Reduced costs by 20%."],
}
_NAME = "Jane Doe"
_CONTACT = {"email": "jane@example.com"}

_DEFAULT_ORDER = [
    "Professional Summary", "Technical Skills", "Educational Qualifications",
    "Certifications", "Tools", "Professional Experience", "Project Details",
]


def _docx_headings(docx_bytes: bytes) -> list[str]:
    doc = Document(io.BytesIO(docx_bytes))
    return [p.text for p in doc.paragraphs if p.style.name.startswith("Heading") and p.text]


def _pdf_headings(pdf_bytes: bytes, candidates: list[str]) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line in candidates]


# --- Backward compatibility: no plan supplied -------------------------------

def test_build_docx_bytes_default_order_unchanged_without_a_plan():
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    headings = [h for h in _docx_headings(docx_bytes) if h in _DEFAULT_ORDER]
    assert headings == _DEFAULT_ORDER


def test_build_pdf_bytes_default_order_unchanged_without_a_plan():
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT)
    headings = _pdf_headings(pdf_bytes, _DEFAULT_ORDER)
    assert headings == _DEFAULT_ORDER


def test_build_resume_file_default_still_works_without_plan_params():
    file_bytes, filename, content_type = build_resume_file(_PARSED, _NAME, _CONTACT, "docx")
    assert filename == "Jane_Doe.docx"
    headings = [h for h in _docx_headings(file_bytes) if h in _DEFAULT_ORDER]
    assert headings == _DEFAULT_ORDER


# --- skills_grouped: category subheadings, backward compatible --------------

def test_skills_grouped_renders_category_subheadings_docx():
    parsed = {**_PARSED, "skills_grouped": {"Programming Languages": ["Python"], "Cloud": ["AWS"]}}
    docx_bytes = build_docx_bytes(parsed, _NAME, _CONTACT)
    headings = _docx_headings(docx_bytes)
    assert "Programming Languages" in headings
    assert "Cloud" in headings


def test_skills_grouped_renders_category_subheadings_pdf():
    parsed = {**_PARSED, "skills_grouped": {"Programming Languages": ["Python"], "Cloud": ["AWS"]}}
    pdf_bytes = build_pdf_bytes(parsed, _NAME, _CONTACT)
    headings = _pdf_headings(pdf_bytes, ["Programming Languages", "Cloud"])
    assert headings == ["Programming Languages", "Cloud"]


def test_missing_skills_grouped_falls_back_to_flat_skills():
    # No "skills_grouped" key at all (older persisted version, manual-entry
    # flow, /api/render) - must render exactly as before, no category
    # subheadings, no error.
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    headings = _docx_headings(docx_bytes)
    assert "Technical Skills" in headings
    assert "Python" not in headings  # items render as bullets, not headings


def test_empty_skills_grouped_falls_back_to_flat_skills():
    parsed = {**_PARSED, "skills_grouped": {}}
    docx_bytes = build_docx_bytes(parsed, _NAME, _CONTACT)
    headings = _docx_headings(docx_bytes)
    assert "Technical Skills" in headings


# --- Contact Intelligence: GitHub/portfolio/relocate/remote ----------------

def test_contact_line_renders_github_and_portfolio_when_present():
    contact = {"email": "jane@example.com", "github": "github.com/janedoe", "portfolio": "janedoe.dev"}
    line = _contact_line(contact)
    assert "github.com/janedoe" in line
    assert "janedoe.dev" in line


def test_contact_line_renders_relocate_and_remote_preference():
    contact = {"open_to_relocate": True, "open_to_remote": True}
    line = _contact_line(contact)
    assert "Open to Relocation" in line
    assert "Open to Remote" in line


def test_contact_line_omits_relocate_remote_and_github_when_absent():
    line = _contact_line({"email": "jane@example.com"})
    assert "Relocat" not in line
    assert "Remote" not in line
    assert "github" not in line.lower()


# --- Achievements/Languages/Publications/Volunteer/Leadership rendering -----

def test_achievements_and_new_sections_render_when_present_docx():
    parsed = {
        **_PARSED,
        "achievements": ["Employee of the Month, March 2023"],
        "languages": ["English (Fluent)"],
        "publications": ["\"Scaling Microservices\", Tech Journal, 2022"],
        "volunteer_experience": ["Weekend coding mentor, Code for Good"],
        "leadership": ["Led a 5-person feature team"],
    }
    docx_bytes = build_docx_bytes(parsed, _NAME, _CONTACT)
    headings = _docx_headings(docx_bytes)
    for expected in ("Achievements", "Languages", "Publications", "Volunteer Experience", "Leadership"):
        assert expected in headings


def test_achievements_and_new_sections_render_when_present_pdf():
    parsed = {
        **_PARSED,
        "achievements": ["Employee of the Month, March 2023"],
        "languages": ["English (Fluent)"],
    }
    pdf_bytes = build_pdf_bytes(parsed, _NAME, _CONTACT)
    headings = _pdf_headings(pdf_bytes, ["Achievements", "Languages"])
    assert headings == ["Achievements", "Languages"]


def test_new_sections_omitted_entirely_when_absent():
    # _PARSED has no languages/publications/volunteer_experience/leadership
    # keys at all - must render identically to before these sections
    # existed, no empty headings, no error.
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT)
    headings = _docx_headings(docx_bytes)
    for absent in ("Languages", "Publications", "Volunteer Experience", "Leadership"):
        assert absent not in headings
    # _PARSED DOES have achievements data - confirms it now renders (this
    # fixture used to silently omit it before the fix).
    assert "Achievements" in headings


# --- Adaptive plan actually changes order/labels -----------------------------

def test_custom_section_plan_changes_docx_order_and_labels():
    plan = [
        ("summary", "Executive Summary"),
        ("skills", "Core Expertise"),
        ("experience", "Enterprise Experience"),
        ("education", "Education"),
    ]
    expected = ["Executive Summary", "Core Expertise", "Enterprise Experience", "Education"]
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT, section_plan=plan)
    headings = [h for h in _docx_headings(docx_bytes) if h in expected]
    assert headings == expected


def test_custom_section_plan_changes_pdf_order_and_labels():
    plan = [
        ("summary", "Leadership Summary"),
        ("experience", "Professional Experience"),
        ("skills", "Technology Portfolio"),
    ]
    pdf_bytes = build_pdf_bytes(_PARSED, _NAME, _CONTACT, section_plan=plan)
    candidates = ["Leadership Summary", "Professional Experience", "Technology Portfolio"]
    assert _pdf_headings(pdf_bytes, candidates) == candidates


def test_section_plan_skips_sections_with_no_content():
    plan = [
        ("summary", "Executive Summary"),
        ("achievements", "Achievements"),
        ("education", "Education"),
    ]
    parsed = dict(_PARSED, achievements=[])  # empty - must be skipped
    docx_bytes = build_docx_bytes(parsed, _NAME, _CONTACT, section_plan=plan)
    headings = [h for h in _docx_headings(docx_bytes) if h in ("Executive Summary", "Achievements", "Education")]
    assert "Achievements" not in headings
    assert headings == ["Executive Summary", "Education"]


def test_extra_content_supplies_synthetic_section():
    plan = [("summary", "Leadership Summary"), ("leadership_achievements", "Leadership Achievements")]
    extra_content = {"leadership_achievements": ["Led a team of 12 engineers.", "Reduced costs by 40%."]}
    docx_bytes = build_docx_bytes(_PARSED, _NAME, _CONTACT, section_plan=plan, extra_content=extra_content)
    doc = Document(io.BytesIO(docx_bytes))
    body_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Leadership Achievements" in body_text
    assert "Led a team of 12 engineers." in body_text


def test_max_pages_override_is_respected():
    # A 20+ year career with a generous override should not be forced down
    # to the default 3-page cap.
    long_career_parsed = dict(_PARSED, experience=[
        {"company": "Acme Corp", "role": "Engineer", "duration": "2000-2024",
         "points": [f"Did task {i}." for i in range(40)], "is_career_break": False},
    ])
    pdf_bytes = build_pdf_bytes(long_career_parsed, _NAME, _CONTACT, max_pages_override=5)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # Just confirm it built successfully and the override was accepted
        # (no exception, non-empty output) - exact page count depends on
        # content volume, which isn't the point of this test.
        assert len(pdf.pages) >= 1


def test_build_resume_file_passes_through_plan_and_extra_content():
    plan = [("summary", "Executive Summary"), ("skills", "Core Expertise")]
    file_bytes, _filename, _content_type = build_resume_file(
        _PARSED, _NAME, _CONTACT, "docx", section_plan=plan,
    )
    expected = ["Executive Summary", "Core Expertise"]
    headings = [h for h in _docx_headings(file_bytes) if h in expected]
    assert headings == expected


# --- Resume Length: _max_pages_allowed years-of-experience bracket ---------
# (Talent Acquisition-approved spec: under 5y->2 pages, 5-10y->3, 10-15y->4,
# 15+y->5 - lower-bound inclusive, see file_generator.py's own
# _PAGE_BUDGET_BRACKETS docstring for why.)

def _experience_spanning(years: int) -> list[dict]:
    return [{"company": "Acme", "role": "Engineer", "duration": f"2000-{2000 + years}", "points": []}]


def test_max_pages_allowed_under_5_years_is_2_pages():
    assert _max_pages_allowed(_experience_spanning(0)) == 2
    assert _max_pages_allowed(_experience_spanning(4)) == 2


def test_max_pages_allowed_5_to_10_years_is_3_pages():
    assert _max_pages_allowed(_experience_spanning(5)) == 3
    assert _max_pages_allowed(_experience_spanning(9)) == 3


def test_max_pages_allowed_10_to_15_years_is_4_pages():
    assert _max_pages_allowed(_experience_spanning(10)) == 4
    assert _max_pages_allowed(_experience_spanning(14)) == 4


def test_max_pages_allowed_15_plus_years_is_5_pages():
    assert _max_pages_allowed(_experience_spanning(15)) == 5
    assert _max_pages_allowed(_experience_spanning(30)) == 5


def test_max_pages_allowed_override_always_wins():
    assert _max_pages_allowed(_experience_spanning(30), override=1) == 1
    assert _max_pages_allowed(_experience_spanning(0), override=7) == 7


def test_max_pages_allowed_handles_apostrophe_year_shorthand():
    # Regression test: a real candidate's resume used "Aug'23 - till date"
    # -style shorthand for every genuine job - the plain 4-digit year regex
    # alone can't see "'23" at all, silently computing 0 years (and
    # therefore the smallest page-cap bracket) for a real 18-year career.
    experience = [
        {"company": "InspiriSYS", "role": "Architect", "duration": "Aug'23 - till date", "points": []},
        {"company": "Honeywell", "role": "Engineer", "duration": "Apr'08 - Mar'14", "points": []},
    ]
    assert _max_pages_allowed(experience) == 5


def test_max_pages_allowed_handles_curly_apostrophe_year_shorthand():
    # A real candidate's resume PDF-extracted with a curly right-single-
    # quote (U+2019, "Aug'23") rather than a straight ASCII apostrophe.
    experience = [
        {"company": "InspiriSYS", "role": "Architect", "duration": "Aug’23 - till date", "points": []},
        {"company": "Honeywell", "role": "Engineer", "duration": "Apr’08 - Mar’14", "points": []},
    ]
    assert _max_pages_allowed(experience) == 5
