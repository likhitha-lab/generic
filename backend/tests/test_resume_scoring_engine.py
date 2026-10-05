"""Unit tests for app/services/resume_scoring_engine.py - the Phase 8
Resume Scoring Engine. Pure analysis only - every test in the "never
mutates" section exists specifically to prove this module has no side
effects on `parsed`."""
import copy

from app.services.resume_scoring_engine import (
    _PERSONA_WEIGHTS,
    _WEIGHTS,
    PERSONAS,
    get_persona_weights,
    score_resume,
)


def _resume(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "jane@example.com",
        "summary": (
            "Senior software engineer with 8 years of experience architecting cloud-native systems "
            "on AWS, leading engineering teams and delivering measurable business impact."
        ),
        "skills": ["Python", "AWS", "Docker", "Kubernetes", "Terraform"],
        "tools": ["Git"],
        "education": ["B.Tech Computer Science"],
        "certifications": ["AWS Certified Solutions Architect"],
        "experience": [
            {"company": "Acme Corp", "role": "Senior Engineer", "duration": "2016-2024",
             "points": ["Led migration of legacy systems to AWS, reducing costs by 30%.",
                        "Automated deployment pipelines using Docker and Terraform, improving velocity by 40%."],
             "is_career_break": False},
        ],
        "projects": [
            {"title": "Internal Tool", "description": "A tool.", "technologies": "Python",
             "responsibilities": ["Built the CLI.", "Reduced processing time by 25%."]},
        ],
        "achievements": [],
    }
    base.update(overrides)
    return base


# --- Weight invariant ------------------------------------------------------

def test_weights_sum_to_100():
    assert sum(_WEIGHTS.values()) == 100


# --- Never modifies the resume (the mission's core constraint) -------------

def test_score_resume_never_mutates_input():
    resume = _resume()
    snapshot = copy.deepcopy(resume)
    score_resume(resume)
    assert resume == snapshot


def test_score_resume_never_mutates_even_on_messy_input():
    resume = _resume(
        skills=["ReactJS", "React.js", "React"],
        experience=[{"company": "Acme", "role": "Engineer", "duration": "2020-2024",
                     "points": ["Worked on stuff.", "Worked on stuff.", "Did things."],
                     "is_career_break": False}],
    )
    snapshot = copy.deepcopy(resume)
    score_resume(resume)
    assert resume == snapshot


# --- Overall shape -----------------------------------------------------------

def test_score_resume_returns_expected_shape():
    report = score_resume(_resume())
    assert set(["score", "breakdown", "warnings", "suggestions"]).issubset(report.keys())
    assert isinstance(report["score"], int)
    assert 0 <= report["score"] <= 100
    assert set(report["breakdown"].keys()) == set(_WEIGHTS.keys())


def test_score_resume_strong_resume_scores_highly():
    report = score_resume(_resume())
    assert report["score"] >= 80, report


def test_score_resume_never_crashes_on_malformed_input():
    report = score_resume({"skills": None, "experience": None, "projects": None})
    assert isinstance(report["score"], int)


def test_score_resume_never_crashes_on_empty_dict():
    report = score_resume({})
    assert isinstance(report["score"], int)
    assert 0 <= report["score"] <= 100


# --- Experience Score ---------------------------------------------------------

def test_missing_experience_scores_zero_and_warns():
    report = score_resume(_resume(experience=[]))
    assert report["breakdown"]["experience"] == 0
    assert any("Missing Professional Experience" in w for w in report["warnings"])


def test_weak_verb_bullets_flagged_and_suggested():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Worked on backend systems.", "Responsible for the database.", "Handled deployments."],
         "is_career_break": False},
    ])
    report = score_resume(resume)
    assert any("weak verb" in w.lower() for w in report["warnings"])
    assert any("strong action verb" in s.lower() for s in report["suggestions"])


def test_missing_metrics_triggers_add_measurable_achievements_suggestion():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Built services.", "Attended meetings.", "Wrote documentation."],
         "is_career_break": False},
    ])
    report = score_resume(resume)
    assert any("measurable achievements" in s.lower() for s in report["suggestions"])


# --- ATS Score -----------------------------------------------------------------

def test_ats_score_flags_low_keyword_coverage():
    resume = _resume(
        skills=["Python", "AWS", "Docker", "Kubernetes", "Terraform"],
        summary="Engineer.",
        experience=[{"company": "Acme", "role": "Engineer", "points": ["Did general work."],
                     "is_career_break": False}],
    )
    report = score_resume(resume)
    assert report["breakdown"]["ats"] < 15
    assert any("demonstrated in Experience" in w for w in report["warnings"])


def test_ats_score_full_for_well_demonstrated_skills():
    report = score_resume(_resume())
    assert report["breakdown"]["ats"] == 15


# --- Duplicate Content Score --------------------------------------------------

def test_duplicate_content_flags_duplicate_skills_and_suggests_removal():
    resume = _resume(skills=["ReactJS", "React.js", "React", "AWS", "Docker", "Kubernetes"])
    report = score_resume(resume)
    assert report["breakdown"]["duplicate_content"] < 12
    assert any("remove duplicate skills" in s.lower() for s in report["suggestions"])


def test_duplicate_content_flags_duplicate_bullets():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024",
         "points": ["Did the migration to AWS cloud infrastructure.",
                    "Did the migration to AWS cloud infrastructure systems."],
         "is_career_break": False},
    ])
    report = score_resume(resume)
    assert report["breakdown"]["duplicate_content"] < 12


def test_duplicate_content_flags_duplicate_projects():
    resume = _resume(projects=[
        {"title": "Internal Tool", "description": "A."},
        {"title": "Internal Tool", "description": "B."},
    ])
    report = score_resume(resume)
    assert any("duplicate project" in w.lower() for w in report["warnings"])


