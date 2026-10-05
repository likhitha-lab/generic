"""Unit tests for app/services/resume_quality_checker.py."""
from app.services.resume_quality_checker import (
    check_resume_quality,
    resume_has_minimum_viable_content,
)


def _strong_resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "summary": (
            "Senior software engineer with 8 years of experience building scalable cloud systems. "
            "Skilled in Python, AWS, and distributed systems design. Led cross-functional teams "
            "delivering high-impact products."
        ),
        "skills": {
            "Programming Languages": ["Python", "Java", "Go", "SQL"],
            "Cloud Platforms": ["AWS", "Azure", "GCP"],
            "DevOps": ["Docker", "Kubernetes", "Terraform"],
        },
        "tools": ["Git", "Jenkins", "Jira"],
        "education": ["B.Tech Computer Science - XYZ University"],
        "certifications": ["AWS Certified Solutions Architect"],
        "experience": [
            {
                "company": "Acme Corp", "role": "Senior Engineer", "duration": "2018-2024",
                "points": [
                    "Led migration of legacy monolith to microservices architecture on AWS.",
                    "Designed and implemented CI/CD pipelines reducing deployment time by 40%.",
                    "Mentored a team of 5 junior engineers on cloud best practices.",
                    "Architected a distributed caching layer improving response times by 30%.",
                ],
                "is_career_break": False,
            },
        ],
        "projects": [],
        "achievements": [],
    }
    base.update(overrides)
    return base


# --- Overall score shape --------------------------------------------------

def test_returns_exact_required_shape():
    result = check_resume_quality(_strong_resume())
    assert set(["score", "warnings", "suggestions"]).issubset(result.keys())
    assert isinstance(result["score"], int)
    assert isinstance(result["warnings"], list)
    assert isinstance(result["suggestions"], list)


def test_strong_resume_scores_highly():
    result = check_resume_quality(_strong_resume())
    assert result["score"] >= 85, result


def test_score_never_exceeds_100_or_goes_negative():
    result = check_resume_quality(_strong_resume())
    assert 0 <= result["score"] <= 100
    empty_result = check_resume_quality({})
    assert 0 <= empty_result["score"] <= 100


# --- Professional Summary ------------------------------------------------

def test_missing_summary_is_penalized_and_flagged():
    result = check_resume_quality(_strong_resume(summary=""))
    assert any("Missing Professional Summary" in w for w in result["warnings"])
    assert result["breakdown"]["summary"] == 0


def test_short_summary_gets_partial_credit():
    result = check_resume_quality(_strong_resume(summary="Engineer."))
    assert 0 < result["breakdown"]["summary"] < 15


# --- Skills ----------------------------------------------------------------

def test_missing_skills_is_penalized_and_flagged():
    result = check_resume_quality(_strong_resume(skills={}))
    assert any("Missing Skills section" in w for w in result["warnings"])
    assert result["breakdown"]["skills"] == 0


def test_flat_uncategorized_skills_get_a_suggestion():
    result = check_resume_quality(_strong_resume(skills=["Python", "Java", "AWS", "Docker", "SQL"]))
    assert any("categor" in s.lower() for s in result["suggestions"])


def test_few_skills_reduce_score():
    result = check_resume_quality(_strong_resume(skills={"Languages": ["Python"]}))
    assert any("very few entries" in w for w in result["warnings"])


# --- Experience --------------------------------------------------------------

def test_missing_experience_is_penalized_and_flagged():
    result = check_resume_quality(_strong_resume(experience=[]))
    assert any("Missing Professional Experience" in w for w in result["warnings"])
    assert result["breakdown"]["experience"] == 0


def test_job_with_no_bullets_is_flagged():
    resume = _strong_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": [], "is_career_break": False},
    ])
    result = check_resume_quality(resume)
    assert any("no bullet points" in w for w in result["warnings"])


def test_weak_action_verbs_trigger_suggestion():
    resume = _strong_resume(experience=[
        {
            "company": "Acme", "role": "Engineer", "duration": "2020-2024",
            "points": ["Was responsible for stuff.", "Worked on things.", "Involved in projects."],
            "is_career_break": False,
        },
    ])
    result = check_resume_quality(resume)
    assert any("action verb" in s.lower() for s in result["suggestions"])


