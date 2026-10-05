"""Unit tests for app/services/skill_intelligence.py - the Phase 2 Skill
Intelligence Engine. Covers every pipeline stage individually plus the full
build_technical_skills() pipeline end-to-end."""
from app.services.skill_intelligence import (
    MAX_TECHNICAL_SKILLS,
    _clean,
    _consolidate_related_skills,
    _detect_duplicates,
    _fold_generic_vendor_suffixes,
    _fold_version_variants,
    _group_etl_transformation_types,
    _group_shared_anchor_variants,
    _group_sql_family_features,
    _is_technical_skill_eligible,
    _looks_like_non_skill,
    _normalize,
    _quality_validate,
    _rank,
    _resolve_aliases,
    build_technical_skills,
    build_technical_skills_grouped,
    canonical_technology_name,
    classify_skill,
    known_technology_variants,
)


# --- Stage 1: Cleaning -------------------------------------------------------

def test_clean_drops_empty_and_blank_entries():
    assert _clean(["Python", "", "   ", None]) == ["Python"]


def test_clean_strips_and_collapses_whitespace():
    assert _clean(["  Python   Django  "]) == ["Python Django"]


def test_looks_like_non_skill_flags_certifications():
    assert _looks_like_non_skill("AWS Certified Solutions Architect - Professional")
    assert _looks_like_non_skill("Microsoft Certified: Azure Administrator")


def test_looks_like_non_skill_flags_degrees():
    assert _looks_like_non_skill("Bachelor of Science in Computer Science")
    assert _looks_like_non_skill("B.Tech from XYZ University")


def test_looks_like_non_skill_flags_long_sentences():
    assert _looks_like_non_skill("Designed and implemented scalable Python data pipelines for analytics")


def test_looks_like_non_skill_false_for_genuine_short_skill():
    assert not _looks_like_non_skill("Python")
    assert not _looks_like_non_skill("Apache Kafka")


def test_clean_drops_certifications_and_degrees_via_looks_like_non_skill():
    raw = ["Python", "AWS Certified Solutions Architect - Professional", "Bachelor of Science in Computer Science"]
    assert _clean(raw) == ["Python"]


# --- Technical Skill Eligibility (responsibility/activity phrase exclusion) -
# CONFIRMED real examples of responsibility phrases incorrectly rendered as
# Technical Skills - these are short noun phrases (not sentences, not
# certification titles), so _looks_like_non_skill's existing checks never
# caught them; a dedicated, explicit, evidence-driven exclusion set was
# needed.

def test_is_technical_skill_eligible_rejects_all_confirmed_responsibility_examples():
    responsibility_examples = (
        "Requirements Gathering", "Client Interaction", "Internal Reviews", "External Reviews",
        "Knowledge Transfer", "Story Prioritization", "Walk-throughs",
        "Business Needs Understanding", "Bug Detection", "Bug Addressing",
        "Test Data Generation", "Documentation Discussions",
    )
    for phrase in responsibility_examples:
        assert not _is_technical_skill_eligible(phrase), f"{phrase!r} should be excluded"


def test_is_technical_skill_eligible_rejects_curation_v2_examples():
    v2_examples = (
        "Requirements Documentation", "Workflow Monitoring", "Task Scheduling",
        "Report Building", "Documentation", "Business Needs", "Discussion Notes",
        "Test Scenario Creation",
    )
    for phrase in v2_examples:
        assert not _is_technical_skill_eligible(phrase), f"{phrase!r} should be excluded"


def test_is_technical_skill_eligible_true_for_genuine_technologies():
    for skill in ("Python", "SQL", "AWS", "Snowflake", "PL/SQL", "React", "Docker"):
        assert _is_technical_skill_eligible(skill), f"{skill!r} should NOT be excluded"


def test_is_technical_skill_eligible_never_substring_matches():
    # "review" is part of the excluded phrase "Internal Reviews", but a
    # DIFFERENT phrase that merely contains a similar word must survive -
    # exact-phrase match only, never a substring/keyword-contains check.
    assert _is_technical_skill_eligible("Code Review Tools")


def test_clean_excludes_responsibility_phrases_end_to_end():
    raw = [
        "Python", "SQL", "Requirements Gathering", "Client Interaction", "Internal Reviews",
        "External Reviews", "Knowledge Transfer", "Story Prioritization", "Walk-throughs",
        "Business Needs Understanding", "Bug Detection", "Bug Addressing", "Test Data Generation",
        "Documentation Discussions", "AWS", "Snowflake",
    ]
    assert _clean(raw) == ["Python", "SQL", "AWS", "Snowflake"]


def test_build_technical_skills_never_renders_responsibility_phrases():
    raw = [
        "Python", "Requirements Gathering", "Client Interaction", "Internal Reviews",
        "External Reviews", "Knowledge Transfer", "Story Prioritization", "Walk-throughs",
        "Business Needs Understanding", "Bug Detection", "Bug Addressing", "Test Data Generation",
        "Documentation Discussions", "AWS",
    ]
    result = build_technical_skills(raw)
    assert result == ["Python", "AWS"]