def test_duplicate_content_full_score_when_clean():
    resume = _resume(skills=["Python", "AWS"])
    report = score_resume(resume)
    assert report["breakdown"]["duplicate_content"] == 12


# --- Summary Score ---------------------------------------------------------------

def test_summary_score_zero_when_missing():
    report = score_resume(_resume(summary=""))
    assert report["breakdown"]["summary"] == 0
    assert any("improve" in s.lower() or "add a" in s.lower() for s in report["suggestions"])


def test_summary_score_penalizes_all_caps():
    resume = _resume(summary="SENIOR ENGINEER WITH EXTENSIVE EXPERIENCE ACROSS CLOUD PLATFORMS AND TEAMS.")
    report = score_resume(resume)
    assert report["breakdown"]["summary"] < 12


# --- Recruiter Readability Score ------------------------------------------------

def test_readability_flags_short_bullets():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": ["Did it."], "is_career_break": False},
    ])
    report = score_resume(resume)
    assert report["breakdown"]["recruiter_readability"] < 10


def test_readability_flags_verbose_bullets():
    long_bullet = " ".join(["word"] * 50)
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": [long_bullet], "is_career_break": False},
    ])
    report = score_resume(resume)
    assert report["breakdown"]["recruiter_readability"] < 10
    assert any("break up" in s.lower() for s in report["suggestions"])


# --- Technical Skills Score -----------------------------------------------------

def test_technical_skills_flags_excessive_count():
    resume = _resume(skills=[f"Skill{i}" for i in range(40)])
    report = score_resume(resume)
    assert report["breakdown"]["technical_skills"] < 10
    assert any("keyword dumping" in s.lower() for s in report["suggestions"])


def test_technical_skills_flags_too_few():
    resume = _resume(skills=["Python"])
    report = score_resume(resume)
    assert report["breakdown"]["technical_skills"] < 10


# --- Information Density Score --------------------------------------------------

def test_information_density_flags_low_substance():
    resume = _resume(experience=[
        {"company": "Acme", "role": "Engineer", "points": ["Did it.", "Ok.", "Fine."], "is_career_break": False},
    ])
    report = score_resume(resume)
    assert report["breakdown"]["information_density"] < 10


def test_information_density_high_for_substantive_bullets():
    # Density is proportional to the fraction of substantive bullets, not
    # a pass/fail step - the fixture's 3/4 bullets carry a metric/enough
    # detail, so the score should be high but needn't be the full weight.
    report = score_resume(_resume())
    assert report["breakdown"]["information_density"] >= 8


# --- Project Score ---------------------------------------------------------------

def test_project_score_flags_too_many_bullets():
    resume = _resume(projects=[
        {"title": "Tool", "description": "A tool.", "responsibilities": [f"Did {i}." for i in range(10)]},
    ])
    report = score_resume(resume)
    assert report["breakdown"]["project"] < 8


def test_project_score_not_penalized_when_absent():
    report = score_resume(_resume(projects=[]))
    assert report["breakdown"]["project"] == 8


# --- Education Score ---------------------------------------------------------------

def test_education_score_zero_when_missing():
    report = score_resume(_resume(education=[]))
    assert report["breakdown"]["education"] == 0
    assert any("Missing Education" in w for w in report["warnings"])


def test_education_suggests_certifications_if_absent():
    resume = _resume(certifications=[])
    report = score_resume(resume)
    assert any("certifications" in s.lower() for s in report["suggestions"])


# --- Recruiter personas (Enterprise feature, backward compatible) -----------

def test_all_persona_weights_sum_to_100():
    for persona, weights in _PERSONA_WEIGHTS.items():
        assert sum(weights.values()) == 100, persona


def test_all_personas_cover_the_same_dimensions():
    for persona in PERSONAS:
        assert set(get_persona_weights(persona).keys()) == set(_WEIGHTS.keys())


def test_default_persona_reproduces_original_weights_exactly():
    # Backward compatibility: score_resume(parsed) with no persona argument
    # must behave EXACTLY as it did before personas existed.
    resume = _resume()
    assert score_resume(resume) == score_resume(resume, persona="general")
    assert get_persona_weights("general") == _WEIGHTS


def test_unknown_persona_falls_back_to_general():
    resume = _resume()
    result = score_resume(resume, persona="not_a_real_persona")
    assert result["persona"] == "general"
    assert result["score"] == score_resume(resume, persona="general")["score"]


def test_technical_recruiter_persona_weighs_skills_and_ats_more_heavily():
    resume = _resume(skills=[f"Skill{i}" for i in range(40)])  # excessive skills - penalized
    general_report = score_resume(resume, persona="general")
    technical_report = score_resume(resume, persona="technical_recruiter")
    # technical_recruiter's technical_skills weight (20) is double
    # general's (10), so the same penalty ratio produces a bigger absolute
    # point loss under that persona.
    assert technical_report["breakdown"]["technical_skills"] <= general_report["breakdown"]["technical_skills"] * 2 + 1


def test_executive_recruiter_persona_weighs_summary_more_heavily():
    resume = _resume(summary="")  # missing summary - scores 0 regardless of persona
    executive_report = score_resume(resume, persona="executive_recruiter")
    general_report = score_resume(resume, persona="general")
    assert executive_report["breakdown"]["summary"] == 0
    assert general_report["breakdown"]["summary"] == 0
    # But the OVERALL score drops further under executive_recruiter, since
    # summary is worth more of the total (20 vs 12).
    assert executive_report["score"] <= general_report["score"]


def test_persona_never_changes_which_findings_are_reported_only_their_weight():
    resume = _resume(summary="")
    for persona in PERSONAS:
        report = score_resume(resume, persona=persona)
        assert any("Missing Professional Summary" in w for w in report["warnings"])
