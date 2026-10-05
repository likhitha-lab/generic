"""Unit tests for app/services/section_recovery.py - the detect -> retry ->
infer -> deterministic Section Recovery Engine. This is the direct fix for
"a section existed in the source but silently disappeared from the
generated resume" - the module's own docstring/module-level comment on
extraction_pipeline._warn_if_section_likely_dropped documents that bug in
detail; these tests verify the actual corrective loop this module adds."""
import app.services.section_recovery as section_recovery
from app.services.gemini_client import GeminiError
from app.services.section_recovery import (
    deterministic_extract,
    detect_missing_sections,
    infer_section,
    recover_missing_sections,
)


# --- Tier 1: detect_missing_sections -----------------------------------------

def test_detect_missing_sections_flags_empty_field_with_keyword_signal():
    raw_text = "Experienced engineer.\n\nCertifications:\nAWS Certified Solutions Architect"
    structured = {"certifications": [], "skills": ["Python"], "experience": [{"company": "Acme"}]}
    assert "certifications" in detect_missing_sections(raw_text, structured)


def test_detect_missing_sections_never_flags_a_populated_field():
    raw_text = "Certifications:\nAWS Certified Solutions Architect"
    structured = {"certifications": ["AWS Certified Solutions Architect"]}
    assert "certifications" not in detect_missing_sections(raw_text, structured)


def test_detect_missing_sections_ignores_field_with_no_keyword_signal_at_all():
    raw_text = "A short resume describing a career in backend engineering."
    structured = {"achievements": []}
    assert "achievements" not in detect_missing_sections(raw_text, structured)


def test_detect_missing_sections_covers_all_extension_fields():
    raw_text = (
        "Awards: Employee of the Month. Languages known: English, Spanish. "
        "Publications: one paper published. Volunteer: community service work. "
        "Leadership: mentored junior engineers."
    )
    structured = {
        "achievements": [], "languages": [], "publications": [],
        "volunteer_experience": [], "leadership": [],
    }
    missing = detect_missing_sections(raw_text, structured)
    for field in ("achievements", "languages", "publications", "volunteer_experience", "leadership"):
        assert field in missing


# --- Tier 2: retry (via recover_missing_sections) ----------------------------

def test_recover_missing_sections_recovers_via_retry(monkeypatch):
    raw_text = "Experience:\nAcme Corp\n\nCertifications:\nAWS Certified Solutions Architect"
    structured = {"certifications": [], "skills": [], "tools": [], "education": [], "experience": [{"company": "Acme"}]}

    def fake_gemini(prompt):
        return {"skills": [], "education": [], "certifications": ["AWS Certified Solutions Architect"], "tools": []}

    monkeypatch.setattr(section_recovery, "call_gemini", fake_gemini)
    result, log = recover_missing_sections(raw_text, structured)
    assert result["certifications"] == ["AWS Certified Solutions Architect"]
    assert log["certifications"] == "retry"


def test_recover_missing_sections_groups_retries_by_underlying_prompt(monkeypatch):
    # skills, education, certifications, tools all missing - must trigger
    # exactly ONE call to the skills-section prompt, not four.
    raw_text = "Skills: Python. Education: B.Tech. Certifications: AWS Certified. Tools: Git."
    structured = {"skills": [], "education": [], "certifications": [], "tools": [], "experience": [{"company": "Acme"}]}
    call_count = {"n": 0}

    def fake_gemini(prompt):
        call_count["n"] += 1
        return {"skills": ["Python"], "education": ["B.Tech"], "certifications": ["AWS Certified"], "tools": ["Git"]}

    monkeypatch.setattr(section_recovery, "call_gemini", fake_gemini)
    result, log = recover_missing_sections(raw_text, structured)
    assert call_count["n"] == 1
    assert result["skills"] == ["Python"]
    assert result["tools"] == ["Git"]
    assert all(log[f] == "retry" for f in ("skills", "education", "certifications", "tools"))


# --- Tier 3: infer_section (no Gemini) ----------------------------------------

