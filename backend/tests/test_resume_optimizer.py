"""Unit tests for the pure, deterministic pieces of the Resume Intelligence
Engine (app/services/resume_optimizer.py), plus degrade-on-failure coverage
for the Gemini-calling pieces. `fake_gemini` (conftest.py, autouse) already
covers the happy-path end-to-end flow via tests/test_resumes_api.py - these
tests target resume_optimizer.py's own functions directly, including ones
that autouse fixture never exercises (dedup, per-piece failure handling).

Experience-bullet refinement itself (seniority classification, bullet-range
targets, per-job Gemini call) now lives in experience_refiner.py - see
tests/test_experience_refiner.py for that module's own tests.
"""
import app.services.experience_refiner as experience_refiner
import app.services.resume_optimizer as optimizer
import app.services.summary_generator as summary_generator
from app.services.gemini_client import GeminiInvalidResponseError


# --- _dedupe_preserve_order --------------------------------------------------

def test_dedupe_preserve_order_removes_exact_duplicates():
    result = optimizer._dedupe_preserve_order(["Azure", "AWS", "Azure", "Docker", "AWS"])
    assert result == ["Azure", "AWS", "Docker"]


def test_dedupe_preserve_order_is_case_and_whitespace_insensitive():
    result = optimizer._dedupe_preserve_order(["AWS Certified", "aws  certified", "  AWS CERTIFIED  "])
    assert result == ["AWS Certified"]


def test_dedupe_preserve_order_never_merges_genuinely_different_items():
    # Two different AWS certifications - must both survive; this is the
    # exact-match-only behavior that keeps education/certifications safe
    # from an incorrect fuzzy-merge losing real information.
    result = optimizer._dedupe_preserve_order([
        "AWS Certified Solutions Architect - Associate",
        "AWS Certified Solutions Architect - Professional",
    ])
    assert len(result) == 2


def test_dedupe_preserve_order_drops_blank_entries():
    assert optimizer._dedupe_preserve_order(["", "   ", "Real Skill"]) == ["Real Skill"]


def test_dedupe_preserve_order_empty_input():
    assert optimizer._dedupe_preserve_order([]) == []


# --- _coerce_skills / _coerce_str_list --------------------------------------

def test_coerce_skills_from_flat_list():
    assert optimizer._coerce_skills(["Python", "SQL"]) == {"Skills": ["Python", "SQL"]}


def test_coerce_skills_from_categorized_dict():
    result = optimizer._coerce_skills({"Programming Languages": ["Python", "SQL"], "Empty Category": []})
    assert result == {"Programming Languages": ["Python", "SQL"]}


def test_coerce_skills_handles_none_and_empty():
    assert optimizer._coerce_skills(None) == {}
    assert optimizer._coerce_skills([]) == {}
    assert optimizer._coerce_skills({}) == {}


def test_coerce_str_list_handles_bad_shapes():
    assert optimizer._coerce_str_list(None) == []
    assert optimizer._coerce_str_list("single string") == ["single string"]
    assert optimizer._coerce_str_list([1, "two", "", "three", None]) == ["1", "two", "three"]


# --- _validate_and_dedupe (Step 10) ------------------------------------------

def _minimal_result(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "", "phone": "", "linkedin": "",
        "summary": "Summary.",
        "skills": ["Python", "Python", "SQL"],
        "tools": ["Git", "git", "Jenkins"],
        "education": ["B.Tech - XYZ University", "B.Tech - XYZ University"],
        "certifications": ["AWS Certified", "AWS Certified"],
        "achievements": ["Reduced costs by 20%", "Reduced costs by 20%"],
        "languages": ["English", "English"],
        "publications": [],
        "volunteer_experience": [],
        "leadership": [],
        "experience": [{"company": "Acme", "role": "Engineer", "points": ["Did X", "Did X", "Did Y"]}],
        "projects": [{"title": "Internal Tool", "description": "A"}, {"title": "internal tool", "description": "B"}],
    }
    base.update(overrides)
    return base


