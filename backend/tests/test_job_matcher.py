"""Unit tests for app/services/job_matcher.py - Job Description matching,
Missing Skill recommendations, and Resume tailoring (Enterprise feature)."""
from app.services.job_matcher import (
    extract_jd_keywords,
    match_resume_to_job,
    tailor_resume_for_job,
)


def _resume(**overrides) -> dict:
    base = {
        "skills": ["Python", "AWS", "Docker"],
        "tools": ["Git"],
        "experience": [
            {"company": "Acme", "role": "Engineer", "points": ["Built services in Python on AWS."],
             "is_career_break": False},
        ],
        "projects": [],
    }
    base.update(overrides)
    return base


# --- extract_jd_keywords ------------------------------------------------------

def test_extract_jd_keywords_finds_recognized_technologies():
    jd = "We are looking for an engineer with Python, AWS, and Kubernetes experience."
    keywords = extract_jd_keywords(jd)
    assert "Python" in keywords
    assert "AWS" in keywords
    assert "Kubernetes" in keywords


def test_extract_jd_keywords_ignores_non_technical_phrases():
    # "communication"/"leadership"/etc. are now recognized Soft/Business
    # Skills (Priority 2 fix - see skill_intelligence.py's Soft Skills/
    # Business Skills categories), so a JD requiring them is a genuine
    # match, not noise - this test now uses words that still classify as
    # nothing at all (a punctuality/attitude phrase with no recognized
    # skill of any kind) to keep testing the original "pure filler is
    # ignored" intent.
    jd = "Strong punctuality and a positive attitude are required."
    keywords = extract_jd_keywords(jd)
    assert keywords == []


def test_extract_jd_keywords_handles_empty_string():
    assert extract_jd_keywords("") == []


def test_extract_jd_keywords_deduplicates():
    jd = "Python required. Python preferred. Must know Python well."
    keywords = extract_jd_keywords(jd)
    assert keywords.count("Python") == 1


# --- match_resume_to_job -------------------------------------------------------

def test_match_resume_to_job_scores_full_match():
    resume = _resume(skills=["Python", "AWS"])
    result = match_resume_to_job(resume, "Looking for Python and AWS experience.")
    assert result["match_score"] == 100
    assert set(result["matched_keywords"]) == {"Python", "AWS"}
    assert result["missing_keywords"] == []


def test_match_resume_to_job_detects_missing_keywords():
    resume = _resume(skills=["Python"])
    result = match_resume_to_job(resume, "Looking for Python, Kubernetes, and Terraform experience.")
    assert "Kubernetes" in result["missing_keywords"]
    assert "Terraform" in result["missing_keywords"]
    assert result["match_score"] < 100


def test_match_resume_to_job_handles_no_jd_keywords():
    # See test_extract_jd_keywords_ignores_non_technical_phrases - avoids
    # "communication"/"teamwork"-style words now recognized as genuine
    # Soft/Business Skills.
    result = match_resume_to_job(_resume(), "Great punctuality and a positive attitude required.")
    assert result["match_score"] == 0
    assert result["jd_keyword_count"] == 0


def test_match_resume_to_job_matches_via_bullet_text_not_just_skills_list():
    resume = _resume(skills=[], experience=[
        {"company": "Acme", "role": "Engineer", "points": ["Built services using Terraform."], "is_career_break": False},
    ])
    result = match_resume_to_job(resume, "Terraform experience required.")
    assert "Terraform" in result["matched_keywords"]


def test_match_resume_to_job_never_mutates_parsed():
    resume = _resume()
    snapshot = dict(resume)
    match_resume_to_job(resume, "Python and AWS required.")
    assert resume == snapshot


# --- tailor_resume_for_job (never mutates, never invents) --------------------

def test_tailor_resume_for_job_promotes_matched_skills():
    resume = _resume(skills=["Docker", "Python", "AWS"])
    result = tailor_resume_for_job(resume, "We need strong Python skills.")
    assert result["suggested_skill_order"][0] == "Python"
    assert set(result["suggested_skill_order"]) == set(resume["skills"])


def test_tailor_resume_for_job_suggests_missing_keywords_with_disclaimer():
    resume = _resume(skills=["Python"])
    result = tailor_resume_for_job(resume, "Python and Kubernetes required.")
    assert any("never fabricate" in s.lower() for s in result["suggestions"])
    assert any("Kubernetes" in s for s in result["suggestions"])


def test_tailor_resume_for_job_never_mutates_parsed():
    resume = _resume(skills=["Docker", "Python", "AWS"])
    snapshot = dict(resume)
    tailor_resume_for_job(resume, "Python required.")
    assert resume == snapshot
    assert resume["skills"] == snapshot["skills"]  # order in the ORIGINAL is untouched


def test_tailor_resume_for_job_never_removes_or_adds_a_skill():
    resume = _resume(skills=["Docker", "Python", "AWS"])
    result = tailor_resume_for_job(resume, "Python and Kubernetes required.")
    assert sorted(result["suggested_skill_order"]) == sorted(resume["skills"])


def test_tailor_resume_for_job_no_reorder_suggestion_when_already_optimal():
    resume = _resume(skills=["Python"])
    result = tailor_resume_for_job(resume, "Python required.")
    assert not any("reorder" in s.lower() for s in result["suggestions"])
