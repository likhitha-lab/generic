"""Unit tests for app/services/industry_intelligence.py - the Phase 7
Industry Intelligence Engine."""
from app.services.industry_intelligence import (
    INDUSTRIES,
    apply_industry_intelligence,
    apply_industry_wording,
    check_industry_ats_alignment,
    classify_industry,
    generate_industry_aligned_summary,
    get_industry_terminology,
)


def _resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "jane@example.com",
        "summary": "Experienced engineer with a strong delivery track record.",
        "skills": ["Python", "AWS"],
        "tools": ["Git"],
        "education": ["B.Tech Computer Science"],
        "certifications": [],
        "experience": [
            {"company": "Acme Corp", "role": "Engineer", "duration": "2018-2024",
             "points": ["Built services.", "Deployed infrastructure."], "is_career_break": False},
        ],
        "projects": [],
    }
    base.update(overrides)
    return base


# --- Classification ------------------------------------------------------------

def test_classify_industry_title_override_enterprise_architect():
    resume = _resume(experience=[{"company": "Acme", "role": "Enterprise Architect", "points": []}])
    assert classify_industry(resume) == "Enterprise Architect"


def test_classify_industry_title_override_solution_architect():
    resume = _resume(experience=[{"company": "Acme", "role": "Solution Architect", "points": []}])
    assert classify_industry(resume) == "Solution Architect"


def test_classify_industry_title_override_business_analyst():
    resume = _resume(experience=[{"company": "Acme", "role": "Business Analyst", "points": []}])
    assert classify_industry(resume) == "Business Analyst"


def test_classify_industry_title_override_project_manager():
    resume = _resume(experience=[{"company": "Acme", "role": "Project Manager", "points": []}])
    assert classify_industry(resume) == "Project Manager"


def test_classify_industry_skill_based_cyber_security():
    resume = _resume(skills=["Cybersecurity", "SIEM", "Penetration Testing"], experience=[])
    assert classify_industry(resume) == "Cyber Security"


def test_classify_industry_skill_based_machine_learning():
    resume = _resume(skills=["TensorFlow", "PyTorch", "Deep Learning"], experience=[])
    assert classify_industry(resume) == "Machine Learning"


def test_classify_industry_skill_based_azure_vs_aws():
    azure_resume = _resume(skills=["Azure", "Azure DevOps"], experience=[])
    aws_resume = _resume(skills=["AWS", "EC2", "S3"], experience=[])
    assert classify_industry(azure_resume) == "Azure"
    assert classify_industry(aws_resume) == "AWS"


def test_classify_industry_skill_based_dotnet():
    resume = _resume(skills=[".NET", "C#", "ASP.NET"], experience=[])
    assert classify_industry(resume) == ".NET"


def test_classify_industry_falls_back_to_default_when_nothing_matches():
    resume = _resume(skills=[], experience=[])
    assert classify_industry(resume) in INDUSTRIES


def test_all_industries_have_terminology():
    for industry in INDUSTRIES:
        terminology = get_industry_terminology(industry)
        assert terminology["priority_keywords"]
        assert terminology["recruiter_terms"]


# --- Industry-specific Professional Summary (never invents) -----------------

def test_generate_industry_aligned_summary_adds_clause_when_evidenced():
    resume = _resume(skills=["Azure", "Azure DevOps"], summary="Experienced cloud engineer.")
    summary = generate_industry_aligned_summary(resume, "Azure")
    assert summary.startswith("Experienced cloud engineer.")
    assert "Azure" in summary
    assert len(summary) > len(resume["summary"])


def test_generate_industry_aligned_summary_never_invents_unlisted_keyword():
    resume = _resume(skills=["Python"], summary="Backend engineer.")
    summary = generate_industry_aligned_summary(resume, "Cyber Security")
    # None of Cyber Security's priority keywords are in this candidate's
    # skills/summary - nothing safe to add, must return unchanged.
    assert summary == "Backend engineer."


def test_generate_industry_aligned_summary_skips_if_already_mentioned():
    resume = _resume(skills=["Python", "Django"], summary="Python developer skilled in Django.")
    summary = generate_industry_aligned_summary(resume, "Python")
    assert summary == "Python developer skilled in Django."


def test_generate_industry_aligned_summary_handles_empty_summary():
    resume = _resume(skills=["Python", "Django"], summary="")
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "Python" in summary
    assert not summary.startswith(" ")


# --- Industry-specific Experience wording (never touches facts) ------------

def test_apply_industry_wording_diversifies_repeated_opener():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": [
            "Developed the authentication service.", "Developed the reporting module.",
        ], "is_career_break": False},
    ])
    changed = apply_industry_wording(resume, "Cyber Security")
    points = resume["experience"][0]["points"]
    assert changed == 1
    assert points[0] == "Developed the authentication service."
    assert points[1] != "Developed the reporting module."
    assert points[1].endswith("the reporting module.")