def test_validate_and_dedupe_removes_duplicates_everywhere():
    result = optimizer._validate_and_dedupe(_minimal_result())
    assert result["skills"] == ["Python", "SQL"]
    assert result["tools"] == ["Git", "Jenkins"]
    assert result["education"] == ["B.Tech - XYZ University"]
    assert result["certifications"] == ["AWS Certified"]
    assert result["achievements"] == ["Reduced costs by 20%"]
    assert result["languages"] == ["English"]
    assert result["experience"][0]["points"] == ["Did X", "Did Y"]
    assert len(result["projects"]) == 1  # "Internal Tool" / "internal tool" treated as the same project


def test_validate_and_dedupe_does_not_fabricate_empty_sections(caplog):
    result = optimizer._validate_and_dedupe(_minimal_result(tools=[], achievements=[]))
    assert result["tools"] == []
    assert result["achievements"] == []


# --- degrade-on-failure behavior (never raises, always falls back) ---------

def test_optimize_skills_and_tools_degrades_on_gemini_failure(monkeypatch):
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(optimizer, "call_gemini", boom)
    skills, candidates, tools = optimizer._optimize_skills_and_tools(["Python", "SQL"], ["Git"])
    # Falls back to skill_categorizer.py's deterministic categorization for
    # summary-prompt context (still genuinely categorized, not one flat
    # uncategorized bucket) - but the candidate pool fed to the Skill
    # Intelligence Engine is the raw, unfiltered list, not pre-filtered
    # through skill_categorizer.py's own (narrower) keyword dictionary.
    assert skills == {"Programming Languages": ["Python", "SQL"]}
    assert candidates == ["Python", "SQL"]
    assert tools == ["Git"]


def test_derive_achievements_returns_empty_when_nothing_to_scan():
    assert optimizer._derive_achievements("", [], []) == []


def test_derive_achievements_degrades_to_empty_on_gemini_failure(monkeypatch):
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(optimizer, "call_gemini", boom)
    result = optimizer._derive_achievements("Summary text.", [{"points": ["Did something."]}], [])
    assert result == []


def test_derive_achievements_includes_stage1_explicit_achievements(monkeypatch):
    # Root-cause fix: Stage 1's own explicit extraction of an Awards/
    # Achievements section (extraction_pipeline.py's 6th call) must survive
    # even when there's nothing else to derive from.
    result = optimizer._derive_achievements("", [], [], stage1_achievements=["Employee of the Month"])
    assert result == ["Employee of the Month"]


def test_derive_achievements_combines_stage1_and_gemini_derived(monkeypatch):
    monkeypatch.setattr(optimizer, "call_gemini", lambda prompt: {"achievements": ["Reduced costs by 20%"]})
    result = optimizer._derive_achievements(
        "Summary text.", [{"points": ["Did something."]}], [], stage1_achievements=["Employee of the Month"],
    )
    assert set(result) == {"Employee of the Month", "Reduced costs by 20%"}


def test_derive_achievements_keeps_stage1_achievements_even_if_gemini_fails(monkeypatch):
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(optimizer, "call_gemini", boom)
    result = optimizer._derive_achievements(
        "Summary text.", [{"points": ["Did something."]}], [], stage1_achievements=["Employee of the Month"],
    )
    assert result == ["Employee of the Month"]


def test_optimize_projects_degrades_on_gemini_failure(monkeypatch):
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(optimizer, "call_gemini", boom)
    projects = [{"title": "Tool", "description": "Built a tool.", "technologies": "", "responsibilities": []}]
    result = optimizer._optimize_projects(projects, tone="Professional")
    assert result == projects


