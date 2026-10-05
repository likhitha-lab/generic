"""Unit tests for app/services/ats_intelligence.py - the Phase 6 ATS
Intelligence Engine."""
from app.services.ats_intelligence import (
    check_section_level_ats_optimization,
    detect_and_fix_duplicate_keywords,
    detect_missing_keywords,
    generate_ats_report,
    get_priority_technologies,
    optimize_for_ats,
    rank_keywords_for_ats,
    standardize_bullet_technology_names,
    validate_keyword_density,
    validate_rendered_technology_names,
)


def _resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "jane@example.com",
        "summary": "Senior engineer with deep AWS and Python experience across cloud platforms.",
        "skills": ["Python", "AWS", "Docker"],
        "tools": ["Git"],
        "education": ["B.Tech Computer Science"],
        "certifications": ["AWS Certified"],
        "experience": [
            {"company": "Acme Corp", "role": "Senior Python Engineer", "duration": "2020-2024",
             "points": ["Built Python services on AWS.", "Deployed with Docker containers."],
             "is_career_break": False},
        ],
        "projects": [],
    }
    base.update(overrides)
    return base


# --- STEP 1: Standardized technology naming ----------------------------------

def test_standardize_bullet_technology_names_fixes_experience_bullets():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": [
            "Migrated workloads using Azure Kubernetes Service.",
            "Built the frontend in ReactJS.",
        ], "is_career_break": False},
    ])
    changed = standardize_bullet_technology_names(resume)
    assert changed == 2
    points = resume["experience"][0]["points"]
    assert "AKS" in points[0]
    assert "React" in points[1] and "ReactJS" not in points[1]


def test_standardize_bullet_technology_names_fixes_project_responsibilities():
    resume = _resume(projects=[
        {"title": "Tool", "responsibilities": ["Deployed via Visual Studio Code and Git Hub integration."]},
    ])
    changed = standardize_bullet_technology_names(resume)
    assert changed == 1
    resp = resume["projects"][0]["responsibilities"][0]
    assert "VS Code" in resp
    assert "GitHub" in resp


def test_standardize_bullet_technology_names_never_changes_unrelated_text():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": ["Led a team of five engineers on the roadmap."],
         "is_career_break": False},
    ])
    changed = standardize_bullet_technology_names(resume)
    assert changed == 0
    assert resume["experience"][0]["points"] == ["Led a team of five engineers on the roadmap."]


def test_standardize_bullet_technology_names_never_confuses_c_with_csharp():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": ["Wrote firmware in C for embedded devices."],
         "is_career_break": False},
    ])
    standardize_bullet_technology_names(resume)
    assert resume["experience"][0]["points"] == ["Wrote firmware in C for embedded devices."]


# --- Priority 4 fix: no more "Node.js.js" text corruption -------------------
# Root cause: the previous implementation ran one independent re.sub per
# variant, longest-first - a shorter variant ("node") could re-match a
# span a longer variant ("node.js") had already substituted, because the
# word-boundary regex treats "." as a non-word character. Fixed with a
# single combined-alternation pass (see _standardize_text's docstring).

def test_standardize_bullet_technology_names_never_duplicates_nodejs_suffix():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer",
         "points": ["Developed REST APIs using Node.js."], "is_career_break": False},
    ])
    standardize_bullet_technology_names(resume)
    point = resume["experience"][0]["points"][0]
    assert point == "Developed REST APIs using Node.js."
    assert "Node.js.js" not in point


def test_standardize_bullet_technology_names_normalizes_bare_node_variant():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer",
         "points": ["Built the backend in node."], "is_career_break": False},
    ])
    standardize_bullet_technology_names(resume)
    assert resume["experience"][0]["points"][0] == "Built the backend in Node.js."


def test_validate_rendered_technology_names_collapses_duplicated_suffix():
    # Defensive safety net - collapses the corruption pattern even if it
    # somehow still reached this point (e.g. from upstream Gemini-consolidated
    # text this module never wrote itself).
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer",
         "points": ["Developed REST APIs using Node.js.js."], "is_career_break": False},
    ])
    fixed = validate_rendered_technology_names(resume)
    assert fixed == 1
    assert resume["experience"][0]["points"][0] == "Developed REST APIs using Node.js."


def test_validate_rendered_technology_names_noop_on_clean_text():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer",
         "points": ["Developed REST APIs using Node.js."], "is_career_break": False},
    ])
    assert validate_rendered_technology_names(resume) == 0
    assert resume["experience"][0]["points"][0] == "Developed REST APIs using Node.js."


def test_optimize_for_ats_pipeline_never_produces_duplicated_suffix():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer",
         "points": ["Responsible for developing REST APIs using Node.js."], "is_career_break": False},
    ])
    parsed, _report = optimize_for_ats(resume)
    point = parsed["experience"][0]["points"][0]
    assert "Node.js.js" not in point
    assert "Node.js" in point


# --- STEP 2: Duplicate keyword detection -------------------------------------

def test_detect_and_fix_duplicate_keywords_merges_variants():
    resume = _resume(skills=["ReactJS", "React.js", "React", "AWS"])
    message = detect_and_fix_duplicate_keywords(resume)
    assert message is not None
    assert resume["skills"].count("React") == 1


def test_detect_and_fix_duplicate_keywords_returns_none_when_clean():
    resume = _resume(skills=["Python", "AWS"])
    assert detect_and_fix_duplicate_keywords(resume) is None


# --- STEP 3/4: ATS keyword ranking + Technology prioritization --------------