# --- Stage 2: Normalization Engine -------------------------------------------

def test_normalize_react_variants():
    assert _normalize(["ReactJS", "React.js", "React"]) == ["React", "React", "React"]


def test_normalize_node_variants():
    assert _normalize(["Node", "NodeJS", "Node.js"]) == ["Node.js", "Node.js", "Node.js"]


def test_normalize_mysql_variants():
    assert _normalize(["My SQL", "Azure My SQL", "MySQL"]) == ["MySQL", "MySQL", "MySQL"]


def test_normalize_sql_developer_to_sql():
    # Technical Skills Curation V2 - confirmed, explicit instruction;
    # reverses the prior round's opposite decision (see
    # _NORMALIZATION_MAP's own comment).
    assert _normalize(["SQL Developer"]) == ["SQL"]


def test_normalize_sql_server_variants():
    assert _normalize(["MS SQL", "Microsoft SQL Server"]) == ["SQL Server", "SQL Server"]


def test_normalize_csharp():
    assert _normalize(["C Sharp"]) == ["C#"]
    assert _normalize(["C#"]) == ["C#"]
    # "C" (the language) must NEVER collapse into "C#" - a real, different language.
    assert _normalize(["C"]) == ["C"]


def test_normalize_javascript():
    assert _normalize(["Java Script"]) == ["JavaScript"]


def test_normalize_postgres():
    assert _normalize(["Postgres"]) == ["PostgreSQL"]


def test_normalize_tensorflow_and_pytorch():
    assert _normalize(["Tensor Flow"]) == ["TensorFlow"]
    assert _normalize(["Py Torch"]) == ["PyTorch"]


def test_normalize_leaves_unrecognized_terms_unchanged():
    assert _normalize(["SomeNicheTool"]) == ["SomeNicheTool"]


def test_normalize_azure_sql_is_never_mapped_to_mysql():
    # Azure SQL is a SQL-Server-based service, not MySQL - mapping it to
    # "MySQL" would misstate a candidate's actual technology experience.
    assert _normalize(["Azure SQL"]) == ["Azure SQL"]


# --- Stage 3: Alias Resolution -----------------------------------------------

def test_resolve_aliases_azure_container_service_and_aks():
    assert _resolve_aliases(["Azure Container Service"]) == ["AKS"]
    assert _resolve_aliases(["Azure Kubernetes Service"]) == ["AKS"]
    assert _resolve_aliases(["AKS"]) == ["AKS"]


def test_resolve_aliases_aspnet_core():
    assert _resolve_aliases(["ASP.NET Core"]) == [".NET Core"]


def test_resolve_aliases_power_platform():
    assert _resolve_aliases(["Power Platform"]) == ["Microsoft Power Platform"]


def test_resolve_aliases_github():
    assert _resolve_aliases(["Git Hub"]) == ["GitHub"]


def test_resolve_aliases_vs_code():
    assert _resolve_aliases(["Visual Studio Code"]) == ["VS Code"]


# --- Stage 4: Duplicate Detection --------------------------------------------

def test_detect_duplicates_removes_exact_repeats():
    assert _detect_duplicates(["React", "React", "AWS"]) == ["React", "AWS"]


def test_detect_duplicates_after_normalization_and_alias_resolution():
    # These arrive already-canonicalized by Stages 2-3 (React/ReactJS -> React,
    # Azure Container Service/AKS -> AKS) - Stage 4 collapses the resulting
    # exact repeats.
    assert _detect_duplicates(["React", "React", "AKS", "AKS"]) == ["React", "AKS"]


def test_detect_duplicates_fuzzy_merges_near_typos_conservatively():
    result = _detect_duplicates(["Kubernetes", "Kubernetess"])
    assert len(result) == 1


def test_detect_duplicates_never_merges_genuinely_different_terms():
    result = _detect_duplicates(["Java", "JavaScript"])
    assert len(result) == 2


# --- Related-Skill Consolidation (version folding + parent/child grouping) --
# CONFIRMED by direct testing: _detect_duplicates' fuzzy merge (threshold
# 0.93) cannot catch either pattern here - "Oracle SQL (11g)" vs "Oracle
# SQL (12c)" scores 0.875, "PL/SQL" vs "Stored Procedures"/"Cursors"
# scores 0.15-0.17 - hence this separate, purpose-built pass.

def test_fold_version_variants_collapses_same_base_technology():
    result = _fold_version_variants(["Oracle SQL (11g)", "Oracle SQL (12c)"])
    assert result == ["Oracle SQL"]


def test_fold_version_variants_leaves_non_version_parenthetical_untouched():
    # No digit inside the parens - meaningful qualifying content, not a
    # version tag, never stripped.
    result = _fold_version_variants(["Machine Learning (Supervised)", "Machine Learning (Unsupervised)"])
    assert result == ["Machine Learning (Supervised)", "Machine Learning (Unsupervised)"]


def test_fold_version_variants_prefers_an_already_bare_occurrence():
    result = _fold_version_variants(["Python", "Python (3.11)"])
    assert result == ["Python"]


