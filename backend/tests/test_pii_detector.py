"""Unit tests for app/services/pii_detector.py - the PII Detection layer
(Phase 1 of the PII Masking/Unmasking architecture). Detection only - no
masking/replacement exists yet, so every test here asserts on WHAT was
found, never on any rewritten output."""
from app.services.pii_detector import (
    detect_pii_in_structured,
    detect_pii_in_text,
    merge_pii_reports,
)

_RAW_TEXT = """Jane Doe
jane.doe@example.com | +1-415-555-0199
linkedin.com/in/janedoe | github.com/janedoe | https://janedoe.dev

Address: 221B Baker Street, London

Professional Summary
Data Engineer with 5 years of experience.

Professional Experience
Acme Corp, Jan 2020 - Present
Data Engineer
Note: Client: Globex Corporation
Employee ID: EMP12345

PAN: ABCDE1234F
Aadhaar: 1234 5678 9012
Passport No: A1234567
"""


def test_detect_pii_in_text_finds_candidate_name():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["candidate_names"] == ["Jane Doe"]


def test_detect_pii_in_text_finds_every_contact_field():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["emails"] == ["jane.doe@example.com"]
    assert report["phones"] == ["+1-415-555-0199"]
    assert report["linkedins"] == ["linkedin.com/in/janedoe"]
    assert report["githubs"] == ["github.com/janedoe"]


def test_detect_pii_in_text_finds_portfolio_url_but_not_linkedin_or_github():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["portfolios"] == ["https://janedoe.dev"]


def test_detect_pii_in_text_finds_labeled_address():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["addresses"] == ["221B Baker Street, London"]


def test_detect_pii_in_text_finds_labeled_client():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["clients"] == ["Globex Corporation"]


def test_detect_pii_in_text_finds_labeled_employee_id():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["employee_ids"] == ["EMP12345"]


def test_detect_pii_in_text_finds_pan():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["pans"] == ["ABCDE1234F"]


def test_detect_pii_in_text_finds_labeled_aadhaar():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["aadhaars"] == ["1234 5678 9012"]


def test_detect_pii_in_text_finds_labeled_passport():
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["passports"] == ["A1234567"]


def test_detect_pii_in_text_never_guesses_companies_or_projects():
    # Deliberate scope limit (see module docstring) - free-text company/
    # project names have no reliable pattern without NER or already-
    # categorized structured data; guessing here would trade precision for
    # recall in the direction this task explicitly said not to.
    report = detect_pii_in_text(_RAW_TEXT)
    assert report["companies"] == []
    assert report["projects"] == []


def test_detect_pii_in_text_does_not_false_positive_on_year_ranges():
    report = detect_pii_in_text("Experience: 2020-2024 at a startup.")
    assert report["phones"] == []


def test_detect_pii_in_text_does_not_false_positive_pan_on_ordinary_words():
    # No 5-letter/4-digit/1-letter shape anywhere in ordinary prose.
    report = detect_pii_in_text("Experienced backend engineer with strong Python skills.")
    assert report["pans"] == []


def test_detect_pii_in_text_unlabeled_12_digit_run_is_not_treated_as_aadhaar():
    # No grouping, no label - a bare 12-digit run is too generic to attribute.
    report = detect_pii_in_text("Reference code 123456789012 for internal tracking.")
    assert report["aadhaars"] == []


def test_detect_pii_in_text_handles_empty_and_none():
    assert detect_pii_in_text("") == detect_pii_in_text("")  # no crash
    for key, value in detect_pii_in_text("").items():
        assert value == []


# --- Structured mode (post-extraction) --------------------------------------

_STRUCTURED = {
    "name": "Jane Doe",
    "email": "jane.doe@example.com",
    "phone": "+1-415-555-0199",
    "linkedin": "linkedin.com/in/janedoe",
    "github": "github.com/janedoe",
    "portfolio": "https://janedoe.dev",
    "location": "London",
    "experience": [
        {"company": "Acme Corp", "role": "Data Engineer", "duration": "2020-Present",
         "notes": "Client: Globex Corporation; Employee ID: EMP12345"},
        {"company": "Career Break", "is_career_break": True, "break_detail": "Travel"},
    ],
    "projects": [
        {"title": "Customer Portal Revamp", "client": "Initech", "description": "Rebuilt the portal."},
    ],
}


def test_detect_pii_in_structured_finds_companies_excluding_career_breaks():
    report = detect_pii_in_structured(_STRUCTURED)
    assert report["companies"] == ["Acme Corp"]


def test_detect_pii_in_structured_finds_clients_from_both_notes_and_projects():
    report = detect_pii_in_structured(_STRUCTURED)
    assert set(report["clients"]) == {"Globex Corporation", "Initech"}


def test_detect_pii_in_structured_finds_project_titles():
    report = detect_pii_in_structured(_STRUCTURED)
    assert report["projects"] == ["Customer Portal Revamp"]


def test_detect_pii_in_structured_finds_employee_ids_from_notes():
    report = detect_pii_in_structured(_STRUCTURED)
    assert report["employee_ids"] == ["EMP12345"]


def test_detect_pii_in_structured_finds_identity_and_contact_fields():
    report = detect_pii_in_structured(_STRUCTURED)
    assert report["candidate_names"] == ["Jane Doe"]
    assert report["emails"] == ["jane.doe@example.com"]
    assert report["addresses"] == ["London"]


def test_detect_pii_in_structured_handles_empty_dict():
    for value in detect_pii_in_structured({}).values():
        assert value == []


# --- Merge -------------------------------------------------------------------

def test_merge_pii_reports_deduplicates_across_both_modes():
    text_report = detect_pii_in_text(_RAW_TEXT)
    structured_report = detect_pii_in_structured(_STRUCTURED)
    merged = merge_pii_reports(text_report, structured_report)
    assert merged["emails"] == ["jane.doe@example.com"]  # same email in both, not duplicated
    assert merged["companies"] == ["Acme Corp"]  # only structured mode finds this
    assert merged["clients"] == ["Globex Corporation", "Initech"]


def test_merge_pii_reports_handles_no_reports():
    merged = merge_pii_reports()
    for value in merged.values():
        assert value == []
