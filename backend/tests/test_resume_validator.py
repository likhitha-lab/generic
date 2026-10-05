"""Unit tests for app/services/resume_validator.py."""
from app.services.resume_validator import ValidationReport, validate_and_clean_resume


def _base_resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "summary": "Experienced engineer.",
        "skills": {"Programming Languages": ["Python", "SQL"]},
        "tools": ["Git"],
        "education": ["B.Tech - XYZ University"],
        "certifications": ["AWS Certified"],
        "experience": [
            {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did X."]},
        ],
        "projects": [],
    }
    base.update(overrides)
    return base


# --- Missing name / email ----------------------------------------------------

def test_missing_name_is_flagged():
    parsed, report = validate_and_clean_resume(_base_resume(name=""))
    assert any("Missing candidate name" in w for w in report.warnings)


def test_missing_email_is_flagged():
    parsed, report = validate_and_clean_resume(_base_resume(email=""))
    assert any("Missing candidate email" in w for w in report.warnings)


def test_present_name_and_email_are_not_flagged():
    parsed, report = validate_and_clean_resume(_base_resume())
    assert not any("Missing candidate name" in w for w in report.warnings)
    assert not any("Missing candidate email" in w for w in report.warnings)


# --- Missing experience -------------------------------------------------------

def test_missing_experience_is_flagged():
    parsed, report = validate_and_clean_resume(_base_resume(experience=[]))
    assert any("Missing Professional Experience" in w for w in report.warnings)


# --- Duplicate bullets ---------------------------------------------------------

def test_duplicate_bullets_are_removed_and_reported():
    resume = _base_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did X.", "did x.", "Did Y."]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert parsed["experience"][0]["points"] == ["Did X.", "Did Y."]
    assert report.cleaned
    assert any("duplicate bullet" in w for w in report.warnings)


# --- Duplicate skills / tools --------------------------------------------------

def test_duplicate_skills_in_flat_list_are_removed():
    parsed, report = validate_and_clean_resume(_base_resume(skills=["Python", "python", "SQL"]))
    assert parsed["skills"] == ["Python", "SQL"]
    assert report.cleaned


def test_duplicate_skills_in_categorized_dict_are_removed():
    resume = _base_resume(skills={"Programming Languages": ["Python", "Python", "SQL"]})
    parsed, report = validate_and_clean_resume(resume)
    assert parsed["skills"]["Programming Languages"] == ["Python", "SQL"]
    assert report.cleaned


def test_duplicate_tools_are_removed():
    parsed, report = validate_and_clean_resume(_base_resume(tools=["Git", "git", "Jenkins"]))
    assert parsed["tools"] == ["Git", "Jenkins"]
    assert report.cleaned


# --- Empty sections -------------------------------------------------------------

def test_empty_skills_section_is_flagged():
    parsed, report = validate_and_clean_resume(_base_resume(skills={}))
    assert any("Empty Skills section" in w for w in report.warnings)


def test_empty_tools_section_is_flagged():
    parsed, report = validate_and_clean_resume(_base_resume(tools=[]))
    assert any("Empty Tools section" in w for w in report.warnings)


def test_empty_projects_is_not_flagged_since_optional():
    parsed, report = validate_and_clean_resume(_base_resume(projects=[]))
    assert not any("Projects" in w for w in report.warnings)


# --- Repeated certifications ------------------------------------------------

def test_repeated_certifications_are_removed():
    resume = _base_resume(certifications=["AWS Certified", "aws certified"])
    parsed, report = validate_and_clean_resume(resume)
    assert parsed["certifications"] == ["AWS Certified"]
    assert report.cleaned
    assert any("Certifications" in w for w in report.warnings)


# --- Repeated jobs ------------------------------------------------------------

def test_repeated_jobs_are_removed():
    resume = _base_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did X."]},
        {"company": "acme", "role": "engineer", "duration": "2020-2024", "points": ["Did X."]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert len(parsed["experience"]) == 1
    assert report.cleaned
    assert any("repeated job entry" in w for w in report.warnings)


def test_different_jobs_at_same_company_are_not_merged():
    resume = _base_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
        {"company": "Acme", "role": "Senior Engineer", "duration": "2020-2024", "points": ["Did Y."]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert len(parsed["experience"]) == 2


# --- Repeated projects --------------------------------------------------------

def test_repeated_projects_are_removed():
    resume = _base_resume(projects=[
        {"title": "Internal Tool", "description": "A", "responsibilities": []},
        {"title": "internal tool", "description": "B", "responsibilities": []},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert len(parsed["projects"]) == 1
    assert report.cleaned
    assert any("repeated project entry" in w for w in report.warnings)


def test_duplicate_project_responsibilities_are_removed():
    resume = _base_resume(projects=[
        {"title": "Tool", "description": "A", "responsibilities": ["Built X", "built x", "Built Y"]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert parsed["projects"][0]["responsibilities"] == ["Built X", "Built Y"]
    assert report.cleaned


# --- Invalid dates -------------------------------------------------------------

def test_backwards_date_range_is_flagged():
    resume = _base_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2024-2020", "points": ["Did X."]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert any("Invalid date range" in w for w in report.warnings)


def test_normal_date_range_is_not_flagged():
    resume = _base_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did X."]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert not any("Invalid date range" in w for w in report.warnings)


def test_present_date_range_is_not_flagged_as_invalid():
    resume = _base_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-Present", "points": ["Did X."]},
    ])
    parsed, report = validate_and_clean_resume(resume)
    assert not any("Invalid date range" in w for w in report.warnings)


# --- Never crash ---------------------------------------------------------------

def test_never_crashes_on_malformed_input():
    malformed = {
        "name": None, "email": None,
        "skills": "not a dict or list, just a string",
        "tools": None,
        "education": None,
        "certifications": None,
        "experience": [{"company": None, "role": None, "duration": None, "points": None}],
        "projects": [{"title": None, "responsibilities": None}],
    }
    parsed, report = validate_and_clean_resume(malformed)  # must not raise
    assert isinstance(report, ValidationReport)


def test_never_crashes_on_completely_empty_dict():
    parsed, report = validate_and_clean_resume({})
    assert isinstance(report, ValidationReport)
    assert report.warnings  # missing name/email/experience/skills/etc all flagged


# --- Report shape --------------------------------------------------------------

def test_report_to_dict_shape():
    parsed, report = validate_and_clean_resume(_base_resume(skills=["Python", "python"]))
    d = report.to_dict()
    assert set(d.keys()) == {"warning_count", "cleaned", "warnings"}
    assert d["cleaned"] is True
    assert d["warning_count"] == len(d["warnings"])


def test_clean_resume_produces_no_warnings():
    parsed, report = validate_and_clean_resume(_base_resume())
    assert report.warnings == []
    assert report.cleaned is False
