"""Unit tests for app/services/section_validation.py - Phase E of the
Resume Intelligence Engine redesign: the Source -> Canonical -> Entity-
Linked -> Rendered comparison report (extracted/duplicated/rendered/lost/
inferred per section)."""
from app.services.canonical_model import CanonicalResume, Employment, TextEntity
from app.services.section_validation import build_section_report, count_entities


def _resume(**kwargs) -> CanonicalResume:
    return CanonicalResume(**kwargs)


def test_count_entities_counts_every_section():
    resume = _resume(
        employments=[Employment(id="employment-1"), Employment(id="employment-2")],
        skills=[TextEntity(id="skill-1", value="Python")],
    )
    counts = count_entities(resume)
    assert counts["employments"] == 2
    assert counts["skills"] == 1
    assert counts["projects"] == 0


def test_build_section_report_no_change_at_any_checkpoint():
    pre = {"employments": 3, "projects": 1, "skills": 5, "tools": 0, "certifications": 0,
           "education": 0, "achievements": 0, "leadership": 0, "publications": 0,
           "languages": 0, "volunteer": 0}
    final_dict = {"experience": [{}, {}, {}], "projects": [{}], "skills": ["a"] * 5}
    report = build_section_report(pre, pre, final_dict)
    assert report["experience"] == {
        "extracted": 3, "duplicated": 0, "rendered": 3, "lost": 0, "inferred": False,
    }
    assert report["skills"] == {"extracted": 5, "duplicated": 0, "rendered": 5, "lost": 0, "inferred": False}


def test_build_section_report_reports_duplicates_merged_by_entity_linking():
    pre = {"employments": 4, "projects": 0, "skills": 0, "tools": 0, "certifications": 0,
           "education": 0, "achievements": 0, "leadership": 0, "publications": 0,
           "languages": 0, "volunteer": 0}
    post = {**pre, "employments": 2}  # Entity Linking merged 4 -> 2
    final_dict = {"experience": [{}, {}]}
    report = build_section_report(pre, post, final_dict)
    assert report["experience"]["extracted"] == 4
    assert report["experience"]["duplicated"] == 2
    assert report["experience"]["rendered"] == 2
    assert report["experience"]["lost"] == 0


def test_build_section_report_flags_a_real_loss_after_entity_linking():
    pre = {"employments": 0, "projects": 2, "skills": 0, "tools": 0, "certifications": 0,
           "education": 0, "achievements": 0, "leadership": 0, "publications": 0,
           "languages": 0, "volunteer": 0}
    post = dict(pre)  # Entity Linking kept both (no duplicates)
    final_dict = {"projects": [{}]}  # but only 1 made it to the final render - a real regression
    report = build_section_report(pre, post, final_dict)
    assert report["projects"]["duplicated"] == 0
    assert report["projects"]["lost"] == 1


def test_build_section_report_marks_inferred_sections_from_recovery_log():
    pre = {"employments": 0, "projects": 0, "skills": 0, "tools": 0, "certifications": 1,
           "education": 0, "achievements": 0, "leadership": 0, "publications": 0,
           "languages": 0, "volunteer": 0}
    final_dict = {"certifications": ["AWS Certified"]}
    report = build_section_report(pre, pre, final_dict, recovery_log={"certifications": "deterministic"})
    assert report["certifications"]["inferred"] is True


def test_build_section_report_defaults_inferred_to_false_without_a_recovery_log():
    pre = {k: 0 for k in ("employments", "projects", "skills", "tools", "certifications",
                           "education", "achievements", "leadership", "publications",
                           "languages", "volunteer")}
    report = build_section_report(pre, pre, {})
    assert all(section["inferred"] is False for section in report.values())


def test_build_section_report_covers_every_expected_legacy_section_key():
    pre = {k: 0 for k in ("employments", "projects", "skills", "tools", "certifications",
                           "education", "achievements", "leadership", "publications",
                           "languages", "volunteer")}
    report = build_section_report(pre, pre, {})
    assert set(report.keys()) == {
        "experience", "projects", "skills", "tools", "certifications", "education",
        "achievements", "leadership", "publications", "languages", "volunteer_experience",
    }
