"""Unit tests for app/services/resume_quality_engine.py - the Phase 5
Resume Quality & Validation Engine (automatic fixes + 15-check report)."""
from app.services.resume_quality_engine import (
    _WEIGHTS,
    _apply_automatic_fixes,
    _check_excessive_skills,
    _check_excessive_tools,
    _check_keyword_stuffing,
    _check_missing_business_impact,
    _check_resume_length,
    _fix_weak_verb_opening,
    _merge_near_duplicate_projects,
    fix_repeated_words,
    fix_truncated_sentence,
    run_quality_engine,
)
from app.services.resume_quality_checker import resume_has_minimum_viable_content


def _resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "jane@example.com",
        "summary": (
            "Senior software engineer with 8 years of experience architecting cloud-native systems "
            "on AWS, leading engineering teams and delivering measurable business impact."
        ),
        "skills": ["Python", "AWS", "Docker"],
        "tools": ["Git"],
        "education": ["B.Tech Computer Science"],
        "certifications": ["AWS Certified Solutions Architect"],
        "experience": [
            {"company": "Acme Corp", "role": "Senior Engineer", "duration": "2016-2024",
             "points": ["Led migration of legacy systems to AWS, reducing costs by 30%.",
                        "Automated deployment pipelines improving release velocity by 40%."],
             "is_career_break": False},
        ],
        "projects": [],
        "achievements": [],
    }
    base.update(overrides)
    return base


# --- Weight invariant ----------------------------------------------------------

def test_weights_sum_to_100():
    assert sum(_WEIGHTS.values()) == 100


# --- _fix_weak_verb_opening ----------------------------------------------------

def test_fix_weak_verb_opening_worked_on():
    fixed, changed = _fix_weak_verb_opening("Worked on backend systems for the platform.")
    assert changed
    assert fixed == "Contributed to backend systems for the platform."


def test_fix_weak_verb_opening_responsible_for():
    # Priority 5 fix - "responsible for" no longer maps to "Managed", which
    # overstated ownership/authority the original phrase didn't necessarily
    # claim; "Contributed to" is conservative (task-scoped, not authority-
    # scoped) and grammatically fits both a gerund and a noun-phrase tail.
    fixed, changed = _fix_weak_verb_opening("Responsible for managing the deployment pipeline.")
    assert changed
    assert fixed.startswith("Contributed to")


def test_fix_weak_verb_opening_never_touches_strong_verb_bullets():
    bullet = "Architected the microservices platform serving 10M requests per day."
    fixed, changed = _fix_weak_verb_opening(bullet)
    assert not changed
    assert fixed == bullet


def test_fix_weak_verb_opening_preserves_rest_of_sentence_verbatim():
    bullet = "Handled the migration of 500+ applications to Kubernetes."
    fixed, changed = _fix_weak_verb_opening(bullet)
    assert changed
    # Everything after the weak phrase must be byte-for-byte unchanged -
    # no fact/technology/metric may be altered, only the opening verb.
    assert fixed.endswith(" the migration of 500+ applications to Kubernetes.")


# --- _merge_near_duplicate_projects --------------------------------------------

def test_merge_near_duplicate_projects_merges_similar_content():
    projects = [
        {"title": "Customer Portal", "description": "Built a customer-facing self-service portal."},
        {"title": "Customer Portal Redesign", "description": "Built a customer facing self service portal."},
    ]
    kept, removed = _merge_near_duplicate_projects(projects)
    assert removed == 1
    assert len(kept) == 1


def test_merge_near_duplicate_projects_keeps_genuinely_different_projects():
    projects = [
        {"title": "Customer Portal", "description": "Built a customer-facing portal."},
        {"title": "Internal Analytics Dashboard", "description": "Built an internal analytics tool."},
    ]
    kept, removed = _merge_near_duplicate_projects(projects)
    assert removed == 0
    assert len(kept) == 2


# --- New quality checks ---------------------------------------------------------

def test_check_keyword_stuffing_detects_comma_stuffed_summary():
    warnings, suggestions = [], []
    resume = _resume(summary="Skilled in " + ", ".join(f"Tech{i}" for i in range(15)) + ".")
    score = _check_keyword_stuffing(resume, warnings, suggestions)
    assert score < 6
    assert warnings


def test_check_keyword_stuffing_passes_natural_prose():
    warnings, suggestions = [], []
    resume = _resume()
    score = _check_keyword_stuffing(resume, warnings, suggestions)
    assert score == 6
    assert not warnings


