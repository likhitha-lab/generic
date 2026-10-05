"""Unit tests for the deterministic, keyword-based skill categorizer."""
from app.services.skill_categorizer import (
    CATEGORY_ORDER,
    categorize_skill,
    categorize_skills,
    finalize_technical_skills,
)


def test_categorize_skill_known_terms():
    assert categorize_skill("AWS") == "Cloud Platforms"
    assert categorize_skill("Python") == "Programming Languages"
    assert categorize_skill("React.js") == "Frameworks"
    assert categorize_skill("PostgreSQL") == "Databases"
    assert categorize_skill("Pandas") == "Libraries"
    assert categorize_skill("Kafka") == "Messaging Technologies"
    assert categorize_skill("Terraform") == "DevOps Technologies"
    assert categorize_skill("Kubernetes") == "DevOps Technologies"
    assert categorize_skill("Microservices") == "Architecture Patterns"
    assert categorize_skill("System Design") == "Architecture Patterns"
    assert categorize_skill("REST APIs") == "Core Engineering Skills"
    assert categorize_skill("TensorFlow") == "AI/ML Technologies"
    assert categorize_skill("Apache Airflow") == "Analytics Technologies"
    assert categorize_skill("Power BI") == "Visualization Technologies"
    assert categorize_skill("OAuth") == "Security Technologies"
    assert categorize_skill("VPN") == "Infrastructure Technologies"
    assert categorize_skill("Linux") == "Infrastructure Technologies"


def test_categorize_skill_unmatched_returns_none():
    # Business/soft-skill/methodology terms - none of these are genuine
    # technical skills, so none should resolve to a category at all.
    for term in ["Leadership", "Stakeholder Management", "Project Planning", "Governance",
                 "Consulting", "Business Analysis", "Quality Management", "Technical Support",
                 "Application Development", "Transformation", "Underwater Basket Weaving"]:
        assert categorize_skill(term) is None, term


def test_categorize_skill_no_word_boundary_false_positive():
    # "java" must not match inside "javascript" - a naive substring check
    # would put JavaScript in the same bucket as Java.
    assert categorize_skill("JavaScript") == "Programming Languages"
    assert categorize_skill("Java") == "Programming Languages"


def test_categorize_skill_case_and_whitespace_insensitive():
    assert categorize_skill("  aws  ") == "Cloud Platforms"
    assert categorize_skill("PYTHON") == "Programming Languages"


def test_categorize_skills_groups_and_sorts_alphabetically():
    result = categorize_skills(["Python", "AWS", "Java", "Azure", "Docker"])
    assert result["Programming Languages"] == ["Java", "Python"]  # alphabetical
    assert result["Cloud Platforms"] == ["AWS", "Azure"]  # alphabetical
    assert result["DevOps Technologies"] == ["Docker"]


def test_categorize_skills_buckets_unmatched_terms_as_other_skills():
    # Root-cause fix: a term that matches no named technical category (this
    # module has no Business/Soft-Skills categories of its own - those live
    # in skill_intelligence.py) must still survive under "Other Skills" -
    # never silently excluded.
    result = categorize_skills(["Python", "Leadership", "Stakeholder Management"])
    assert result["Programming Languages"] == ["Python"]
    assert sorted(result["Other Skills"]) == ["Leadership", "Stakeholder Management"]


def test_categorize_skills_still_excludes_certification_and_degree_strings():
    # Unlike a genuine-but-unmatched skill, a certification/degree/sentence-
    # like string that leaked into the raw skills list is NOT a skill at
    # all - it stays excluded (belongs in certifications/education).
    result = categorize_skills(["Python", "AWS Certified Solutions Architect - Professional",
                                 "Bachelor of Science in Computer Science"])
    all_items = [skill for items in result.values() for skill in items]
    assert all_items == ["Python"]


def test_categorize_skills_omits_empty_categories():
    result = categorize_skills(["Python"])
    assert "Databases" not in result
    assert list(result.keys()) == ["Programming Languages"]


def test_categorize_skills_every_matched_skill_in_exactly_one_category():
    skills = ["AWS", "Python", "React.js", "PostgreSQL", "Docker", "Kubernetes"]
    result = categorize_skills(skills)
    all_categorized = [skill for items in result.values() for skill in items]
    assert sorted(all_categorized, key=str.lower) == sorted(skills, key=str.lower)
    # no skill appears twice across categories
    assert len(all_categorized) == len(set(s.lower() for s in all_categorized))


def test_categorize_skills_handles_empty_and_none_input():
    assert categorize_skills(None) == {}
    assert categorize_skills([]) == {}


def test_categorize_skills_output_category_order_follows_category_order_constant():
    result = categorize_skills(["Leadership", "Python", "AWS"])
    result_keys = list(result.keys())
    expected_order = [c for c in CATEGORY_ORDER if c in result_keys]
    assert result_keys == expected_order


def test_category_order_has_no_non_technical_buckets():
    for forbidden in ("Soft Skills", "Project Management", "Other"):
        assert forbidden not in CATEGORY_ORDER


# --- finalize_technical_skills ----------------------------------------------

def test_finalize_technical_skills_flattens_without_category_headers():
    categorized = {"Programming Languages": ["Python", "Java"], "Cloud Platforms": ["AWS"]}
    result = finalize_technical_skills(categorized)
    assert result == ["Python", "Java", "AWS"]
    assert "Programming Languages" not in result  # no category name leaks into the flat list


def test_finalize_technical_skills_dedupes_across_categories():
    categorized = {"A": ["Python", "python"], "B": ["Python"]}
    result = finalize_technical_skills(categorized)
    assert result == ["Python"]


def test_finalize_technical_skills_caps_at_max_items():
    categorized = {"A": [f"Skill{i}" for i in range(50)]}
    result = finalize_technical_skills(categorized, max_items=10)
    assert len(result) == 10
    assert result == [f"Skill{i}" for i in range(10)]


def test_finalize_technical_skills_has_no_default_cap():
    categorized = {"A": [f"Skill{i}" for i in range(50)]}
    result = finalize_technical_skills(categorized)
    assert len(result) == 50


def test_finalize_technical_skills_never_pads_below_max():
    categorized = {"Programming Languages": ["Python"]}
    result = finalize_technical_skills(categorized)
    assert result == ["Python"]


def test_finalize_technical_skills_handles_empty_and_none():
    assert finalize_technical_skills(None) == []
    assert finalize_technical_skills({}) == []


# --- certification/degree/project-name leakage guard -------------------------

def test_categorize_skill_excludes_certification_strings_even_if_they_contain_a_keyword():
    # Contains "aws" (a real Cloud Platforms keyword) but is a certification
    # title, not a skill - must be excluded, not miscategorized as "AWS".
    assert categorize_skill("AWS Certified Solutions Architect - Professional") is None
    assert categorize_skill("Microsoft Certified: Azure Administrator") is None


def test_categorize_skill_excludes_degree_strings():
    assert categorize_skill("Bachelor of Science in Computer Science") is None
    assert categorize_skill("B.Tech from XYZ University") is None


def test_categorize_skill_excludes_long_sentence_like_strings():
    # A real skill is a short name, never a full sentence/description.
    assert categorize_skill("Designed and implemented Python-based data pipelines for analytics") is None


def test_categorize_skill_still_matches_short_genuine_skills():
    assert categorize_skill("Python") == "Programming Languages"
    assert categorize_skill("Apache Kafka") == "Messaging Technologies"
