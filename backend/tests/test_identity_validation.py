"""Unit + API-level tests for app/services/identity_validation.py - Phase D
of the Resume Intelligence Engine redesign: the hard gate that fixes the
confirmed real-world defect where a resume with real experience/skills but
a blank name still rendered (resume_quality_checker.
resume_has_minimum_viable_content's OR-based gate lets that through - see
identity_validation.py's own module docstring for the full audit trail)."""
import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from app.services.identity_validation import (
    ResumeValidationError,
    check_contact_preserved,
    check_name_present,
    count_duplicate_entities,
    find_duplicate_entities,
    validate_identity,
)

_VALID = {
    "name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100",
    "linkedin": "linkedin.com/in/janedoe", "github": "github.com/janedoe",
    "experience": [], "projects": [],
}


def test_validate_identity_passes_for_complete_resume():
    validate_identity(dict(_VALID), raw_text="Jane Doe\njane@example.com")  # must not raise


def test_validate_identity_raises_when_name_missing():
    structured = {**_VALID, "name": ""}
    with pytest.raises(ResumeValidationError, match="name"):
        validate_identity(structured, raw_text="Some resume text with no name line at all.")


def test_validate_identity_raises_when_name_is_only_whitespace():
    structured = {**_VALID, "name": "   "}
    with pytest.raises(ResumeValidationError):
        validate_identity(structured, raw_text="text")


def test_validate_identity_raises_when_email_present_in_source_but_lost():
    structured = {**_VALID, "email": ""}
    with pytest.raises(ResumeValidationError, match="email"):
        validate_identity(structured, raw_text="Jane Doe\nContact: jane@example.com")


def test_validate_identity_does_not_raise_when_email_genuinely_absent_from_source():
    structured = {**_VALID, "email": ""}
    validate_identity(structured, raw_text="Jane Doe\nNo email listed anywhere in this document.")


def test_validate_identity_raises_when_phone_present_in_source_but_lost():
    structured = {**_VALID, "phone": ""}
    with pytest.raises(ResumeValidationError, match="phone"):
        validate_identity(structured, raw_text="Jane Doe\nCall me at +1 415 555 0199")


def test_validate_identity_raises_when_linkedin_present_in_source_but_lost():
    structured = {**_VALID, "linkedin": ""}
    with pytest.raises(ResumeValidationError, match="LinkedIn"):
        validate_identity(structured, raw_text="Jane Doe\nlinkedin.com/in/janedoe")


def test_validate_identity_raises_when_github_present_in_source_but_lost():
    structured = {**_VALID, "github": ""}
    with pytest.raises(ResumeValidationError, match="GitHub"):
        validate_identity(structured, raw_text="Jane Doe\ngithub.com/janedoe")


def test_validate_identity_raises_on_duplicate_employment_surviving_entity_linking():
    structured = {
        **_VALID,
        "experience": [
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
        ],
    }
    with pytest.raises(ResumeValidationError, match="Duplicate employment"):
        validate_identity(structured, raw_text="Jane Doe")


def test_validate_identity_raises_on_duplicate_project_surviving_entity_linking():
    structured = {
        **_VALID,
        "projects": [
            {"title": "Customer Portal", "description": "A self-service web portal for customers to use."},
            {"title": "Customer Portal", "description": "A self-service web portal for customers to use."},
        ],
    }
    with pytest.raises(ResumeValidationError, match="Duplicate project"):
        validate_identity(structured, raw_text="Jane Doe")


def test_validate_identity_does_not_raise_for_genuinely_distinct_employments():
    structured = {
        **_VALID,
        "experience": [
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
            {"company": "Widget Inc", "role": "Manager", "duration": "2020-2024", "points": ["Did Y."]},
        ],
    }
    validate_identity(structured, raw_text="Jane Doe")


# --- End-to-end: the real gate actually firing through the upload API -------