def test_fold_version_variants_never_touches_unrelated_skills():
    result = _fold_version_variants(["Oracle SQL (11g)", "SQL Developer", "SQL"])
    assert result == ["Oracle SQL", "SQL Developer", "SQL"]


def test_group_shared_anchor_variants_folds_etl_wording_variants():
    result = _group_shared_anchor_variants(
        ["ETL mappings", "ETL mapping creation", "Workflows (ETL)", "Transformations (ETL)"]
    )
    assert result == ["ETL (Mappings, Mapping creation, Workflows, Transformations)"]


def test_group_shared_anchor_variants_leaves_lone_mention_untouched():
    # Only one skill mentions "ETL" - not a repeated-wording pattern, left alone.
    result = _group_shared_anchor_variants(["ETL mappings", "Python", "AWS"])
    assert result == ["ETL mappings", "Python", "AWS"]


def test_group_shared_anchor_variants_does_not_touch_sql_generically():
    # Confirms the deliberate scope limit - "SQL" is NOT in
    # _SHARED_ANCHOR_WORDS (unlike "ETL", it's used in genuinely different
    # senses across ordinary resume text - Oracle SQL/SQL Developer/SQL
    # are three different things, not wording variants of one skill).
    result = _group_shared_anchor_variants(["Oracle SQL", "SQL Developer", "SQL"])
    assert result == ["Oracle SQL", "SQL Developer", "SQL"]


def test_group_sql_family_features_groups_features_under_recognized_anchor():
    result = _group_sql_family_features(
        ["PL/SQL", "Stored Procedures", "Functions", "Views", "Triggers", "Cursors"]
    )
    assert result == ["PL/SQL (Stored Procedures, Functions, Views, Triggers, Cursors)"]


def test_group_sql_family_features_never_groups_without_a_recognized_anchor():
    # No PL/SQL/T-SQL/MySQL/etc. present - these stay as their own entries,
    # never grouped under a guessed/invented parent.
    result = _group_sql_family_features(["Stored Procedures", "Views"])
    assert result == ["Stored Procedures", "Views"]


def test_group_sql_family_features_leaves_unrelated_skills_alone():
    result = _group_sql_family_features(["PL/SQL", "Views", "Python", "AWS"])
    assert result == ["PL/SQL (Views)", "Python", "AWS"]


def test_group_sql_family_features_prefers_specific_dialect_over_bare_sql():
    # CONFIRMED real bug found while validating end-to-end: with both bare
    # "SQL" and "PL/SQL" present, the anchor must be "PL/SQL" (the more
    # specific match - Stored Procedures/Triggers are PL/SQL-specific
    # constructs), never the generic "SQL" simply because it happened to
    # appear first in the list.
    result = _group_sql_family_features(["SQL", "PL/SQL", "Stored Procedures", "Triggers"])
    assert result == ["SQL", "PL/SQL (Stored Procedures, Triggers)"]


def test_consolidate_related_skills_handles_all_three_confirmed_examples():
    assert _consolidate_related_skills(
        ["PL/SQL", "Stored Procedures", "Functions", "Views", "Triggers", "Cursors"]
    ) == ["PL/SQL (Stored Procedures, Functions, Views, Triggers, Cursors)"]

    assert _consolidate_related_skills(
        ["Oracle SQL (11g)", "Oracle SQL (12c)", "SQL Developer", "SQL"]
    ) == ["Oracle SQL", "SQL Developer", "SQL"]

    assert _consolidate_related_skills(
        ["ETL mappings", "ETL mapping creation", "Workflows (ETL)", "Transformations (ETL)"]
    ) == ["ETL (Mappings, Mapping creation, Workflows, Transformations)"]


def test_consolidate_related_skills_never_drops_a_skill_with_no_group():
    # A completely unrelated skill list - nothing to consolidate, nothing lost.
    result = _consolidate_related_skills(["Python", "AWS", "Docker"])
    assert result == ["Python", "AWS", "Docker"]


# --- Generic vendor-suffix folding ("Oracle" + "Oracle SQL" + "Oracle DB") --

def test_fold_generic_vendor_suffixes_collapses_redundant_naming():
    result = _fold_generic_vendor_suffixes(["Oracle", "Oracle SQL", "Oracle DB"])
    assert result == ["Oracle"]


def test_fold_generic_vendor_suffixes_synthesizes_bare_form_when_absent():
    # No bare "Oracle" in the input - canonical form is still just the
    # shared first word, drawn from an already-present skill, not invented.
    result = _fold_generic_vendor_suffixes(["Oracle SQL", "Oracle DB"])
    assert result == ["Oracle"]


def test_fold_generic_vendor_suffixes_never_collapses_a_distinguishing_product():
    # "Oracle Fusion" is a genuinely different product - the WHOLE group
    # (including "Oracle SQL") is left untouched rather than risk losing it.
    result = _fold_generic_vendor_suffixes(["Oracle", "Oracle SQL", "Oracle Fusion"])
    assert result == ["Oracle", "Oracle SQL", "Oracle Fusion"]