def test_optimize_resume_never_raises_even_if_every_call_fails(monkeypatch):
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated total Gemini failure")

    # call_gemini is bound into THREE module namespaces that all need
    # patching (experience refinement and summary generation are both
    # delegated to their own modules, each of which imported its own
    # reference at import time) - patching only resume_optimizer's would
    # leave those two steps hitting the real (unpatched) Gemini client.
    monkeypatch.setattr(optimizer, "call_gemini", boom)
    monkeypatch.setattr(experience_refiner, "call_gemini", boom)
    monkeypatch.setattr(summary_generator, "call_gemini", boom)
    structured = {
        "name": "Jane Doe", "email": "jane@example.com", "phone": "", "linkedin": "",
        "summary": "Experienced engineer.",
        "skills": ["Python", "SQL"], "tools": ["Git"],
        "education": ["B.Tech"], "certifications": ["AWS Certified"],
        "experience": [{"company": "Acme", "role": "Engineer", "points": ["A", "B", "C", "D", "E"]}],
        "projects": [{"title": "Tool", "description": "Built a tool."}],
    }
    result = optimizer.optimize_resume(structured, tone="Professional")
    assert result["name"] == "Jane Doe"
    assert result["experience"][0]["points"] == ["A", "B", "C", "D", "E"]
    assert result["achievements"] == []
    # Priority 3 fix - on a total Gemini failure, summary_generator now
    # falls back to a deterministic summary built from the candidate's own
    # extracted data (role/company/skills/certifications) rather than
    # Stage 1's original one-line summary verbatim - still never invents
    # anything, so every fact asserted below must trace back to `structured`.
    assert "Engineer" in result["summary"]
    assert "Acme" in result["summary"]
    assert "Python" in result["summary"] and "SQL" in result["summary"]
    assert "AWS Certified" in result["summary"]
    # "skills" must be a flat, un-subsectioned list (never a category dict) -
    # see finalize_technical_skills/file_generator.py's add_skills_section.
    assert result["skills"] == ["Python", "SQL"]
    assert result["tools"] == ["Git"]


# --- _quality_audit (Phase 1 quality check) ----------------------------------

def _audit_result(**overrides) -> dict:
    base = {
        "name": "Jane Doe", "email": "", "phone": "", "linkedin": "",
        "summary": (
            "Senior software engineer with 8 years of experience architecting cloud-native systems "
            "on AWS and Azure, leading small engineering teams."
        ),
        "skills": ["Python"], "tools": [], "education": [], "certifications": [], "achievements": [],
        "experience": [{"company": "Acme", "role": "Engineer", "points": ["Did X."]}],
        "projects": [],
    }
    base.update(overrides)
    return base


def test_quality_audit_trims_experience_bullets_over_hard_cap():
    points = [f"Did task {i}." for i in range(20)]
    result = optimizer._quality_audit(_audit_result(experience=[{"company": "Acme", "points": points}]))
    assert len(result["experience"][0]["points"]) == optimizer._MAX_EXPERIENCE_BULLETS_HARD_CAP
    assert result["experience"][0]["points"] == points[:optimizer._MAX_EXPERIENCE_BULLETS_HARD_CAP]


def test_quality_audit_trims_project_bullets_over_cap():
    responsibilities = [f"Did task {i}." for i in range(10)]
    result = optimizer._quality_audit(
        _audit_result(projects=[{"title": "Tool", "responsibilities": responsibilities}])
    )
    assert len(result["projects"][0]["responsibilities"]) == optimizer._MAX_PROJECT_BULLETS
    assert result["projects"][0]["responsibilities"] == responsibilities[: optimizer._MAX_PROJECT_BULLETS]


def test_quality_audit_never_trims_when_within_limits():
    exp = {"company": "Acme", "points": ["Did X.", "Did Y."]}
    result = optimizer._quality_audit(_audit_result(experience=[exp]))
    assert result["experience"][0]["points"] == ["Did X.", "Did Y."]


def test_quality_audit_logs_weak_verbs_without_changing_content(caplog):
    exp = {"company": "Acme", "points": ["Worked on backend systems.", "Led the migration effort."]}
    with caplog.at_level("WARNING"):
        result = optimizer._quality_audit(_audit_result(experience=[exp]))
    assert result["experience"][0]["points"] == ["Worked on backend systems.", "Led the migration effort."]
    assert any("weak verb" in message for message in caplog.messages)


