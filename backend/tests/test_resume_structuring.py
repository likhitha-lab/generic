"""Unit tests for app/services/resume_structuring.py - the Phase 4 Resume
Structuring Engine (Seniority Detection, Role Classification, Section
Templates, Executive Section Generator, Page Optimization)."""
from app.services.resume_structuring import (
    ROLE_CATEGORIES,
    SENIORITY_LEVELS,
    build_structure_plan,
    classify_role,
    detect_seniority,
    determine_page_budget,
    generate_executive_sections,
    layout_family_for_seniority,
)


def _resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "jane@example.com",
        "summary": "Experienced engineer.",
        "skills": ["Python", "AWS"],
        "tools": ["Git"],
        "education": ["B.Tech"],
        "certifications": ["AWS Certified"],
        "experience": [],
        "projects": [],
        "achievements": [],
    }
    base.update(overrides)
    return base


# --- STEP 3: Seniority Detection ---------------------------------------------

def test_detect_seniority_title_override_cto():
    resume = _resume(experience=[{"company": "Acme", "role": "Chief Technology Officer", "points": []}])
    assert detect_seniority(resume) == "CTO"


def test_detect_seniority_title_override_vp_engineering():
    resume = _resume(experience=[{"company": "Acme", "role": "VP Engineering", "points": []}])
    assert detect_seniority(resume) == "VP Engineering"


def test_detect_seniority_title_override_director():
    resume = _resume(experience=[{"company": "Acme", "role": "Director of Engineering", "points": []}])
    assert detect_seniority(resume) == "Director"


def test_detect_seniority_title_override_engineering_manager():
    resume = _resume(experience=[{"company": "Acme", "role": "Engineering Manager", "points": []}])
    assert detect_seniority(resume) == "Engineering Manager"


def test_detect_seniority_title_override_enterprise_architect():
    resume = _resume(experience=[{"company": "Acme", "role": "Enterprise Architect", "points": []}])
    assert detect_seniority(resume) == "Enterprise Architect"


def test_detect_seniority_title_override_solution_architect():
    resume = _resume(experience=[{"company": "Acme", "role": "Solution Architect", "points": []}])
    assert detect_seniority(resume) == "Solution Architect"


def test_detect_seniority_title_override_principal_engineer():
    resume = _resume(experience=[{"company": "Acme", "role": "Principal Engineer", "points": []}])
    assert detect_seniority(resume) == "Principal Engineer"


def test_detect_seniority_title_override_staff_engineer():
    resume = _resume(experience=[{"company": "Acme", "role": "Staff Engineer", "points": []}])
    assert detect_seniority(resume) == "Staff Engineer"


def test_detect_seniority_title_override_lead_engineer():
    resume = _resume(experience=[{"company": "Acme", "role": "Technical Lead", "points": []}])
    assert detect_seniority(resume) == "Lead Engineer"


def test_detect_seniority_title_override_senior_engineer():
    resume = _resume(experience=[{"company": "Acme", "role": "Senior Software Engineer", "points": []}])
    assert detect_seniority(resume) == "Senior Engineer"


def test_detect_seniority_title_override_junior_and_intern():
    resume = _resume(experience=[{"company": "Acme", "role": "Junior Developer", "points": []}])
    assert detect_seniority(resume) == "Junior Engineer"
    resume2 = _resume(experience=[{"company": "Acme", "role": "Software Engineering Intern", "points": []}])
    assert detect_seniority(resume2) == "Intern"


def test_detect_seniority_no_experience_is_fresher():
    resume = _resume(experience=[])
    assert detect_seniority(resume) == "Fresher"


def test_detect_seniority_never_relies_only_on_years():
    # No explicit title, but heavy leadership/architecture/business signal
    # should score meaningfully higher than a same-years candidate with
    # none of those signals.
    light_signal = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2015-2024",
         "points": ["Wrote code.", "Fixed bugs."]},
    ])
    heavy_signal = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2015-2024",
         "points": [
             "Led team of 15 engineers across the platform architecture initiative.",
             "Directed the solution architecture for the enterprise-wide migration.",
             "Mentored junior engineers and owned the technology roadmap and budget.",
         ]},
    ])
    light_index = SENIORITY_LEVELS.index(detect_seniority(light_signal))
    heavy_index = SENIORITY_LEVELS.index(detect_seniority(heavy_signal))
    assert heavy_index > light_index