def test_fold_generic_vendor_suffixes_leaves_lone_mention_untouched():
    result = _fold_generic_vendor_suffixes(["Oracle SQL"])
    assert result == ["Oracle SQL"]


def test_fold_generic_vendor_suffixes_confirmed_v2_example_with_database_word():
    # Technical Skills Curation V2's own confirmed example - "Database" is
    # already in _GENERIC_VENDOR_SUFFIX_WORDS, so this needed no new code.
    result = _fold_generic_vendor_suffixes(["Oracle", "Oracle SQL", "Oracle DB", "Oracle Database"])
    assert result == ["Oracle"]


# --- ETL transformation-type grouping ("Router/Lookup/... Transformation") --

def test_group_etl_transformation_types_confirmed_v2_example_subset():
    # Technical Skills Curation V2's own confirmed example uses only 3 of
    # the 5 transformation types - confirms the mechanism isn't hardcoded
    # to a specific count.
    result = _group_etl_transformation_types([
        "Informatica IDMC", "Router Transformation", "Lookup Transformation", "Joiner Transformation",
    ])
    assert result == ["Informatica IDMC", "Informatica Transformations (Router, Lookup, Joiner)"]


def test_group_etl_transformation_types_groups_under_present_vendor_anchor():
    result = _group_etl_transformation_types([
        "Informatica IDMC", "Router Transformation", "Lookup Transformation",
        "Aggregator Transformation", "Joiner Transformation", "Expression Transformation",
    ])
    assert result == [
        "Informatica IDMC",
        "Informatica Transformations (Router, Lookup, Aggregator, Joiner, Expression)",
    ]


def test_group_etl_transformation_types_falls_back_to_generic_label_without_anchor():
    # No Informatica/Talend/SSIS/DataStage/Ab Initio anchor present -
    # never invents a vendor name, uses the generic label instead.
    result = _group_etl_transformation_types(
        ["Router Transformation", "Lookup Transformation", "Aggregator Transformation"]
    )
    assert result == ["Transformations (Router, Lookup, Aggregator)"]


def test_group_etl_transformation_types_leaves_lone_mention_untouched():
    result = _group_etl_transformation_types(["Router Transformation", "Python", "AWS"])
    assert result == ["Router Transformation", "Python", "AWS"]


def test_consolidate_related_skills_handles_the_two_new_confirmed_examples():
    assert _consolidate_related_skills(
        ["Informatica IDMC", "Router Transformation", "Lookup Transformation",
         "Aggregator Transformation", "Joiner Transformation", "Expression Transformation"]
    ) == ["Informatica IDMC", "Informatica Transformations (Router, Lookup, Aggregator, Joiner, Expression)"]

    assert _consolidate_related_skills(["Oracle", "Oracle SQL", "Oracle DB"]) == ["Oracle"]


def test_build_technical_skills_end_to_end_groups_pl_sql_family():
    result = build_technical_skills(["PL/SQL", "Stored Procedures", "Functions", "Views", "Triggers", "Cursors"])
    assert result == ["PL/SQL (Stored Procedures, Functions, Views, Triggers, Cursors)"]


def test_build_technical_skills_end_to_end_reduces_oracle_sql_version_variants():
    result = build_technical_skills(["Oracle SQL (11g)", "Oracle SQL (12c)", "SQL Developer", "SQL"])
    assert "Oracle SQL" in result
    assert "Oracle SQL (11g)" not in result
    assert "Oracle SQL (12c)" not in result
    # "SQL Developer" -> "SQL" (Technical Skills Curation V2 - a confirmed,
    # explicit reversal of the prior round's opposite decision, see
    # _NORMALIZATION_MAP's own comment on why) - normalizes to and
    # exact-dedupes with bare "SQL" into one surviving entry.
    assert "SQL Developer" not in result
    assert "SQL" in result


def test_build_technical_skills_end_to_end_groups_etl_wording_variants():
    result = build_technical_skills(["ETL mappings", "ETL mapping creation", "Workflows (ETL)", "Transformations (ETL)"])
    assert result == ["ETL (Mappings, Mapping creation, Workflows, Transformations)"]


# --- Stages 5 & 6: classify_skill (Noise Removal + Classification) ----------

def test_classify_skill_known_terms():
    assert classify_skill("Python") == "Programming Language"
    assert classify_skill("React") == "Framework"
    assert classify_skill("AWS") == "Cloud"
    assert classify_skill("MySQL") == "Database"
    assert classify_skill("Terraform") == "DevOps"
    assert classify_skill("Linux") == "Infrastructure"
    assert classify_skill("TensorFlow") == "Machine Learning"
    assert classify_skill("Power BI") == "Analytics"
    assert classify_skill("Docker") == "Container"
    assert classify_skill("Power Apps") == "Power Platform"
    assert classify_skill("DNS") == "Networking"
    assert classify_skill("SSL") == "Security"
    # Moved from Security to its own API Integration category (Technical
    # Skills presentation pass) - OAuth/JWT/REST/SOAP/webhooks are API-
    # integration concepts, not general security concepts.
    assert classify_skill("OAuth") == "API Integration"