def test_infer_section_finds_skill_mentioned_only_in_experience_bullet():
    structured = {
        "summary": "",
        "experience": [{"points": ["Built services using Python and Kubernetes."]}],
        "projects": [],
    }
    inferred = infer_section("skills", structured)
    assert "Python" in inferred
    assert "Kubernetes" in inferred


def test_infer_section_finds_certification_mentioned_inline():
    structured = {
        "summary": "",
        "experience": [{"points": ["Earned AWS Certified Solutions Architect while leading the migration."]}],
        "projects": [],
    }
    inferred = infer_section("certifications", structured)
    assert any("certified" in phrase.lower() for phrase in inferred)


def test_infer_section_returns_empty_for_unsupported_sections():
    structured = {"summary": "Led the team.", "experience": [], "projects": []}
    assert infer_section("experience", structured) == []
    assert infer_section("achievements", structured) == []


def test_infer_section_returns_empty_when_nothing_to_scan():
    assert infer_section("skills", {"summary": "", "experience": [], "projects": []}) == []


# --- Tier 4: deterministic_extract (scans raw_text directly) ----------------

def test_deterministic_extract_finds_skill_in_raw_text():
    raw_text = "A resume with no explicit Skills heading, but built with React and PostgreSQL throughout."
    assert "React" in deterministic_extract("skills", raw_text)
    assert "PostgreSQL" in deterministic_extract("skills", raw_text)


def test_deterministic_extract_finds_education_line():
    raw_text = "Some prose.\nBachelor of Science in Computer Science, ABC University, 2018\nMore prose."
    result = deterministic_extract("education", raw_text)
    assert any("bachelor" in line.lower() for line in result)


def test_deterministic_extract_returns_empty_for_unsupported_sections():
    assert deterministic_extract("experience", "Some raw text.") == []
    assert deterministic_extract("projects", "Some raw text.") == []


# --- Full loop: falls through tiers, never overwrites real content ----------

def test_recover_missing_sections_falls_through_to_inference_when_retry_fails(monkeypatch):
    raw_text = "Experience:\nAcme Corp\nBuilt services using Python and Docker."
    structured = {
        "skills": [], "tools": [], "education": [], "certifications": [],
        "summary": "", "experience": [{"points": ["Built services using Python and Docker."]}], "projects": [],
    }

    def boom(prompt):
        raise GeminiError("Gemini unavailable")

    monkeypatch.setattr(section_recovery, "call_gemini", boom)
    result, log = recover_missing_sections(raw_text, structured)
    assert "Python" in result["skills"]
    assert log["skills"] == "inference"


def test_recover_missing_sections_never_overwrites_existing_content(monkeypatch):
    raw_text = "Certifications: AWS Certified Solutions Architect"
    structured = {"certifications": ["Already Extracted Cert"], "skills": ["Python"], "tools": ["Git"]}

    def fake_gemini(prompt):
        return {"certifications": ["Should Never Appear"], "skills": [], "education": [], "tools": []}

    monkeypatch.setattr(section_recovery, "call_gemini", fake_gemini)
    result, log = recover_missing_sections(raw_text, structured)
    assert result["certifications"] == ["Already Extracted Cert"]
    assert log == {}  # nothing was missing, so nothing was touched at all


def test_recover_missing_sections_logs_unrecovered_when_all_tiers_fail(monkeypatch):
    raw_text = "Volunteer: helped out at a local shelter."
    structured = {"volunteer_experience": [], "summary": "", "experience": [], "projects": []}

    def boom(prompt):
        raise GeminiError("Gemini unavailable")

    monkeypatch.setattr(section_recovery, "call_gemini", boom)
    result, log = recover_missing_sections(raw_text, structured)
    assert result["volunteer_experience"] == []
    assert log["volunteer_experience"] == "unrecovered"


def test_recover_missing_sections_is_a_noop_when_nothing_is_missing():
    structured = {"skills": ["Python"], "tools": ["Git"], "certifications": ["AWS Certified"]}
    result, log = recover_missing_sections("Skills: Python. Tools: Git. Certifications: AWS Certified.", structured)
    assert result == structured
    assert log == {}