def test_rank_keywords_for_ats_favors_title_and_summary_mentions():
    resume = _resume(
        skills=["Python", "Docker"],
        summary="Senior Python engineer.",
        experience=[{"company": "Acme", "role": "Python Engineer", "duration": "2020-2024",
                     "points": ["Wrote some code."], "is_career_break": False}],
    )
    ranked = dict(rank_keywords_for_ats(resume))
    assert ranked["Python"] > ranked["Docker"]


def test_rank_keywords_for_ats_weights_recent_job_higher():
    resume = _resume(
        skills=["Python", "Java"],
        summary="",
        experience=[
            {"company": "Recent Co", "role": "Engineer", "duration": "2022-2024",
             "points": ["Built services in Python."], "is_career_break": False},
            {"company": "Old Co", "role": "Engineer", "duration": "2015-2018",
             "points": ["Wrote code in Java."], "is_career_break": False},
        ],
    )
    ranked = dict(rank_keywords_for_ats(resume))
    assert ranked["Python"] > ranked["Java"]


def test_rank_keywords_for_ats_handles_empty_skills():
    assert rank_keywords_for_ats(_resume(skills=[])) == []


def test_get_priority_technologies_respects_top_n():
    resume = _resume(skills=["Python", "AWS", "Docker", "Git", "Linux"])
    top = get_priority_technologies(resume, top_n=2)
    assert len(top) == 2


# --- STEP 5: Keyword density validation --------------------------------------

def test_validate_keyword_density_flags_low_coverage():
    resume = _resume(
        skills=["Python", "AWS", "Docker", "Kubernetes", "Terraform"],
        summary="Engineer.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Did general work."],
                     "is_career_break": False}],
    )
    result = validate_keyword_density(resume)
    assert result["coverage_ratio"] < 0.4
    assert result["warnings"]


def test_validate_keyword_density_passes_well_demonstrated_skills():
    resume = _resume()  # Python/AWS/Docker all mentioned in summary+bullets
    result = validate_keyword_density(resume)
    assert result["coverage_ratio"] == 1.0
    assert not result["warnings"]


def test_validate_keyword_density_flags_overused_keyword():
    bullets = ["Used Python for this task."] * 8
    resume = _resume(skills=["Python"], summary="", experience=[
        {"company": "Acme", "role": "Engineer", "points": bullets, "is_career_break": False},
    ])
    result = validate_keyword_density(resume)
    assert any("keyword-stuffed" in w for w in result["warnings"])


def test_validate_keyword_density_handles_no_skills():
    result = validate_keyword_density(_resume(skills=[]))
    assert result["coverage_ratio"] == 0.0
    assert result["warnings"] == []


# --- STEP 6: Missing keyword detection (never mutates, never invents) ------

def test_detect_missing_keywords_suggests_common_pairing():
    resume = _resume(skills=["Docker"], summary="", experience=[])
    suggestions = detect_missing_keywords(resume)
    assert "Kubernetes" in suggestions


def test_detect_missing_keywords_never_suggests_already_present_skill():
    resume = _resume(skills=["Docker", "Kubernetes"], summary="", experience=[])
    assert "Kubernetes" not in detect_missing_keywords(resume)


def test_detect_missing_keywords_never_mutates_parsed():
    resume = _resume(skills=["Docker"])
    before = dict(resume)
    detect_missing_keywords(resume)
    assert resume["skills"] == before["skills"]


def test_detect_missing_keywords_respects_cap():
    resume = _resume(skills=["Docker", "React", "Python", "Terraform", "Jenkins", "PostgreSQL"], summary="", experience=[])
    assert len(detect_missing_keywords(resume)) <= 5


# --- STEP 7: Section-level ATS optimization ----------------------------------

def test_check_section_level_flags_missing_critical_section():
    resume = _resume(education=[])
    warnings, _suggestions = check_section_level_ats_optimization(resume, coverage_ratio=1.0)
    assert any("Education" in w for w in warnings)


def test_check_section_level_flags_zero_coverage():
    resume = _resume()
    warnings, suggestions = check_section_level_ats_optimization(resume, coverage_ratio=0.0)
    assert any("only in the Skills section" in w for w in warnings)
    assert suggestions


def test_check_section_level_passes_when_healthy():
    resume = _resume()
    warnings, _suggestions = check_section_level_ats_optimization(resume, coverage_ratio=1.0)
    assert warnings == []


# --- Orchestration -------------------------------------------------------------

def test_generate_ats_report_shape():
    report = generate_ats_report(_resume())
    assert set(["score", "warnings", "suggestions", "priority_keywords",
                "keyword_coverage_ratio", "missing_keyword_suggestions"]).issubset(report.keys())
    assert 0 <= report["score"] <= 100


def test_optimize_for_ats_applies_fixes_and_returns_report():
    resume = _resume(
        skills=["ReactJS", "React.js", "React"],
        experience=[{"company": "Acme", "role": "Engineer",
                     "points": ["Migrated to Azure Kubernetes Service."], "is_career_break": False}],
    )
    parsed, report = optimize_for_ats(resume)
    assert parsed["skills"].count("React") == 1
    assert "AKS" in parsed["experience"][0]["points"][0]
    assert any("Standardized" in w for w in report["warnings"])
    assert any("duplicate technology keyword" in w for w in report["warnings"])


def test_optimize_for_ats_never_crashes_on_malformed_input():
    parsed, report = optimize_for_ats({"skills": None, "experience": None, "projects": None})
    assert isinstance(report["score"], int)


def test_optimize_for_ats_never_crashes_on_empty_dict():
    parsed, report = optimize_for_ats({})
    assert isinstance(report["score"], int)


def test_optimize_for_ats_no_fix_messages_when_resume_already_clean():
    resume = _resume()
    _parsed, report = optimize_for_ats(resume)
    assert not any("Standardized" in w for w in report["warnings"])
    assert not any("duplicate technology keyword" in w for w in report["warnings"])