def test_career_break_entries_are_not_penalized_for_missing_bullets():
    resume = _strong_resume(experience=[
        {"company": "Career Break", "role": "", "duration": "2019-2020", "points": [], "is_career_break": True},
        *_strong_resume()["experience"],
    ])
    result = check_resume_quality(resume)
    assert not any("Career Break" in w for w in result["warnings"])


# --- Education ---------------------------------------------------------------

def test_missing_education_is_penalized_and_flagged():
    result = check_resume_quality(_strong_resume(education=[]))
    assert any("Missing Education section" in w for w in result["warnings"])
    assert result["breakdown"]["education"] == 0


# --- ATS compliance ------------------------------------------------------------

def test_missing_name_and_email_reduce_ats_score():
    result = check_resume_quality(_strong_resume(name="", email=""))
    assert any("Missing candidate name" in w for w in result["warnings"])
    assert any("Missing email" in w for w in result["warnings"])
    assert result["breakdown"]["ats_compliance"] < 10


# --- Readability ---------------------------------------------------------------

def test_all_caps_summary_is_flagged():
    result = check_resume_quality(_strong_resume(summary="SENIOR ENGINEER WITH EIGHT YEARS OF EXPERIENCE IN CLOUD."))
    assert any("all caps" in w.lower() for w in result["warnings"])


def test_very_short_bullets_trigger_suggestion():
    resume = _strong_resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did it."], "is_career_break": False},
    ])
    result = check_resume_quality(resume)
    assert any("too short" in s.lower() for s in result["suggestions"])


# --- Duplicate detection / section completeness (fed by validation_warnings) --

def test_duplicate_detection_uses_validation_warnings():
    validation_warnings = ["Removed 2 duplicate bullet(s) for Acme Corp."]
    result = check_resume_quality(_strong_resume(), validation_warnings)
    assert any("duplicate bullet" in w for w in result["warnings"])
    assert result["breakdown"]["duplicate_detection"] < 5


def test_section_completeness_uses_validation_warnings():
    validation_warnings = ["Empty Tools section."]
    result = check_resume_quality(_strong_resume(), validation_warnings)
    assert any("Empty Tools section" in w for w in result["warnings"])
    assert result["breakdown"]["section_completeness"] < 5


def test_no_validation_warnings_gives_full_marks_for_those_two_dimensions():
    result = check_resume_quality(_strong_resume(), [])
    assert result["breakdown"]["duplicate_detection"] == 5
    assert result["breakdown"]["section_completeness"] == 5


# --- Never crashes on malformed input ------------------------------------------

def test_never_crashes_on_malformed_input():
    malformed = {
        "name": None, "email": None, "summary": None,
        "skills": None, "tools": None, "education": None,
        "experience": [{"company": None, "role": None, "duration": None, "points": None, "is_career_break": None}],
    }
    result = check_resume_quality(malformed)  # must not raise
    assert isinstance(result["score"], int)


def test_never_crashes_on_empty_dict():
    result = check_resume_quality({})
    assert isinstance(result["score"], int)
    # Summary/Skills/Experience/Education (60 of the 100 points) all require
    # actual content and score 0 on a totally empty dict; Formatting/
    # Readability find nothing *wrong* (there's nothing to check) so they
    # aren't penalized, and ATS Compliance/Duplicate/Completeness only take
    # partial deductions - so an empty resume scores low, not exactly 0.
    assert result["score"] < 40
    assert result["breakdown"]["summary"] == 0
    assert result["breakdown"]["skills"] == 0
    assert result["breakdown"]["experience"] == 0
    assert result["breakdown"]["education"] == 0


# --- resume_has_minimum_viable_content -----------------------------------------

def test_minimum_viable_content_true_for_strong_resume():
    assert resume_has_minimum_viable_content(_strong_resume())


def test_minimum_viable_content_false_for_completely_empty_resume():
    assert not resume_has_minimum_viable_content({})
    assert not resume_has_minimum_viable_content({"name": "", "email": "", "experience": [], "skills": {}, "education": []})


def test_minimum_viable_content_true_if_only_one_signal_present():
    assert resume_has_minimum_viable_content({"name": "Jane Doe"})
    assert resume_has_minimum_viable_content({"email": "jane@example.com"})
    assert resume_has_minimum_viable_content({"skills": {"Languages": ["Python"]}})
    assert resume_has_minimum_viable_content({"education": ["B.Tech"]})