def test_apply_industry_wording_never_touches_career_break():
    resume = _resume(experience=[
        {"company": "Career Break", "is_career_break": True, "points": ["Traveled.", "Took time off."]},
    ])
    original = dict(resume["experience"][0])
    apply_industry_wording(resume, "DevOps")
    assert resume["experience"][0] == original


def test_apply_industry_wording_no_change_when_no_repeats():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer",
         "points": ["Developed the API.", "Automated the pipeline."], "is_career_break": False},
    ])
    changed = apply_industry_wording(resume, "DevOps")
    assert changed == 0


# --- Industry-specific ATS optimization (suggestion-only) -------------------

def test_check_industry_ats_alignment_reports_present_and_missing():
    resume = _resume(skills=["Java", "Spring Boot"], experience=[])
    result = check_industry_ats_alignment(resume, "Java")
    assert "java" in result["present"] or "spring boot" in result["present"]
    assert result["missing"]
    assert 0 <= result["coverage_ratio"] <= 1


def test_check_industry_ats_alignment_never_mutates_parsed():
    resume = _resume(skills=["Java"])
    before = dict(resume)
    check_industry_ats_alignment(resume, "Java")
    assert resume["skills"] == before["skills"]


# --- Orchestration -------------------------------------------------------------

def test_apply_industry_intelligence_full_pipeline():
    # "Azure" only (no DevOps-keyword overlap like "Terraform"/"Azure
    # DevOps") for an unambiguous classification.
    resume = _resume(
        skills=["Azure", "Azure Functions", "Azure SQL"],
        summary="Cloud engineer.",
        experience=[
            {"company": "Acme", "role": "Cloud Engineer", "duration": "2018-2024",
             "points": ["Migrated the platform.", "Migrated the databases."], "is_career_break": False},
        ],
    )
    parsed, report = apply_industry_intelligence(resume)
    assert report["industry"] == "Azure"
    assert report["summary_updated"] is True
    assert report["bullets_updated"] >= 1
    assert "Azure" in parsed["summary"]
    assert parsed["experience"][0]["points"][1] != "Migrated the databases."


def test_apply_industry_intelligence_never_crashes_on_malformed_input():
    parsed, report = apply_industry_intelligence({"skills": None, "experience": None})
    assert report["industry"] in INDUSTRIES


def test_apply_industry_intelligence_never_crashes_on_empty_dict():
    parsed, report = apply_industry_intelligence({})
    assert report["industry"] in INDUSTRIES


def test_apply_industry_intelligence_preserves_non_summary_non_points_fields():
    resume = _resume()
    parsed, _report = apply_industry_intelligence(resume)
    assert parsed["name"] == "Jane Doe"
    assert parsed["experience"][0]["company"] == "Acme Corp"


# --- Professional Summary Improvement: leadership clause --------------------