def test_quality_audit_logs_repeated_bullet_openings(caplog):
    exp = {"company": "Acme", "points": ["Led the migration of legacy systems.", "Led the migration of the data warehouse."]}
    with caplog.at_level("WARNING"):
        optimizer._quality_audit(_audit_result(experience=[exp]))
    assert any("opening wording" in message for message in caplog.messages)


def test_quality_audit_logs_ai_sounding_summary_phrases(caplog):
    with caplog.at_level("WARNING"):
        optimizer._quality_audit(_audit_result(summary="A dynamic self-starter passionate about technology."))
    assert any("AI-sounding" in message for message in caplog.messages)


def test_quality_audit_logs_too_short_summary(caplog):
    with caplog.at_level("WARNING"):
        optimizer._quality_audit(_audit_result(summary="Short summary."))
    assert any("too short" in message for message in caplog.messages)


def test_quality_audit_does_not_flag_a_good_summary(caplog):
    with caplog.at_level("WARNING"):
        optimizer._quality_audit(_audit_result())
    assert not any("summary" in message.lower() for message in caplog.messages)


def test_optimize_resume_skills_are_flat_and_uncapped(monkeypatch):
    """Even on the Gemini-success path, the final result["skills"] must be
    a flat list (no category sub-headings) - and, per the "never drop a
    genuine skill" fix, NOT truncated just because the candidate has a
    large number of real skills."""
    import app.services.skill_intelligence as skill_intelligence

    # 40 distinct, genuinely-technical terms spanning several categories -
    # real skill names (not synthetic placeholders) so the Skill
    # Intelligence Engine's classification stage actually keeps them, and
    # this genuinely exercises "more than the old cap" with real content.
    many_real_skills = [
        "Python", "Java", "JavaScript", "TypeScript", "Go", "Ruby", "PHP", "Swift", "Kotlin", "Scala",
        "React", "Angular", "Vue", "Django", "Flask", "Spring Boot", "Express", "Next.js", "Laravel", "Rails",
        "AWS", "Azure", "GCP", "Heroku", "DigitalOcean",
        "MySQL", "PostgreSQL", "MongoDB", "Redis", "Cassandra",
        "Docker", "Kubernetes", "Jenkins", "Terraform", "Ansible",
        "TensorFlow", "PyTorch", "Power BI", "Tableau", "Kafka",
    ]
    assert len(many_real_skills) > skill_intelligence.MAX_TECHNICAL_SKILLS

    def fake_gemini(prompt):
        return {"skills": {"All Skills": many_real_skills}, "tools": []}

    monkeypatch.setattr(optimizer, "call_gemini", fake_gemini)
    # summary_generator.py imports its own reference to call_gemini - not
    # patching it here would make this test silently hit the real Gemini
    # API over the network (the exact bug this session already found and
    # fixed once for the fake_gemini conftest fixture).
    monkeypatch.setattr(summary_generator, "call_gemini", lambda prompt: {"summary": "A summary."})
    structured = {"name": "Jane Doe", "skills": ["placeholder"], "tools": [], "experience": []}
    result = optimizer.optimize_resume(structured, tone="Professional")
    assert isinstance(result["skills"], list)
    assert len(result["skills"]) == len(many_real_skills)
    assert set(result["skills"]) == set(many_real_skills)


# --- Enterprise quality refinement: relevance context + phrase variation ----

def test_optimize_resume_passes_relevance_context_to_skill_ranking(monkeypatch):
    """Skill Prioritization: within the same category tier, a skill
    heavily evidenced by the candidate's own title/summary/bullets should
    rank ahead of one only listed in Skills - proven end-to-end through
    optimize_resume(), not just skill_intelligence.py in isolation."""
    def fake_gemini(prompt):
        return {"skills": {"Cloud": ["Azure", "AWS"]}, "tools": []}

    monkeypatch.setattr(optimizer, "call_gemini", fake_gemini)
    monkeypatch.setattr(experience_refiner, "call_gemini", lambda prompt: {"points": ["Did X."]})
    monkeypatch.setattr(summary_generator, "call_gemini", lambda prompt: {"summary": "Senior AWS engineer."})
    structured = {
        "name": "Jane Doe", "skills": ["Azure", "AWS"], "tools": [],
        "summary": "Senior AWS engineer.",
        "experience": [{"company": "Acme", "role": "AWS Cloud Engineer",
                         "points": ["Built services on AWS.", "Automated deployments.", "Optimized costs."]}],
    }
    result = optimizer.optimize_resume(structured, tone="Professional")
    assert result["skills"].index("AWS") < result["skills"].index("Azure")