def test_check_excessive_skills_flags_over_cap():
    warnings, suggestions = [], []
    resume = _resume(skills=[f"Skill{i}" for i in range(40)])
    score = _check_excessive_skills(resume, warnings, suggestions)
    assert score < 6
    assert warnings


def test_check_excessive_tools_flags_over_cap():
    warnings, suggestions = [], []
    resume = _resume(tools=[f"Tool{i}" for i in range(25)])
    score = _check_excessive_tools(resume, warnings, suggestions)
    assert score < 6
    assert warnings


def test_check_missing_business_impact_flags_low_ratio():
    warnings, suggestions = [], []
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": ["Attended meetings.", "Wrote documentation.",
                                                             "Reviewed code.", "Updated tickets."]},
    ])
    score = _check_missing_business_impact(resume, warnings, suggestions)
    assert score < 7
    assert warnings


def test_check_missing_business_impact_passes_when_metrics_present():
    warnings, suggestions = [], []
    resume = _resume()
    score = _check_missing_business_impact(resume, warnings, suggestions)
    assert score == 7


def test_check_resume_length_flags_long_content_for_short_career():
    warnings, suggestions = [], []
    resume = _resume(
        experience=[{"company": "Acme", "role": "Engineer", "duration": "2023-2024",
                     "points": [f"Did task {i} involving substantial detailed work across many systems." for i in range(200)]}],
    )
    score = _check_resume_length(resume, warnings, suggestions)
    assert score < 7
    assert warnings


def test_check_resume_length_passes_reasonable_content():
    warnings, suggestions = [], []
    resume = _resume()
    score = _check_resume_length(resume, warnings, suggestions)
    assert score == 7


# --- _apply_automatic_fixes (never invents facts) ------------------------------

def test_apply_automatic_fixes_dedupes_technologies_via_alias_reprocessing():
    resume = _resume(skills=["ReactJS", "React.js", "React", "AWS"])
    changes = _apply_automatic_fixes(resume)
    assert resume["skills"].count("React") == 1
    assert any("technology" in c.lower() for c in changes)


def test_apply_automatic_fixes_rewrites_weak_verb_bullets():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Worked on backend services for the platform.", "Led the migration to AWS."],
         "is_career_break": False},
    ])
    _apply_automatic_fixes(resume)
    points = resume["experience"][0]["points"]
    assert not any(p.lower().startswith("worked on") for p in points)
    # The factual content (what was worked on) must survive unchanged.
    assert any("backend services for the platform" in p for p in points)


def test_apply_automatic_fixes_merges_redundant_projects():
    resume = _resume(projects=[
        {"title": "Customer Portal", "description": "Built a customer-facing self-service portal.",
         "responsibilities": ["Built the frontend."]},
        {"title": "Customer Portal Redesign", "description": "Built a customer facing self service portal.",
         "responsibilities": ["Built the backend."]},
    ])
    changes = _apply_automatic_fixes(resume)
    assert len(resume["projects"]) == 1
    assert any("redundant project" in c.lower() for c in changes)


def test_apply_automatic_fixes_never_touches_career_break_entries():
    resume = _resume(experience=[
        {"company": "Career Break", "is_career_break": True, "points": ["Traveled.", "Took time off."]},
    ])
    original = dict(resume["experience"][0])
    _apply_automatic_fixes(resume)
    assert resume["experience"][0] == original


def test_apply_automatic_fixes_returns_empty_list_when_nothing_needs_fixing():
    resume = _resume()
    changes = _apply_automatic_fixes(resume)
    assert changes == []


# --- run_quality_engine (full orchestration) -----------------------------------

def test_run_quality_engine_returns_expected_report_shape():
    parsed, report = run_quality_engine(_resume())
    assert set(["score", "warnings", "suggestions", "breakdown", "section_recovery"]).issubset(report.keys())
    assert isinstance(report["score"], int)
    assert 0 <= report["score"] <= 100
    assert set(report["breakdown"].keys()) == set(_WEIGHTS.keys())


def test_run_quality_engine_defaults_section_recovery_to_empty_dict():
    # Manual Resume Generator flow has no raw source text to recover from -
    # not passing recovery_log must behave identically to before this
    # parameter existed, with an empty (not missing) "section_recovery" key.
    _parsed, report = run_quality_engine(_resume())
    assert report["section_recovery"] == {}


