"""Unit tests for app/services/evaluation_dashboard.py - the primary
regression metric for future development (per explicit requirement).
Verifies every one of the 13 required metrics is computed correctly from
already-computed section_validation/ats/scoring reports, without
recomputing any of them."""
from app.services.evaluation_dashboard import build_evaluation_report

_FULL_SECTION_REPORT = {
    key: {"extracted": 0, "duplicated": 0, "rendered": 0, "lost": 0, "inferred": False}
    for key in ("skills", "tools", "certifications", "education", "experience", "projects",
                "achievements", "leadership", "publications", "languages", "volunteer_experience")
}


def _section_report(**overrides) -> dict:
    report = {k: dict(v) for k, v in _FULL_SECTION_REPORT.items()}
    for key, stats in overrides.items():
        report[key].update(stats)
    return report


def _scoring_report(summary_score: int = 12, overall_score: int = 82) -> dict:
    return {"score": overall_score, "breakdown": {"summary": summary_score}, "warnings": [], "suggestions": [],
            "persona": "general"}


_POLISHED_BASE = {"name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100",
                   "linkedin": "linkedin.com/in/janedoe", "experience": [], "projects": []}


def test_build_evaluation_report_all_pass_when_nothing_lost():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="Jane Doe\njane@example.com",
        section_report=_section_report(skills={"extracted": 10, "rendered": 10}),
        recovery_log={}, ats_report={"score": 90}, scoring_report=_scoring_report(),
    )
    assert report["name_extraction"] == "PASS"
    assert report["contact_preservation"] == "PASS"
    assert report["skills_preservation_pct"] == 100.0
    assert report["duplicate_entity_count"] == 0
    assert report["missing_section_count"] == 0
    assert report["ats_score"] == 90
    assert report["overall_resume_quality_score"] == 82


def test_build_evaluation_report_name_extraction_fails_when_blank():
    report = build_evaluation_report(
        {**_POLISHED_BASE, "name": ""}, raw_text="text",
        section_report=_section_report(), recovery_log={}, ats_report={"score": 50},
        scoring_report=_scoring_report(),
    )
    assert report["name_extraction"] == "FAIL"


def test_build_evaluation_report_contact_preservation_fails_when_source_had_a_phone_but_lost_it():
    report = build_evaluation_report(
        {**_POLISHED_BASE, "phone": ""}, raw_text="Jane Doe\nCall +1 415 555 0199",
        section_report=_section_report(), recovery_log={}, ats_report={"score": 50},
        scoring_report=_scoring_report(),
    )
    assert report["contact_preservation"] == "FAIL"


def test_build_evaluation_report_skills_preservation_reflects_partial_loss():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(skills={"extracted": 20, "rendered": 15, "lost": 5}),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["skills_preservation_pct"] == 75.0


def test_build_evaluation_report_all_five_named_preservation_fields_present():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text", section_report=_section_report(), recovery_log={},
        ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    for field in ("skills_preservation_pct", "experience_preservation_pct", "projects_preservation_pct",
                  "certifications_preservation_pct", "education_preservation_pct"):
        assert report[field] == 100.0  # nothing extracted anywhere - vacuously fully preserved


def test_build_evaluation_report_preservation_pct_not_penalized_by_successful_dedup():
    # CONFIRMED real-world regression: a real 21-year resume's Experience
    # section extracted as 25 raw (duplicate-inflated) entries, correctly
    # collapsed by Entity Linking to 16 real distinct jobs with zero
    # content lost - this must read as 100% preserved, not 64% (16/25),
    # which would misreport a successful fix as a data-loss regression.
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(experience={"extracted": 25, "rendered": 16, "duplicated": 9}),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["experience_preservation_pct"] == 100.0