def test_generate_industry_aligned_summary_adds_leadership_clause_when_title_evidences_it():
    resume = _resume(
        skills=["Python"],
        summary="Backend engineer.",
        experience=[{"company": "Acme", "role": "Engineering Manager", "points": ["Built services."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "leadership" in summary.lower()


def test_generate_industry_aligned_summary_adds_leadership_clause_when_bullet_evidences_it():
    resume = _resume(
        skills=["Python"],
        summary="Backend engineer.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Mentored junior engineers on best practices."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "leadership" in summary.lower()


def test_generate_industry_aligned_summary_no_leadership_clause_without_evidence():
    resume = _resume(
        skills=["Python"],
        summary="Backend engineer with strong delivery record.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Built services."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "leadership" not in summary.lower()


def test_generate_industry_aligned_summary_no_duplicate_leadership_mention():
    resume = _resume(
        skills=["Python"],
        summary="Engineering leader with a strong delivery record.",
        experience=[{"company": "Acme", "role": "Engineering Manager", "points": ["Built services."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert summary.lower().count("leader") == 1


def test_generate_industry_aligned_summary_both_clauses_can_coexist():
    resume = _resume(
        skills=["Python", "Django"],
        summary="Backend engineer.",
        experience=[{"company": "Acme", "role": "Engineering Manager", "points": ["Built services."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "python" in summary.lower()
    assert "leadership" in summary.lower()


# --- Executive Summary: pre-sales / enterprise-solution-design clauses ------

def test_generate_industry_aligned_summary_adds_presales_clause_when_evidenced():
    resume = _resume(
        skills=["Python"], summary="Backend engineer.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Led pre-sales solution proposals for enterprise clients."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "pre-sales" in summary.lower()


def test_generate_industry_aligned_summary_adds_enterprise_solution_clause_when_evidenced():
    resume = _resume(
        skills=["Python"], summary="Backend engineer.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Drove digital transformation across the platform."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "enterprise" in summary.lower()


def test_generate_industry_aligned_summary_no_presales_clause_without_evidence():
    resume = _resume(
        skills=["Python"], summary="Backend engineer with a strong delivery record.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Built services."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert "pre-sales" not in summary.lower()
    assert "enterprise solution" not in summary.lower()


def test_generate_industry_aligned_summary_never_lowercases_display_keywords():
    # Priority 4 fix - root cause: `priority_keywords` are stored all-
    # lowercase for matching, but were previously interpolated verbatim
    # into displayed prose ("...with hands-on expertise in java and spring
    # boot."), which reads as an unproofread typo.
    resume = _resume(skills=["Java", "Spring Boot"], summary="Backend engineer.")
    summary = generate_industry_aligned_summary(resume, "Java")
    assert "expertise in java" not in summary
    assert "expertise in Java" in summary


def test_generate_industry_aligned_summary_uses_acronym_override_casing():
    resume = _resume(skills=["AWS", "EC2"], summary="Cloud engineer.")
    summary = generate_industry_aligned_summary(resume, "AWS")
    assert "expertise in AWS" in summary
    assert "expertise in Aws" not in summary


def test_generate_industry_aligned_summary_never_duplicates_enterprise_mention():
    resume = _resume(
        skills=["Python"], summary="Enterprise software engineer.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Drove digital transformation across the platform."]}],
    )
    summary = generate_industry_aligned_summary(resume, "Python")
    assert summary.lower().count("enterprise") == 1


# --- Industry-specific templates: Informatica / SAP / QA ------------------

def test_classify_industry_title_override_sap():
    resume = _resume(experience=[{"company": "Acme", "role": "SAP FICO Consultant", "points": []}])
    assert classify_industry(resume) == "SAP"


def test_classify_industry_title_override_informatica():
    resume = _resume(experience=[{"company": "Acme", "role": "Informatica Developer", "points": []}])
    assert classify_industry(resume) == "Informatica"


def test_classify_industry_title_override_qa():
    resume = _resume(experience=[{"company": "Acme", "role": "QA Engineer", "points": []}])
    assert classify_industry(resume) == "QA"
    resume2 = _resume(experience=[{"company": "Acme", "role": "SDET", "points": []}])
    assert classify_industry(resume2) == "QA"


def test_classify_industry_skill_based_sap():
    resume = _resume(skills=["SAP HANA", "SAP ABAP", "S/4HANA"], experience=[])
    assert classify_industry(resume) == "SAP"


def test_classify_industry_skill_based_informatica():
    resume = _resume(skills=["Informatica PowerCenter", "IICS"], experience=[])
    assert classify_industry(resume) == "Informatica"


def test_classify_industry_skill_based_qa():
    resume = _resume(skills=["Selenium", "Test Automation", "Regression Testing"], experience=[])
    assert classify_industry(resume) == "QA"


def test_all_industries_have_terminology_including_new_ones():
    for industry in ("SAP", "Informatica", "QA"):
        assert industry in INDUSTRIES
        terminology = get_industry_terminology(industry)
        assert terminology["priority_keywords"]
        assert terminology["recruiter_terms"]


def test_generate_industry_aligned_summary_sap_never_lowercases_acronyms():
    resume = _resume(skills=["SAP", "SAP HANA"], summary="Backend engineer.")
    summary = generate_industry_aligned_summary(resume, "SAP")
    assert "expertise in sap and sap hana" not in summary
    assert "expertise in SAP and SAP HANA" in summary


def test_apply_industry_wording_qa_uses_automation_theme_verbs():
    resume = _resume(experience=[
        {"company": "Acme", "role": "QA Engineer", "points": [
            "Automated the regression suite.", "Automated the smoke test pipeline.",
        ], "is_career_break": False},
    ])
    changed = apply_industry_wording(resume, "QA")
    points = resume["experience"][0]["points"]
    assert changed == 1
    assert points[0] == "Automated the regression suite."
    assert points[1] != "Automated the smoke test pipeline."
    assert points[1].endswith("the smoke test pipeline.")


def test_apply_industry_intelligence_full_pipeline_sap():
    resume = _resume(
        skills=["SAP", "SAP HANA", "SAP FICO"],
        summary="Backend consultant.",
        experience=[
            {"company": "Acme", "role": "SAP Consultant", "duration": "2018-2024",
             "points": ["Configured SAP FICO modules.", "Configured SAP MM modules."], "is_career_break": False},
        ],
    )
    parsed, report = apply_industry_intelligence(resume)
    assert report["industry"] == "SAP"
    assert "SAP" in parsed["summary"]