def test_classify_skill_excludes_noise():
    # "Leadership"/"Communication"/"Business Analysis"/"Problem Solving"
    # are deliberately NOT in this list anymore - Priority 2/6 fix added
    # Soft Skills/Business Skills/Project Management categories precisely
    # so these classify as real skills instead of being excluded as noise
    # (see test_classify_skill_recognizes_business_and_soft_skills below).
    for term in ["Planning", "Architecture", "Team Player", "Fast Learner",
                 "Acme Corp Inc", "Customer Portal Redesign"]:
        assert classify_skill(term) is None, term


def test_classify_skill_recognizes_business_and_soft_skills():
    # Priority 2 fix - a non-technical candidate's Skills section must
    # never disappear entirely just because none of their skills are a
    # tech stack (see test_build_technical_skills_never_empties_pm_resume
    # for the end-to-end regression case this was found from).
    assert classify_skill("Stakeholder Management") == "Business Skills"
    assert classify_skill("Risk Management") == "Business Skills"
    assert classify_skill("Budgeting") == "Business Skills"
    assert classify_skill("Client Management") == "Business Skills"
    assert classify_skill("Negotiation") == "Business Skills"
    assert classify_skill("Scrum") == "Project Management"
    assert classify_skill("Agile") == "Project Management"
    assert classify_skill("Kanban") == "Project Management"
    assert classify_skill("Business Analysis") == "Project Management"
    assert classify_skill("Requirements Gathering") == "Project Management"
    assert classify_skill("Product Management") == "Project Management"
    assert classify_skill("Program Management") == "Project Management"
    assert classify_skill("Change Management") == "Project Management"
    assert classify_skill("Vendor Management") == "Business Skills"
    assert classify_skill("Communication") == "Soft Skills"
    assert classify_skill("Leadership") == "Soft Skills"


def test_build_technical_skills_never_empties_pm_resume():
    # Regression test for the confirmed Priority 2 defect: a Project
    # Manager's raw skill list previously classified as 0/7 real skills
    # (every item excluded as "noise"), so the ENTIRE Technical Skills
    # section rendered empty - a resume must never lose its Skills section
    # just because the candidate is non-technical.
    raw = ["Agile", "Scrum", "Stakeholder Management", "Risk Management",
           "Budgeting", "Negotiation", "Presentation"]
    result = build_technical_skills(raw)
    assert len(result) == len(raw)
    for item in raw:
        assert item in result


def test_classify_skill_no_word_boundary_false_positive():
    assert classify_skill("Java") == "Programming Language"
    assert classify_skill("JavaScript") == "Programming Language"


def test_build_technical_skills_never_drops_unclassifiable_genuine_skill():
    # Root-cause fix: an item classify_skill() can't match to any named
    # category (e.g. a niche/unusual skill this module's keyword
    # dictionaries don't name) must still survive in the output under the
    # "Other Skills" catch-all - never silently excluded.
    raw = ["Python", "Underwater Basket Weaving", "Falconry"]
    result = build_technical_skills(raw)
    assert set(result) == set(raw)


def test_build_technical_skills_grouped_buckets_unclassifiable_as_other_skills():
    raw = ["Python", "Underwater Basket Weaving"]
    grouped = build_technical_skills_grouped(raw)
    # "Programming Language" displays as "Languages" (Technical Skills
    # presentation pass) - same classification, friendlier label.
    assert grouped["Languages"] == ["Python"]
    assert grouped["Other Skills"] == ["Underwater Basket Weaving"]


# --- Technical Skills PRESENTATION: dynamic display labels ------------------

def test_grouped_renames_simple_categories():
    grouped = build_technical_skills_grouped(["Python", "MySQL", "AWS"])
    assert "Languages" in grouped
    assert "Databases" in grouped
    assert "Cloud & Storage" in grouped
    assert "Programming Language" not in grouped
    assert "Database" not in grouped
    assert "Cloud" not in grouped


def test_grouped_splits_analytics_into_dynamic_sub_labels():
    grouped = build_technical_skills_grouped(["Snowflake", "Power BI", "Spark", "Airflow"])
    assert grouped["Data Warehouse"] == ["Snowflake"]
    assert grouped["BI & Reporting"] == ["Power BI"]
    assert grouped["Data Engineering"] == ["Spark"]
    # Airflow is a scheduling/orchestration tool, not a data-PROCESSING one
    # like Spark - split into its own "Orchestration" label (Technical
    # Skills Curation V2 - a newly named allowed category).
    assert grouped["Orchestration"] == ["Airflow"]
    assert "Analytics" not in grouped  # nothing left unmatched to need the fallback label


def test_grouped_analytics_fallback_label_used_when_no_specific_match():
    # A term classified as Analytics but not matching any of the three
    # specific sub-keyword sets keeps the generic "Analytics" label -
    # never dropped, never mis-grouped.
    grouped = build_technical_skills_grouped(["Data Pipeline Design"])
    assert grouped.get("Analytics") == ["Data Pipeline Design"] or any(
        "Data Pipeline Design" in items for items in grouped.values()
    )