def test_optimize_resume_varies_repeated_generic_phrases_across_jobs(monkeypatch):
    """Experience Deduplication: a generic responsibility phrase
    copy-pasted verbatim into two different jobs should read differently
    in each after optimize_resume() runs."""
    def fake_gemini(prompt):
        return {"skills": ["Python"], "tools": []}

    monkeypatch.setattr(optimizer, "call_gemini", fake_gemini)

    def fake_experience_gemini(prompt):
        if "Acme" in prompt:
            return {"points": ["Performed requirements analysis for the platform."]}
        return {"points": ["Performed requirements analysis for the new system."]}

    monkeypatch.setattr(experience_refiner, "call_gemini", fake_experience_gemini)
    monkeypatch.setattr(summary_generator, "call_gemini", lambda prompt: {"summary": "Engineer."})
    structured = {
        "name": "Jane Doe", "skills": ["Python"], "tools": [],
        "experience": [
            {"company": "Acme", "role": "Engineer",
             "points": ["Performed requirements analysis for the platform.", "Did other work.", "More work.", "Even more."]},
            {"company": "Globex", "role": "Engineer",
             "points": ["Performed requirements analysis for the new system.", "Did other work.", "More work.", "Even more."]},
        ],
    }
    result = optimizer.optimize_resume(structured, tone="Professional")
    first_job_text = " ".join(result["experience"][0]["points"]).lower()
    second_job_text = " ".join(result["experience"][1]["points"]).lower()
    assert "requirements analysis" in first_job_text
    assert "requirements analysis" not in second_job_text


def test_optimize_resume_never_drops_projects_by_count(monkeypatch):
    """Projects Handled is never count-capped in the main pipeline - CONFIRMED
    real-world regression (a real 8.7-year candidate with 8 genuinely
    distinct, different-client projects had 4 silently dropped by a
    years-scaled rank_and_select_projects() cap), which directly violated
    the "100% Project Preservation" requirement. Every project the
    candidate has must survive, exactly like every Experience job already
    does (see prompts.py rule 7) - length is controlled by compressing each
    project's own bullet count, never by dropping whole projects. Proven
    end-to-end through optimize_resume(), well past the old MAX_PROJECTS
    ceiling."""
    import app.services.experience_intelligence as experience_intelligence

    def fake_gemini(prompt):
        if "projects" in prompt.lower():
            raise GeminiInvalidResponseError("force fallback to Stage 1's own project list")
        return {"skills": ["Python"], "tools": []}

    monkeypatch.setattr(optimizer, "call_gemini", fake_gemini)
    monkeypatch.setattr(summary_generator, "call_gemini", lambda prompt: {"summary": "Engineer."})

    strong_project = {
        "title": "Enterprise Cloud Migration",
        "description": "Led the enterprise-scale architecture for migrating workloads to Azure for a global client.",
        "technologies": "Azure", "responsibilities": ["Reduced infrastructure costs by 35%."],
    }
    other_projects = [
        {"title": f"Client Project {i}", "description": "Built and delivered a distinct client engagement.", "responsibilities": ["Did real work."]}
        for i in range(experience_intelligence.MAX_PROJECTS + 2)  # deliberately past the old ceiling
    ]
    structured = {
        "name": "Jane Doe", "skills": ["Python"], "tools": [],
        "experience": [{"company": "Acme", "role": "Engineer", "duration": "2005-2024", "points": []}],
        "projects": [strong_project] + other_projects,
    }
    result = optimizer.optimize_resume(structured, tone="Professional")
    assert len(result["projects"]) == len(structured["projects"])
    assert any(p["title"] == "Enterprise Cloud Migration" for p in result["projects"])