def test_run_quality_engine_surfaces_the_given_recovery_log():
    recovery_log = {"certifications": "retry", "languages": "unrecovered"}
    _parsed, report = run_quality_engine(_resume(), recovery_log=recovery_log)
    assert report["section_recovery"] == recovery_log


def test_run_quality_engine_strong_resume_scores_highly():
    _parsed, report = run_quality_engine(_resume())
    assert report["score"] >= 75, report


def test_run_quality_engine_never_crashes_on_malformed_input():
    malformed = {"name": None, "email": None, "skills": None, "experience": None}
    parsed, report = run_quality_engine(malformed)
    assert isinstance(report["score"], int)


def test_run_quality_engine_never_crashes_on_empty_dict():
    parsed, report = run_quality_engine({})
    assert isinstance(report["score"], int)


def test_run_quality_engine_gate_still_works_for_genuinely_empty_resume():
    parsed, _report = run_quality_engine({})
    assert not resume_has_minimum_viable_content(parsed)


def test_run_quality_engine_gate_passes_for_thin_but_real_resume():
    parsed, _report = run_quality_engine({"name": "Jane Doe"})
    assert resume_has_minimum_viable_content(parsed)


def test_run_quality_engine_applies_fixes_and_reports_them():
    resume = _resume(skills=["ReactJS", "React.js", "React"])
    parsed, report = run_quality_engine(resume)
    assert parsed["skills"].count("React") == 1
    assert report["automatic_fixes_applied"]


# --- Repeated-word correction (this round's Priority 2 fix) -----------------

def test_fix_repeated_words_adjacent_duplicate():
    assert fix_repeated_words("Production Production support") == "Production support"


def test_fix_repeated_words_conjunction_pattern():
    assert fix_repeated_words("Delivered and delivered the project on time.") == "Delivered the project on time."


def test_fix_repeated_words_case_insensitive_conjunction():
    assert fix_repeated_words("Managed and managed the release process.") == "Managed the release process."


def test_fix_repeated_words_noop_on_clean_text():
    text = "Delivered the project on time and under budget."
    assert fix_repeated_words(text) == text


def test_fix_repeated_words_does_not_touch_different_adjacent_words():
    text = "Designed and developed the platform."
    assert fix_repeated_words(text) == text


# --- Truncated-sentence correction (this round's Priority 3 fix) -----------

def test_fix_truncated_sentence_trims_to_last_clause_boundary():
    text = "Contributed to Business needs assessment, designed solutions, and estimated the effo."
    result = fix_truncated_sentence(text)
    assert result == "Contributed to Business needs assessment, designed solutions."
    assert not result.rstrip(".").split()[-1] == "effo"


def test_fix_truncated_sentence_noop_on_complete_sentence():
    text = "Delivered the project on time and under budget."
    assert fix_truncated_sentence(text) == text


def test_fix_truncated_sentence_noop_on_legitimate_short_ending_word():
    for text in ["Managed the full project team.", "Reduced overall delivery risk.", "Reviewed the source code."]:
        assert fix_truncated_sentence(text) == text


def test_fix_truncated_sentence_drops_bullet_with_no_salvageable_clause():
    assert fix_truncated_sentence("Estimated the effo.") == ""


def test_fix_truncated_sentence_ignores_text_without_trailing_period():
    text = "Estimated the effo"
    assert fix_truncated_sentence(text) == text


# --- Wiring: _apply_automatic_fixes applies both mechanics fixes -----------

def test_apply_automatic_fixes_fixes_repeated_word_in_experience_bullet():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Production Production support for critical systems.", "Built new features."],
         "is_career_break": False},
    ])
    _apply_automatic_fixes(resume)
    assert resume["experience"][0]["points"][0] == "Production support for critical systems."


def test_apply_automatic_fixes_fixes_truncated_bullet_in_experience():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": [
             "Contributed to Business needs assessment, designed solutions, and estimated the effo.",
             "Built new features for the platform.",
         ], "is_career_break": False},
    ])
    _apply_automatic_fixes(resume)
    points = resume["experience"][0]["points"]
    assert "effo" not in " ".join(points)
    assert "Contributed to Business needs assessment, designed solutions." in points


def test_apply_automatic_fixes_fixes_repeated_word_in_summary():
    resume = _resume(summary="Delivered and delivered enterprise-scale platforms for global clients.")
    _apply_automatic_fixes(resume)
    assert resume["summary"] == "Delivered enterprise-scale platforms for global clients."