def _nameless_pdf_bytes() -> bytes:
    """Synthetic resume with NO plausible name line anywhere in the first
    8 lines (every line is either a recognized section heading or starts
    with a disqualifying/summary-opener word - see normalization.py's
    clean_name) - and no email/phone/linkedin/github anywhere. Every one
    of person_extractor.resolve_candidate_name's 5 signals must fail for
    this document, so the ONLY way name ends up non-empty is if something
    invents one - which validate_identity must then reject via the
    (mocked) empty Gemini contact response, forcing the fallback chain."""
    buffer = io.BytesIO()
    # ReportLab's own SimpleDocTemplate defaults the PDF /Author metadata
    # field to the literal string "anonymous" - which survives clean_name's
    # plausibility checks and would otherwise resolve as the candidate's
    # name via person_extractor's signal 4 (file metadata), defeating this
    # fixture's entire purpose. Force it blank explicitly.
    doc = SimpleDocTemplate(buffer, pagesize=A4, author="")
    styles = getSampleStyleSheet()
    doc.build([
        Paragraph("PROFESSIONAL SUMMARY", styles["Heading2"]),
        Paragraph("Experienced software engineer with five years in backend systems.", styles["Normal"]),
        Paragraph("PROFESSIONAL EXPERIENCE", styles["Heading2"]),
        Paragraph("Responsible for maintaining backend services.", styles["Normal"]),
        Paragraph("TECHNICAL SKILLS", styles["Heading2"]),
        Paragraph("Python and SQL for data pipelines.", styles["Normal"]),
        Paragraph("EDUCATION", styles["Heading2"]),
        Paragraph("Bachelor of Science in Computer Science.", styles["Normal"]),
    ])
    return buffer.getvalue()


def test_upload_with_no_recoverable_name_returns_422(client, auth_headers, monkeypatch):
    import app.services.extraction_pipeline as extraction_pipeline_module

    # Force every Gemini call (including the retry-with-a-stricter-prompt
    # attempt) to return a blank name/contact - the autouse fake_gemini
    # fixture's fixed response always has a name, which would never
    # exercise this gate at all.
    monkeypatch.setattr(
        extraction_pipeline_module, "call_gemini",
        lambda prompt: {
            "name": "", "email": "", "phone": "", "linkedin": "", "github": "", "portfolio": "",
            "open_to_relocate": None, "open_to_remote": None, "summary": "A backend engineer.",
            "skills": ["Python", "SQL"], "education": ["Bachelor of Science"], "certifications": [], "tools": [],
            "experience": [{"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020",
                            "points": ["Maintained backend services."], "reason_for_leaving": "", "notes": "",
                            "is_career_break": False, "break_detail": ""}],
            "projects": [], "achievements": [], "languages": [], "publications": [],
            "volunteer_experience": [], "leadership": [],
        },
    )

    resp = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.pdf", _nameless_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 422, resp.text
    assert "name" in resp.json()["detail"].lower()


# --- Non-raising checks (shared with evaluation_dashboard.py) --------------

def test_check_name_present_true_and_false():
    assert check_name_present({"name": "Jane Doe"}) is True
    assert check_name_present({"name": ""}) is False
    assert check_name_present({}) is False


def test_check_contact_preserved_true_when_nothing_lost():
    assert check_contact_preserved(dict(_VALID), raw_text="Jane Doe\njane@example.com") is True


def test_check_contact_preserved_false_when_source_has_it_but_structured_lost_it():
    structured = {**_VALID, "phone": ""}
    assert check_contact_preserved(structured, raw_text="Jane Doe\nCall +1 415 555 0199") is False


def test_check_contact_preserved_true_when_genuinely_absent_from_source():
    structured = {**_VALID, "phone": ""}
    assert check_contact_preserved(structured, raw_text="Jane Doe\nNo phone number here.") is True


def test_find_duplicate_entities_empty_for_distinct_entries():
    structured = {
        **_VALID,
        "experience": [
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
            {"company": "Widget Inc", "role": "Manager", "duration": "2020-2024", "points": ["Did Y."]},
        ],
    }
    assert find_duplicate_entities(structured) == []
    assert count_duplicate_entities(structured) == 0


def test_find_duplicate_entities_describes_the_duplicate_employment():
    structured = {
        **_VALID,
        "experience": [
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
        ],
    }
    found = find_duplicate_entities(structured)
    assert len(found) == 1
    assert "Acme Corp" in found[0]
    assert count_duplicate_entities(structured) == 1