def test_grouped_merges_business_skills_and_project_management_into_practices():
    grouped = build_technical_skills_grouped(["Agile", "Stakeholder Management", "Project Management"])
    assert set(grouped["Practices"]) == {"Agile", "Stakeholder Management", "Project Management"}
    assert "Business Skills" not in grouped
    assert "Project Management" not in grouped


def test_grouped_new_api_integration_and_data_quality_categories():
    grouped = build_technical_skills_grouped(["REST API", "OAuth2", "JWT", "Data Quality", "Data Profiling"])
    assert set(grouped["API Integration"]) == {"REST API", "OAuth2", "JWT"}
    assert set(grouped["Data Quality"]) == {"Data Quality", "Data Profiling"}


def test_grouped_never_drops_a_skill_across_the_whole_relabeling_pass():
    raw = [
        "SQL", "PL/SQL", "Stored Procedures", "Triggers", "Views", "Functions", "Python",
        "Snowflake", "Power BI", "ETL", "Spark", "Informatica IDMC",
        "Data Quality", "REST API", "OAuth2", "AWS", "MySQL", "Agile", "Project Management",
    ]
    grouped = build_technical_skills_grouped(raw)
    rendered_text = " ".join(item for items in grouped.values() for item in items)
    # Every original term must still be findable somewhere in the final
    # output - either as its own entry or as a sub-item inside a grouped one.
    for term in ("SQL", "PL/SQL", "Stored Procedures", "Triggers", "Views", "Functions", "Python",
                 "Snowflake", "Power BI", "ETL", "Spark", "Informatica IDMC", "Data Quality",
                 "REST API", "OAuth2", "AWS", "MySQL", "Agile", "Project Management"):
        assert term in rendered_text, f"{term!r} missing from grouped Technical Skills output"


def test_build_technical_skills_grouped_same_final_set_as_flat():
    raw = ["Python", "AWS", "Stakeholder Management", "Underwater Basket Weaving"]
    from app.services.skill_intelligence import build_technical_skills_grouped

    flat = set(build_technical_skills(raw))
    grouped_flat = {item for items in build_technical_skills_grouped(raw).values() for item in items}
    assert flat == grouped_flat == set(raw)


# --- Stage 7: Skill Ranking (never alphabetical) -----------------------------

def test_rank_orders_by_category_priority_not_alphabetically():
    # Deliberately reverse-alphabetical input across categories: a naive
    # alphabetical sort would put "AWS" before "Python", but ranking must
    # place Programming Language (Python) before Cloud (AWS).
    survivors = [("AWS", "Cloud"), ("Python", "Programming Language")]
    assert _rank(survivors) == ["Python", "AWS"]


def test_rank_preserves_first_seen_order_within_same_category():
    survivors = [("Zebra Lang", "Programming Language"), ("Alpha Lang", "Programming Language")]
    # Not alphabetical ("Alpha" before "Zebra") - input order preserved instead.
    assert _rank(survivors) == ["Zebra Lang", "Alpha Lang"]


def test_rank_full_priority_order():
    survivors = [
        ("OAuth", "Security"), ("Docker", "Container"), ("Power BI", "Analytics"),
        ("TensorFlow", "Machine Learning"), ("Linux", "Infrastructure"), ("Terraform", "DevOps"),
        ("MySQL", "Database"), ("AWS", "Cloud"), ("React", "Framework"), ("Python", "Programming Language"),
    ]
    assert _rank(survivors) == [
        "Python", "React", "AWS", "MySQL", "Terraform", "Linux", "TensorFlow", "Power BI", "Docker", "OAuth",
    ]


# --- Stage 8: Quality Validation ---------------------------------------------

def test_quality_validate_is_a_noop_on_already_clean_input():
    assert _quality_validate(["Python", "AWS"]) == ["Python", "AWS"]


def test_quality_validate_catches_residual_duplicates():
    assert _quality_validate(["Python", "python"]) == ["Python"]


# --- Full pipeline: build_technical_skills -----------------------------------

def test_build_technical_skills_full_pipeline_realistic_messy_input():
    raw = [
        "ReactJS", "React.js", "React",  # normalization + duplicate collapse
        "Azure Kubernetes Service", "Azure Container Service", "AKS",  # alias + duplicate collapse
        "My SQL", "Postgres", "Java Script",  # normalization
        "Team Player",  # matches no named category - kept under "Other Skills", never dropped
        "Leadership", "Business Analysis",  # now real skills, see Priority 2/6 fix
        "AWS Certified Solutions Architect - Professional",  # certification leakage - still excluded
        "Bachelor of Science in Computer Science",  # degree leakage - still excluded
        "Python", "Docker", "Terraform", "OAuth",
    ]
    result = build_technical_skills(raw)

    assert "Leadership" in result
    assert "Business Analysis" in result
    # Root-cause fix: a genuine input term that matches no named category
    # must still survive (under "Other Skills") - never silently dropped.
    assert "Team Player" in result
    # Certification/degree LEAKAGE (not a skill at all) is still excluded -
    # that's a different, narrower guard (_looks_like_non_skill/_clean),
    # unaffected by the "Other Skills" catch-all fix.
    assert not any("Certified" in item for item in result)
    assert not any("Bachelor" in item for item in result)
    assert not any("Bachelor" in item for item in result)

    assert result.count("React") == 1
    assert result.count("AKS") == 1
    assert "MySQL" in result
    assert "PostgreSQL" in result
    assert "JavaScript" in result

    # Programming Language (Python) must outrank Container (Docker)/DevOps
    # (Terraform)/Security (OAuth) - never alphabetical.
    assert result.index("Python") < result.index("Docker")
    assert result.index("Python") < result.index("Terraform")
    assert result.index("Python") < result.index("OAuth")