# --- STEP 4: Role Classification ---------------------------------------------

def test_classify_role_title_match():
    resume = _resume(experience=[{"company": "Acme", "role": "Site Reliability Engineer", "points": []}])
    assert classify_role(resume) == "Site Reliability Engineer"


def test_classify_role_dotnet_title():
    resume = _resume(experience=[{"company": "Acme", "role": ".NET Developer", "points": []}])
    assert classify_role(resume) == ".NET Developer"


def test_classify_role_falls_back_to_skills():
    resume = _resume(experience=[{"company": "Acme", "role": "Engineer", "points": []}],
                      skills=["Python", "Django", "Flask"])
    assert classify_role(resume) == "Python Developer"


def test_classify_role_detects_full_stack_from_skills():
    resume = _resume(experience=[{"company": "Acme", "role": "Engineer", "points": []}],
                      skills=["React", "Angular", "Node.js", "Express"])
    assert classify_role(resume) == "Full Stack Developer"


def test_classify_role_java_keyword_never_matches_inside_javascript():
    # "java" is a literal substring of "javascript" - a naive substring
    # count would wrongly credit "Java Developer" for a JavaScript-only
    # skill list.
    resume = _resume(experience=[{"company": "Acme", "role": "Engineer", "points": []}],
                      skills=["JavaScript", "React", "TypeScript"])
    assert classify_role(resume) != "Java Developer"


def test_detect_seniority_director_title_never_matches_cto_via_substring():
    # "director" literally contains the substring "cto" - must not
    # misclassify as CTO.
    resume = _resume(experience=[{"company": "Acme", "role": "Director of Engineering", "points": []}])
    assert detect_seniority(resume) == "Director"


def test_classify_role_defaults_when_nothing_matches():
    resume = _resume(experience=[], skills=[])
    assert classify_role(resume) in ROLE_CATEGORIES


def test_all_role_categories_are_distinct():
    assert len(ROLE_CATEGORIES) == len(set(ROLE_CATEGORIES))


# --- Layout family mapping ----------------------------------------------------

def test_layout_family_covers_every_seniority_level():
    for level in SENIORITY_LEVELS:
        assert layout_family_for_seniority(level) in {"fresher", "junior", "mid", "senior", "architect", "manager"}


def test_layout_family_fresher_and_intern():
    assert layout_family_for_seniority("Fresher") == "fresher"
    assert layout_family_for_seniority("Intern") == "fresher"


def test_layout_family_architect_group():
    for level in ("Principal Engineer", "Architect", "Solution Architect", "Enterprise Architect"):
        assert layout_family_for_seniority(level) == "architect"


def test_layout_family_manager_group():
    for level in ("Engineering Manager", "Director", "VP Engineering", "CTO"):
        assert layout_family_for_seniority(level) == "manager"


# --- STEP 7: Executive Section Generator (never invents) --------------------

def test_generate_executive_sections_derives_internships():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Software Engineering Intern", "points": ["Assisted with testing."]},
    ])
    extra = generate_executive_sections(resume)
    assert "internships" in extra
    assert extra["internships"][0]["role"] == "Software Engineering Intern"


def test_generate_executive_sections_no_internships_key_when_none_found():
    resume = _resume(experience=[{"company": "Acme", "role": "Senior Engineer", "points": []}])
    assert "internships" not in generate_executive_sections(resume)


def test_generate_executive_sections_derives_leadership_achievements_from_existing_content_only():
    resume = _resume(
        achievements=["Reduced costs by 40%."],
        experience=[{"company": "Acme", "role": "Manager", "duration": "2015-2024",
                     "points": ["Led team of 12 engineers on the platform migration."]}],
    )
    extra = generate_executive_sections(resume)
    assert "Reduced costs by 40%." in extra["leadership_achievements"]
    assert "Led team of 12 engineers on the platform migration." in extra["leadership_achievements"]
    # Every derived item must be traceable to something already in the input.
    for item in extra["leadership_achievements"]:
        assert item in resume["achievements"] or any(
            item in (exp.get("points") or []) for exp in resume["experience"]
        )


