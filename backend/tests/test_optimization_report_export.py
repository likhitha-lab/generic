"""Unit tests for app/services/optimization_report_export.py - exportable
optimization reports (Enterprise feature)."""
from app.services.job_matcher import match_resume_to_job
from app.services.optimization_report_export import build_optimization_report, render_report_as_text


def _resume(**overrides) -> dict:
    base = {
        "summary": "Experienced engineer with a track record of delivering cloud platforms.",
        "skills": ["Python", "AWS"],
        "tools": ["Git"],
        "education": ["B.Tech"],
        "certifications": [],
        "experience": [
            {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
             "points": ["Led migration to AWS, reducing costs by 30%."], "is_career_break": False},
        ],
        "projects": [],
    }
    base.update(overrides)
    return base


def test_build_optimization_report_scoring_only():
    report = build_optimization_report(_resume())
    assert "scoring" in report
    assert "comparison" not in report
    assert "job_match" not in report
    assert report["persona"] == "general"


def test_build_optimization_report_includes_comparison_when_before_given():
    before = _resume(skills=["Python"])
    after = _resume(skills=["Python", "AWS", "Docker"])
    report = build_optimization_report(after, before=before)
    assert "comparison" in report
    assert "explanations" in report
    assert report["comparison"]["skills"]["added"]


def test_build_optimization_report_includes_job_match_when_given():
    resume = _resume()
    job_match = match_resume_to_job(resume, "Python and AWS required.")
    report = build_optimization_report(resume, job_match=job_match)
    assert report["job_match"] == job_match


def test_build_optimization_report_respects_persona():
    report = build_optimization_report(_resume(), persona="technical_recruiter")
    assert report["persona"] == "technical_recruiter"
    assert report["scoring"]["persona"] == "technical_recruiter"


def test_build_optimization_report_never_mutates_inputs():
    resume = _resume()
    before = _resume(skills=["Python"])
    resume_snapshot = dict(resume)
    before_snapshot = dict(before)
    build_optimization_report(resume, before=before)
    assert resume == resume_snapshot
    assert before == before_snapshot


def test_render_report_as_text_includes_score_and_sections():
    report = build_optimization_report(_resume())
    text = render_report_as_text(report)
    assert "OVERALL SCORE" in text
    assert "Warnings:" in text
    assert "Suggestions:" in text


def test_render_report_as_text_includes_comparison_section_when_present():
    before = _resume(skills=["Python"])
    after = _resume(skills=["Python", "AWS", "Docker"])
    report = build_optimization_report(after, before=before)
    text = render_report_as_text(report)
    assert "BEFORE vs AFTER COMPARISON" in text
    assert "WHAT CHANGED AND WHY" in text


def test_render_report_as_text_includes_job_match_section_when_present():
    resume = _resume()
    job_match = match_resume_to_job(resume, "Python and Kubernetes required.")
    report = build_optimization_report(resume, job_match=job_match)
    text = render_report_as_text(report)
    assert "JOB DESCRIPTION MATCH" in text
    assert "never fabricate" in text.lower() or "add only if genuinely applicable" in text.lower()


def test_render_report_as_text_handles_empty_lists_gracefully():
    report = build_optimization_report(_resume())
    text = render_report_as_text(report)
    assert isinstance(text, str) and text
