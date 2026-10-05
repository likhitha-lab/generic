"""Unit tests for app/services/optimization_explainer.py - deterministic,
diff-grounded explanations of optimizations (Enterprise feature)."""
from app.services.optimization_explainer import explain_optimizations


def _resume(**overrides) -> dict:
    base = {
        "summary": "Engineer.",
        "skills": ["Python"],
        "experience": [
            {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
             "points": ["Did X."], "is_career_break": False},
        ],
        "projects": [],
    }
    base.update(overrides)
    return base


def test_explain_optimizations_reports_skill_additions():
    before = _resume(skills=["Python"])
    after = _resume(skills=["Python", "AWS", "Docker"])
    result = explain_optimizations(before, after)
    assert result["skills"]
    assert "AWS" in result["skills"][0] or "Docker" in result["skills"][0]


def test_explain_optimizations_reports_summary_rewrite():
    before = _resume(summary="Short.")
    after = _resume(summary="A fully rewritten, much longer professional summary about this candidate's career.")
    result = explain_optimizations(before, after)
    assert result["summary"]
    assert "expanded" in result["summary"][0]


def test_explain_optimizations_reports_bullet_compression():
    before = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": [f"Did {i}." for i in range(20)], "is_career_break": False},
    ])
    after = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Did a merged thing.", "Did another merged thing."], "is_career_break": False},
    ])
    result = explain_optimizations(before, after)
    assert result["experience"]
    assert "20" in result["experience"][0] and "2" in result["experience"][0]


def test_explain_optimizations_reports_score_change():
    before = _resume(summary="", skills=[], experience=[])
    after = _resume()
    result = explain_optimizations(before, after)
    assert result["scores"]
    assert "improved" in result["scores"][0] or "Score" in result["scores"][0]


def test_explain_optimizations_no_change_produces_no_explanations():
    resume = _resume()
    result = explain_optimizations(resume, resume)
    assert result["all"] == [] or all("0 point" not in item for item in result["all"])


def test_explain_optimizations_all_key_aggregates_every_group():
    before = _resume(skills=["Python"], summary="Short.")
    after = _resume(
        skills=["Python", "AWS"],
        summary="A fully rewritten, much longer professional summary about this candidate's career.",
    )
    result = explain_optimizations(before, after)
    assert len(result["all"]) == sum(len(v) for k, v in result.items() if k != "all")


def test_explain_optimizations_never_mutates_inputs():
    before = _resume()
    after = _resume(skills=["Python", "Docker"])
    before_snapshot = dict(before)
    after_snapshot = dict(after)
    explain_optimizations(before, after)
    assert before == before_snapshot
    assert after == after_snapshot


def test_explain_optimizations_handles_empty_dicts():
    result = explain_optimizations({}, {})
    assert result["all"] == []