def test_generate_executive_sections_caps_low_priority_sections_for_long_careers():
    resume = _resume(
        certifications=[f"Cert {i}" for i in range(10)],
        education=[f"Degree {i}" for i in range(5)],
        experience=[{"company": "Acme", "role": "Engineer", "duration": "2005-2024", "points": []}],
    )
    extra = generate_executive_sections(resume)
    assert len(extra["certifications"]) == 5
    assert len(extra["education"]) == 3
    assert extra["certifications"] == resume["certifications"][:5]


def test_generate_executive_sections_does_not_cap_short_careers():
    resume = _resume(
        certifications=[f"Cert {i}" for i in range(10)],
        experience=[{"company": "Acme", "role": "Engineer", "duration": "2022-2024", "points": []}],
    )
    extra = generate_executive_sections(resume)
    assert "certifications" not in extra


# --- STEP 8: Page Optimization -------------------------------------------------

def test_determine_page_budget_boundaries():
    # Bracket updated to match the new Talent Acquisition-approved spec:
    # under 5y -> 2 pages, 5-10y -> 3, 10-15y -> 4, 15+y -> 5 (lower-bound
    # inclusive - see file_generator.py's own _PAGE_BUDGET_BRACKETS).
    assert determine_page_budget(0) == 2
    assert determine_page_budget(4.9) == 2
    assert determine_page_budget(5) == 3
    assert determine_page_budget(9.9) == 3
    assert determine_page_budget(10) == 4
    assert determine_page_budget(14.9) == 4
    assert determine_page_budget(15) == 5
    assert determine_page_budget(25) == 5


# --- Orchestration: build_structure_plan -------------------------------------

def test_build_structure_plan_fresher():
    resume = _resume(experience=[])
    plan = build_structure_plan(resume)
    assert plan.seniority == "Fresher"
    keys = [key for key, _label in plan.sections]
    assert keys.index("education") < keys.index("skills")
    assert "internships" in keys


def test_build_structure_plan_senior_engineer_puts_core_expertise_after_summary():
    resume = _resume(experience=[{"company": "Acme", "role": "Senior Engineer", "duration": "2015-2024", "points": []}])
    plan = build_structure_plan(resume)
    keys = [key for key, _label in plan.sections]
    assert keys[0] == "summary"
    assert keys[1] == "core_expertise"


def test_build_structure_plan_manager_puts_leadership_achievements_above_technical_skills():
    resume = _resume(experience=[{"company": "Acme", "role": "Engineering Manager", "duration": "2010-2024", "points": []}])
    plan = build_structure_plan(resume)
    keys = [key for key, _label in plan.sections]
    assert keys.index("leadership_achievements") < keys.index("technology_portfolio")


def test_build_structure_plan_architect_uses_enterprise_experience_and_transformation_programs():
    resume = _resume(experience=[{"company": "Acme", "role": "Solution Architect", "duration": "2012-2024", "points": []}])
    plan = build_structure_plan(resume)
    keys = [key for key, _label in plan.sections]
    assert "enterprise_experience" in keys
    assert "major_transformation_programs" in keys


def test_build_structure_plan_max_pages_scales_with_years():
    junior = _resume(experience=[{"company": "Acme", "role": "Engineer", "duration": "2023-2024", "points": []}])
    veteran = _resume(experience=[{"company": "Acme", "role": "Engineer", "duration": "2005-2024", "points": []}])
    assert build_structure_plan(junior).max_pages < build_structure_plan(veteran).max_pages


def test_build_structure_plan_never_produces_duplicate_section_keys():
    for role in ["", "Senior Engineer", "Engineering Manager", "Solution Architect"]:
        resume = _resume(experience=[{"company": "Acme", "role": role, "duration": "2015-2024", "points": []}])
        keys = [key for key, _label in build_structure_plan(resume).sections]
        assert len(keys) == len(set(keys))