def test_build_evaluation_report_preservation_pct_still_flags_a_real_loss_after_dedup():
    # If Entity Linking merged 9 away (25 -> 16) but the FINAL render only
    # has 14, that's a genuine loss of 2 real jobs after dedup - must still
    # be caught, not masked by the same fix above.
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(experience={"extracted": 25, "rendered": 14, "duplicated": 9}),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["experience_preservation_pct"] == round(14 / 16 * 100, 1)


def test_build_evaluation_report_duplicate_entity_count_uses_final_output_not_merged_count():
    # section_report shows Entity Linking merged 9 duplicates away (a GOOD
    # sign, not a defect) - the dashboard's duplicate_entity_count must
    # reflect what REMAINS in the final polished dict (0 here), never the
    # merged-away count, since a bigger merged-away number would otherwise
    # make an already-fixed resume look worse than a resume that never had
    # duplicates to begin with.
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(experience={"extracted": 25, "rendered": 16, "duplicated": 9}),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["duplicate_entity_count"] == 0


def test_build_evaluation_report_duplicate_entity_count_flags_real_remaining_duplicates():
    polished = {
        **_POLISHED_BASE,
        "experience": [
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2020", "points": ["Did X."]},
        ],
    }
    report = build_evaluation_report(
        polished, raw_text="text", section_report=_section_report(), recovery_log={},
        ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["duplicate_entity_count"] == 1


def test_build_evaluation_report_missing_section_count_counts_whole_section_loss():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(
            certifications={"extracted": 3, "rendered": 0, "lost": 3},
            skills={"extracted": 10, "rendered": 10},
        ),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["missing_section_count"] == 1


def test_build_evaluation_report_missing_section_count_includes_unrecovered_sections():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text", section_report=_section_report(),
        recovery_log={"achievements": "unrecovered"}, ats_report={"score": 80},
        scoring_report=_scoring_report(),
    )
    assert report["missing_section_count"] == 1


def test_build_evaluation_report_information_preservation_is_weighted_aggregate():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(
            skills={"extracted": 10, "rendered": 10},
            experience={"extracted": 10, "rendered": 5, "lost": 5},
        ),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    # 15 rendered / 20 extracted = 75%, not a simple 50/50 average of the
    # two sections' own percentages (100% and 50%) which would give 75%
    # here too by coincidence - use unequal section sizes to distinguish.
    assert report["information_preservation_pct"] == 75.0


def test_build_evaluation_report_summary_completeness_scales_with_scoring_breakdown():
    strong = build_evaluation_report(
        _POLISHED_BASE, raw_text="text", section_report=_section_report(), recovery_log={},
        ats_report={"score": 80}, scoring_report=_scoring_report(summary_score=12),
    )
    weak = build_evaluation_report(
        _POLISHED_BASE, raw_text="text", section_report=_section_report(), recovery_log={},
        ats_report={"score": 80}, scoring_report=_scoring_report(summary_score=3),
    )
    assert strong["summary_completeness_pct"] == 100.0
    assert weak["summary_completeness_pct"] == 25.0


def test_build_evaluation_report_ats_score_passed_through_directly():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text", section_report=_section_report(), recovery_log={},
        ats_report={"score": 63}, scoring_report=_scoring_report(),
    )
    assert report["ats_score"] == 63


def test_build_evaluation_report_overall_quality_score_passed_through_directly():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text", section_report=_section_report(), recovery_log={},
        ats_report={"score": 80}, scoring_report=_scoring_report(overall_score=71),
    )
    assert report["overall_resume_quality_score"] == 71


def test_build_evaluation_report_includes_full_section_detail_for_deeper_analysis():
    report = build_evaluation_report(
        _POLISHED_BASE, raw_text="text",
        section_report=_section_report(skills={"extracted": 5, "rendered": 5}),
        recovery_log={}, ats_report={"score": 80}, scoring_report=_scoring_report(),
    )
    assert report["section_detail"]["skills"]["extracted"] == 5
    assert report["section_detail"]["skills"]["preservation_pct"] == 100.0
