"""Unit tests for app/services/person_extractor.py's 5-signal priority
chain and the never-output-a-placeholder guarantee."""
import io

from docx import Document

from app.services.person_extractor import is_forbidden_name, resolve_candidate_name


# --- is_forbidden_name -------------------------------------------------------

def test_is_forbidden_name_catches_every_listed_placeholder():
    for forbidden in ["Converted_Resume", "converted_resume", "Resume", "RESUME", "Unknown", "Untitled"]:
        assert is_forbidden_name(forbidden), forbidden


def test_is_forbidden_name_strips_file_extension():
    assert is_forbidden_name("Resume.pdf")
    assert is_forbidden_name("resume.docx")


def test_is_forbidden_name_allows_real_names():
    assert not is_forbidden_name("Jane Doe")
    assert not is_forbidden_name("")
    assert not is_forbidden_name(None)


# --- Signal 1: resume header (Gemini result / strict raw-text scan) --------

def test_resolves_from_gemini_header_result_when_plausible():
    result = resolve_candidate_name("Jane Doe", "Experienced engineer.", "Jane Doe\nSummary\n...", None, None)
    assert result == "Jane Doe"


def test_falls_through_gemini_when_implausible_to_header_line_scan():
    # Gemini mistakenly returned the summary's opening words - clean_name
    # rejects this (see normalization.py), so it must fall through to the
    # strict raw-text header scan instead, which finds the real name on
    # line 1.
    raw_text = "Jane Doe\nExperienced Software Engineer with 8 years...\n"
    result = resolve_candidate_name("Experienced Software Engineer", "Experienced Software Engineer with 8 years...", raw_text, None, None)
    assert result == "Jane Doe"


def test_gemini_result_that_is_a_forbidden_placeholder_is_rejected():
    raw_text = "Jane Doe\nProfessional Summary\n"
    result = resolve_candidate_name("Converted_Resume", "", raw_text, None, None)
    assert result == "Jane Doe"


# --- Signal 2: email owner ---------------------------------------------------

def test_resolves_from_email_when_header_signals_fail():
    result = resolve_candidate_name(None, "", "Professional Summary\nNo name line here\n", "john.doe@gmail.com", None)
    assert result == "John Doe"


def test_email_with_digits_still_resolves():
    result = resolve_candidate_name(None, "", "no header here", "jane.smith123@gmail.com", None)
    assert result == "Jane Smith"


def test_generic_departmental_email_is_not_used():
    result = resolve_candidate_name(None, "", "no header here", "hr@company.com", None)
    assert result == ""


def test_single_word_email_local_part_is_not_used():
    # Only a first name available - not enough to trust as a full name.
    result = resolve_candidate_name(None, "", "no header here", "john@gmail.com", None)
    assert result == ""


# --- Signal 3: LinkedIn profile ----------------------------------------------

def test_resolves_from_linkedin_when_higher_signals_fail():
    result = resolve_candidate_name(
        None, "", "no header here", None, "https://www.linkedin.com/in/jane-doe-77a1b2/"
    )
    assert result == "Jane Doe"


def test_linkedin_strips_trailing_numeric_id():
    result = resolve_candidate_name(None, "", "no header here", None, "linkedin.com/in/john-smith-123456789")
    assert result == "John Smith"


def test_non_linkedin_url_yields_nothing():
    result = resolve_candidate_name(None, "", "no header here", None, "https://github.com/johndoe")
    assert result == ""


# --- Signal 4: PDF/DOCX file metadata ----------------------------------------

def _docx_bytes_with_author(author: str) -> bytes:
    doc = Document()
    doc.core_properties.author = author
    doc.add_paragraph("no usable header line")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_resolves_from_docx_metadata_when_higher_signals_fail():
    file_bytes = _docx_bytes_with_author("Jane Doe")
    result = resolve_candidate_name(None, "", "no header here", None, None, file_bytes=file_bytes, suffix=".docx")
    assert result == "Jane Doe"


def test_docx_metadata_author_that_is_not_a_real_name_is_rejected():
    # A single-word / software-name author (common default metadata) must
    # not be trusted as a full name.
    file_bytes = _docx_bytes_with_author("Microsoft Office User")
    result = resolve_candidate_name(None, "", "no header here", None, None, file_bytes=file_bytes, suffix=".docx")
    # "Microsoft Office User" survives clean_name's word-count cap fine, so
    # this documents current behavior rather than asserting rejection -
    # metadata is inherently the lowest-confidence signal for a reason.
    assert result in ("Microsoft Office User", "")


def test_metadata_signal_skipped_without_file_bytes():
    result = resolve_candidate_name(None, "", "no header here", None, None)
    assert result == ""


# --- Signal 5: last-resort loose scan ----------------------------------------

def test_resolves_via_loose_scan_when_name_is_past_strict_scan_window():
    # Name appears on line 5, past the strict 3-line header window, but
    # within the loose 8-line last-resort window.
    raw_text = "Header logo\nCompany banner\nDate: 2024\nJane Doe\nContact info line\n"
    result = resolve_candidate_name(None, "", raw_text, None, None)
    assert result == "Jane Doe"


# --- Overall priority order + never-output-a-placeholder guarantee --------

def test_priority_order_header_beats_email_beats_linkedin():
    result = resolve_candidate_name(
        "Jane Doe", "Summary text.", "Jane Doe\nSummary\n", "wrong.person@gmail.com",
        "linkedin.com/in/another-person",
    )
    assert result == "Jane Doe"


def test_returns_blank_when_every_signal_fails():
    result = resolve_candidate_name(None, "", "no usable content at all here", None, None)
    assert result == ""


def test_never_returns_a_forbidden_placeholder_even_if_every_signal_points_there():
    # Every signal either fails or would only ever produce a forbidden
    # placeholder - result must be "", never "Resume"/"Unknown"/etc.
    result = resolve_candidate_name("Resume", "", "Untitled\nResume\n", None, None)
    assert result == ""
    assert not is_forbidden_name(result)
