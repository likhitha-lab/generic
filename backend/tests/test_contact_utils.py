"""Unit tests for app/utils/contact.py's GitHub-URL fallback extraction
(Contact Intelligence addition) - the same layout-independent regex-scan
fallback pattern already proven for email/phone/LinkedIn."""
from app.utils.contact import (
    extract_all_emails,
    extract_all_github_urls,
    extract_all_linkedin_urls,
    extract_all_phones,
    extract_github_url,
)


def test_extract_github_url_finds_profile_link():
    text = "Portfolio: https://github.com/janedoe | Email: jane@example.com"
    assert extract_github_url(text) == "https://github.com/janedoe"


def test_extract_github_url_finds_bare_domain_without_scheme():
    assert extract_github_url("Find my work at github.com/janedoe") == "github.com/janedoe"


def test_extract_github_url_returns_empty_when_absent():
    assert extract_github_url("No links here, just a summary.") == ""


def test_extract_github_url_handles_none_and_empty():
    assert extract_github_url(None) == ""
    assert extract_github_url("") == ""


def test_extract_github_url_strips_trailing_punctuation():
    assert extract_github_url("See github.com/janedoe.") == "github.com/janedoe"


# --- "Find every match" variants (PII Detection layer's own reuse) --------

def test_extract_all_emails_finds_every_occurrence():
    text = "Primary: jane@example.com, backup: jane.doe@work.example.org"
    assert extract_all_emails(text) == ["jane@example.com", "jane.doe@work.example.org"]


def test_extract_all_phones_finds_every_occurrence_and_skips_year_ranges():
    text = "Call +1-415-555-0199 or (415) 555-0100. Worked there 2020-2024."
    phones = extract_all_phones(text)
    assert len(phones) == 2
    assert "2020-2024" not in phones


def test_extract_all_linkedin_urls_finds_every_occurrence():
    text = "linkedin.com/in/janedoe and also linkedin.com/in/john-smith"
    assert extract_all_linkedin_urls(text) == ["linkedin.com/in/janedoe", "linkedin.com/in/john-smith"]


def test_extract_all_github_urls_finds_every_occurrence():
    text = "github.com/janedoe, github.com/janedoe/resume-builder"
    assert extract_all_github_urls(text) == ["github.com/janedoe", "github.com/janedoe/resume-builder"]


def test_extract_all_emails_empty_when_absent():
    assert extract_all_emails("No contact info here.") == []
