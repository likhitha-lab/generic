"""Unit tests for app/services/resume_comparison.py - Before vs After
comparison (Enterprise feature)."""
from app.services.resume_comparison import (
    compare_experience,
    compare_projects,
    compare_resumes,
    compare_scores,
    compare_skills,
    compare_summary,
    compare_tools,
)


def _resume(**overrides) -> dict:
    base = {
        "summary": "Engineer.",
        "skills": ["Python", "AWS"],
        "tools": ["Git"],
        "experience": [
            {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
             "points": ["Did X.", "Did Y."], "is_career_break": False},
        ],
        "projects": [{"title": "Tool", "description": "A tool."}],
    }
    base.update(overrides)
    return base


def test_compare_skills_detects_added_and_removed():
    before = _resume(skills=["Python", "Java"])
    after = _resume(skills=["Python", "AWS"])
    result = compare_skills(before, after)
    assert result["added"] == ["AWS"]
    assert result["removed"] == ["Java"]
    assert result["unchanged_count"] == 1


def test_compare_skills_case_insensitive_unchanged():
    before = _resume(skills=["python"])
    after = _resume(skills=["Python"])
    result = compare_skills(before, after)
    assert result["added"] == []
    assert result["removed"] == []
    assert result["unchanged_count"] == 1


def test_compare_tools_detects_changes():
    result = compare_tools(_resume(tools=["Git"]), _resume(tools=["Git", "Jenkins"]))
    assert result["added"] == ["Jenkins"]


def test_compare_summary_detects_rewrite():
    before = _resume(summary="Short summary.")
    after = _resume(summary="A completely different, much longer professional summary about the candidate.")
    result = compare_summary(before, after)
    assert result["rewritten"] is True
    assert result["after_length"] > result["before_length"]


def test_compare_summary_detects_unchanged():
    result = compare_summary(_resume(summary="Same summary."), _resume(summary="Same summary."))
    assert result["rewritten"] is False
    assert result["similarity"] == 1.0


def test_compare_experience_matches_jobs_by_identity_and_counts_bullets():
    before = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": [f"Did {i}." for i in range(10)], "is_career_break": False},
    ])
    after = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Did a compressed thing.", "Did another."], "is_career_break": False},
    ])
    result = compare_experience(before, after)
    assert result["jobs"][0]["bullets_before"] == 10
    assert result["jobs"][0]["bullets_after"] == 2
    assert result["jobs"][0]["bullets_compressed"] == 8


def test_compare_experience_handles_new_job_with_no_before_match():
    before = _resume(experience=[])
    after = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did X."], "is_career_break": False},
    ])
    result = compare_experience(before, after)
    assert result["jobs"][0]["bullets_before"] == 0
    assert result["jobs"][0]["bullets_compressed"] == 0  # no before-match - not counted as "compressed"


def test_compare_projects_detects_added_and_removed():
    before = _resume(projects=[{"title": "Tool A"}])
    after = _resume(projects=[{"title": "Tool B"}])
    result = compare_projects(before, after)
    assert result["added"] == ["tool b"]
    assert result["removed"] == ["tool a"]


def test_compare_scores_computes_delta():
    weak = _resume(summary="", skills=[], experience=[], projects=[])
    strong = _resume()
    result = compare_scores(weak, strong)
    assert result["after_score"] > result["before_score"]
    assert result["delta"] == result["after_score"] - result["before_score"]


def test_compare_resumes_full_report_shape():
    result = compare_resumes(_resume(), _resume())
    assert set(["skills", "tools", "summary", "experience", "projects", "scores"]).issubset(result.keys())


def test_compare_resumes_never_mutates_inputs():
    before = _resume()
    after = _resume(skills=["Python", "Docker"])
    before_snapshot = dict(before)
    after_snapshot = dict(after)
    compare_resumes(before, after)
    assert before == before_snapshot
    assert after == after_snapshot


def test_compare_resumes_handles_empty_dicts():
    result = compare_resumes({}, {})
    assert result["skills"]["added"] == []
    assert result["scores"]["delta"] == 0