def test_build_technical_skills_has_no_default_cap():
    # A candidate's genuine skill must never silently disappear for length
    # reasons - build_technical_skills no longer truncates by default, even
    # when the input is larger than the old MAX_TECHNICAL_SKILLS guidance.
    raw = [
        "Python", "Java", "JavaScript", "TypeScript", "Go", "Ruby", "PHP", "Swift", "Kotlin", "Scala",
        "React", "Angular", "Vue", "Django", "Flask", "Spring Boot", "Express", "Next.js", "Laravel", "Rails",
        "AWS", "Azure", "GCP", "Heroku", "DigitalOcean",
        "MySQL", "PostgreSQL", "MongoDB", "Redis", "Cassandra",
        "Docker", "Kubernetes", "Jenkins", "Terraform", "Ansible",
        "TensorFlow", "PyTorch", "Power BI", "Tableau", "Kafka",
    ]
    assert len(raw) > MAX_TECHNICAL_SKILLS
    result = build_technical_skills(raw)
    assert len(result) == len(raw)


def test_build_technical_skills_never_pads_below_max():
    assert build_technical_skills(["Python"]) == ["Python"]


def test_build_technical_skills_handles_empty_and_none():
    assert build_technical_skills(None) == []
    assert build_technical_skills([]) == []


def test_build_technical_skills_respects_custom_max_items():
    raw = ["Python", "Java", "React", "AWS", "MySQL"]
    result = build_technical_skills(raw, max_items=2)
    assert len(result) == 2


# --- canonical_technology_name / known_technology_variants (Phase 6 reuse) --

def test_canonical_technology_name_normalization_variant():
    assert canonical_technology_name("ReactJS") == "React"
    assert canonical_technology_name("Postgres") == "PostgreSQL"


def test_canonical_technology_name_alias_variant():
    assert canonical_technology_name("Azure Kubernetes Service") == "AKS"
    assert canonical_technology_name("Visual Studio Code") == "VS Code"


def test_canonical_technology_name_unrecognized_returns_none():
    assert canonical_technology_name("SomeNicheTool") is None


def test_known_technology_variants_contains_expected_entries():
    variants = known_technology_variants()
    assert variants["reactjs"] == "React"
    assert variants["azure kubernetes service"] == "AKS"
    assert isinstance(variants, dict)


# --- New normalization entries (enterprise quality refinement) --------------

def test_normalize_ms_sql_server_three_word_variant():
    assert _normalize(["MS SQL Server"]) == ["SQL Server"]


def test_normalize_microsoft_azure_cloud():
    assert _normalize(["Microsoft Azure Cloud"]) == ["Microsoft Azure"]


def test_normalize_azure_sql_database():
    assert _normalize(["Azure SQL Database"]) == ["Azure SQL"]


def test_normalize_azure_app_service_drops_redundant_prefix():
    assert _normalize(["Azure App Service"]) == ["App Service"]


def test_normalize_csharp_dotnet_variants_preserve_distinction():
    # "C#.NET" (a garbled compound) normalizes to the language alone; "C#
    # ASP.NET" (both named together) normalizes to the more specific
    # framework name - the two are never merged into one vague term, and
    # bare "ASP.NET" is left as its own distinct, correctly-cased entry.
    assert _normalize(["C#.NET"]) == ["C#"]
    assert _normalize(["C# ASP.NET"]) == ["ASP.NET"]
    assert _normalize(["ASP.NET"]) == ["ASP.NET"]
    assert _normalize(["C#"]) == ["C#"]


def test_azure_sql_bare_is_never_merged_into_mysql():
    # Azure SQL (SQL-Server-based) must never collapse into MySQL - a
    # factual-correctness guard carried over from the original design.
    assert _normalize(["Azure SQL"]) == ["Azure SQL"]


# --- Skill Prioritization (relevance-based ranking, backward compatible) ---

def test_rank_without_relevance_context_is_unchanged_from_before():
    # No relevance_scores given (the default) - same first-seen tie-break
    # as always, byte-for-byte identical to pre-refinement behavior.
    survivors = [("Zebra Lang", "Programming Language"), ("Alpha Lang", "Programming Language")]
    assert _rank(survivors) == ["Zebra Lang", "Alpha Lang"]


def test_rank_with_relevance_scores_orders_by_score_within_category():
    survivors = [("Python", "Programming Language"), ("Java", "Programming Language")]
    # Java scores higher than Python here despite Python appearing first.
    assert _rank(survivors, relevance_scores={"Python": 1, "Java": 10}) == ["Java", "Python"]


def test_build_technical_skills_relevance_context_promotes_evidenced_skill():
    context = {
        "summary": "Senior AWS engineer.",
        "experience": [{"role": "AWS Cloud Engineer", "points": ["Built services on AWS."]}],
        "projects": [],
    }
    # Both are "Cloud" category skills (same tier) - AWS is heavily
    # evidenced (title + summary + bullet), Azure isn't mentioned anywhere
    # outside the skills list itself.
    result = build_technical_skills(["Azure", "AWS"], relevance_context=context)
    assert result.index("AWS") < result.index("Azure")


def test_build_technical_skills_without_relevance_context_still_works():
    # Default (no context) - existing callers (ats_intelligence.py, resume_
    # quality_engine.py, resume_scoring_engine.py) are unaffected.
    assert build_technical_skills(["Python", "AWS"]) == ["Python", "AWS"]


# --- Technology Grouping (family clustering, still a flat list) -------------

def test_azure_family_items_cluster_together_within_cloud_category():
    # All four classify under the same "Cloud" category tier - clustering
    # only ever operates WITHIN a tier (never redesigning the existing
    # category classification that spans Cloud/Database/Security/etc.).
    result = build_technical_skills(["Python", "AWS", "Azure Functions", "Azure", "App Service"])
    azure_family_items = {"Azure", "Azure Functions", "App Service"}
    azure_positions = sorted(result.index(item) for item in azure_family_items if item in result)
    assert azure_positions == list(range(azure_positions[0], azure_positions[0] + len(azure_positions)))
    assert "AWS" not in result[azure_positions[0]:azure_positions[-1] + 1]


def test_aws_family_items_cluster_together():
    result = build_technical_skills(["Azure", "EC2", "AWS", "S3"])
    aws_family_items = {"AWS", "EC2", "S3"}
    aws_positions = sorted(result.index(item) for item in aws_family_items if item in result)
    assert aws_positions == list(range(aws_positions[0], aws_positions[0] + len(aws_positions)))


def test_gcp_family_items_cluster_together():
    result = build_technical_skills(["AWS", "GCP", "BigQuery", "Azure"])
    gcp_family_items = {"GCP", "BigQuery"}
    gcp_positions = sorted(result.index(item) for item in gcp_family_items if item in result)
    assert gcp_positions == list(range(gcp_positions[0], gcp_positions[0] + len(gcp_positions)))


# --- Industry-specific templates: ETL Tools / ERP Systems categories ------
# (a candidate whose entire skill set is Informatica/SAP-specific must not
# lose their Skills section, the same class of fix Testing/Project
# Management/Business Skills/Soft Skills already got.)

def test_classify_skill_recognizes_etl_tools():
    assert classify_skill("Informatica PowerCenter") == "ETL Tools"
    assert classify_skill("IICS") == "ETL Tools"
    assert classify_skill("Informatica MDM") == "ETL Tools"
    assert classify_skill("Talend") == "ETL Tools"


def test_classify_skill_recognizes_erp_systems():
    assert classify_skill("SAP HANA") == "ERP Systems"
    assert classify_skill("SAP ABAP") == "ERP Systems"
    assert classify_skill("S/4HANA") == "ERP Systems"
    assert classify_skill("Workday") == "ERP Systems"


def test_build_technical_skills_never_empties_sap_or_informatica_resume():
    sap_skills = ["SAP", "SAP HANA", "SAP ABAP", "SAP FICO"]
    result = build_technical_skills(sap_skills)
    assert len(result) == len(sap_skills)

    informatica_skills = ["Informatica PowerCenter", "IICS", "Informatica MDM"]
    result = build_technical_skills(informatica_skills)
    assert len(result) == len(informatica_skills)


def test_classify_skill_recognizes_extended_qa_terms():
    assert classify_skill("SDET") == "Testing"
    assert classify_skill("Regression Testing") == "Testing"
    assert classify_skill("JMeter") == "Testing"


# --- Industry-specific templates: relevance bonus for industry keywords --

def test_build_technical_skills_industry_keywords_boost_ranking_within_tier():
    # Both classify as "ERP Systems" - a same-tier bonus can reorder WITHIN
    # a tier, never across tiers (category always dominates - see _rank's
    # own sort key). With no industry context, first-seen order wins; with
    # SAP's own priority keywords given, "SAP" (industry-relevant) ranks
    # ahead of "Workday" (a different ERP system, not SAP-relevant).
    skills = ["Workday", "SAP"]
    no_industry = build_technical_skills(skills, relevance_context={"summary": "", "experience": [], "projects": []})
    with_industry = build_technical_skills(
        skills, relevance_context={"summary": "", "experience": [], "projects": []},
        industry_keywords=("sap",),
    )
    assert with_industry.index("SAP") < with_industry.index("Workday")
    assert no_industry == ["Workday", "SAP"]  # unchanged first-seen order without industry context


def test_industry_keywords_defaults_to_noop():
    skills = ["Python", "AWS"]
    assert build_technical_skills(skills) == build_technical_skills(skills, industry_keywords=())
