"""Unit tests for app/services/experience_intelligence.py - the Phase 3
Experience Intelligence Engine. Covers every pipeline stage individually
plus the full enhance_experience()/enhance_projects() orchestration."""
from app.services.experience_intelligence import (
    MAX_PROJECTS,
    classify_project_theme,
    deduplicate_cross_entry_bullets,
    detect_achievements,
    detect_cross_entry_repetition,
    diversify_action_verbs,
    enforce_bullet_cap,
    enhance_experience,
    enhance_job_experience,
    enhance_project,
    enhance_projects,
    extract_metrics,
    filter_non_genuine_career_breaks,
    has_impact_signal,
    is_genuine_career_break,
    max_projects_for_years,
    merge_near_duplicate_bullets,
    page_budget_for_years,
    prioritize_bullets_with_impact,
    rank_and_select_projects,
    remove_experience_project_overlap,
    replace_weak_verb_openers,
    score_project_relevance,
    vary_repeated_generic_phrases,
    years_of_experience,
)
from app.services.experience_intelligence import _recency_adjusted_bullet_cap


# --- STEP 3: Project Classification ------------------------------------------

def test_classify_project_theme_cloud_migration():
    assert classify_project_theme(["Led the cloud migration of legacy workloads via lift and shift."]) == "Cloud Migration"


def test_classify_project_theme_security():
    assert classify_project_theme(["Implemented security hardening and compliance controls for PCI audit."]) == "Security"


def test_classify_project_theme_ai():
    assert classify_project_theme(["Built a generative AI chatbot for customer support automation."]) == "AI"


def test_classify_project_theme_frontend():
    assert classify_project_theme(["Built a React-based user interface for the customer portal."]) == "Frontend Engineering"


def test_classify_project_theme_defaults_to_application_development():
    assert classify_project_theme(["Did some general work.", "Handled tasks."]) == "Application Development"


def test_classify_project_theme_picks_highest_keyword_count():
    bullets = [
        "Migrated data to the new system.",
        "Automated deployment pipelines with CI/CD.",
        "Automated infrastructure provisioning using automation scripts.",
    ]
    # Two "automat*" mentions outweigh one "migrated" mention.
    assert classify_project_theme(bullets) == "Automation"


# --- STEP 5: Action Verb Diversification -------------------------------------

def test_diversify_action_verbs_swaps_repeated_opener():
    bullets = ["Developed the API gateway.", "Developed the internal dashboard."]
    result = diversify_action_verbs(bullets, "Application Development")
    assert result[0] == "Developed the API gateway."
    assert result[1] != "Developed the internal dashboard."
    assert result[1].endswith("the internal dashboard.")  # only the opener changed


def test_diversify_action_verbs_leaves_single_occurrence_untouched():
    bullets = ["Developed the API gateway.", "Automated the deployment pipeline."]
    assert diversify_action_verbs(bullets, "Application Development") == bullets


def test_diversify_action_verbs_ignores_non_verb_openers():
    bullets = ["Did task one.", "Did task two."]
    # "did" is not a recognized strong action verb - left untouched, no crash.
    assert diversify_action_verbs(bullets, "Application Development") == bullets


def test_diversify_action_verbs_never_reuses_a_verb_already_used_as_opener():
    bullets = ["Developed X.", "Developed Y.", "Built Z.", "Developed W."]
    result = diversify_action_verbs(bullets, "Application Development")
    openers = [b.split()[0].rstrip(".") for b in result]
    # "Built" was already used as an opener (bullet 3) - must never be
    # picked again as a replacement for one of the repeated "Developed"s.
    assert openers.count("Built") == 1


def test_diversify_action_verbs_preserves_theme_preference_order():
    bullets = ["Migrated the database.", "Migrated the application servers."]
    result = diversify_action_verbs(bullets, "Cloud Migration")
    assert result[1].split()[0] in {"Modernized", "Orchestrated", "Executed"}


# --- STEP 6: Bullet Compression ----------------------------------------------

def test_merge_near_duplicate_bullets_merges_near_identical_wording():
    bullets = [
        "Led the migration of legacy systems to Azure cloud infrastructure.",
        "Led migration of the legacy system to Azure's cloud infrastructure.",
    ]
    result = merge_near_duplicate_bullets(bullets)
    assert len(result) == 1


def test_merge_near_duplicate_bullets_keeps_genuinely_different_bullets():
    bullets = ["Led the migration to Azure.", "Implemented security hardening for compliance."]
    assert merge_near_duplicate_bullets(bullets) == bullets


def test_merge_near_duplicate_bullets_removes_exact_duplicates():
    bullets = ["Did X.", "Did X.", "Did Y."]
    assert merge_near_duplicate_bullets(bullets) == ["Did X.", "Did Y."]


def test_enforce_bullet_cap_trims_when_over_max():
    bullets = [f"Bullet {i}" for i in range(10)]
    result = enforce_bullet_cap(bullets, 5, 8)
    assert result == bullets[:8]


def test_enforce_bullet_cap_never_pads_below_min():
    bullets = ["Only one bullet."]
    assert enforce_bullet_cap(bullets, 5, 8) == bullets


def test_enforce_bullet_cap_noop_within_range():
    bullets = [f"Bullet {i}" for i in range(6)]
    assert enforce_bullet_cap(bullets, 5, 8) == bullets


# --- STEP 7: Achievement Detection / Metric Extraction -----------------------

def test_extract_metrics_percentage():
    assert extract_metrics("Reduced deployment time by 70%.") == ["70%"]


def test_extract_metrics_dollar_amount():
    assert extract_metrics("Saved $2M in annual cloud spend.") != []


def test_extract_metrics_scale_count():
    assert extract_metrics("Migrated 500+ applications to the new platform.") != []
    assert extract_metrics("Supported 10M users across the platform.") != []


def test_extract_metrics_team_size():
    assert extract_metrics("Led team of 12 engineers.") != []


def test_extract_metrics_none_found():
    assert extract_metrics("Developed backend services for the platform.") == []


def test_has_impact_signal_true_for_metric():
    assert has_impact_signal("Improved accuracy by 15%.")


def test_has_impact_signal_true_for_outcome_language_without_metric():
    assert has_impact_signal("Streamlined the deployment process, reducing manual effort.")


def test_has_impact_signal_false_for_plain_responsibility():
    assert not has_impact_signal("Attended daily stand-up meetings.")


def test_detect_achievements_returns_only_metric_bearing_bullets():
    bullets = ["Reduced cloud costs by 40%.", "Attended meetings.", "Led team of 12 engineers."]
    assert detect_achievements(bullets) == ["Reduced cloud costs by 40%.", "Led team of 12 engineers."]


# --- STEP 8: Business Impact prioritization (never invents new text) --------

def test_prioritize_bullets_with_impact_moves_impact_bullets_first():
    bullets = ["Attended meetings.", "Reduced costs by 40%.", "Wrote documentation."]
    result = prioritize_bullets_with_impact(bullets)
    assert result[0] == "Reduced costs by 40%."
    assert set(result) == set(bullets)


def test_prioritize_bullets_with_impact_preserves_relative_order_within_groups():
    bullets = ["Reduced costs by 10%.", "Attended meetings.", "Improved uptime by 20%.", "Wrote documentation."]
    result = prioritize_bullets_with_impact(bullets)
    assert result == ["Reduced costs by 10%.", "Improved uptime by 20%.", "Attended meetings.", "Wrote documentation."]


def test_prioritize_bullets_with_impact_never_invents_or_drops_content():
    bullets = ["A metric-free bullet.", "Another plain bullet."]
    result = prioritize_bullets_with_impact(bullets)
    assert sorted(result) == sorted(bullets)


# --- STEP 9: Cross-entry Deduplication (detect-only) -------------------------

def test_detect_cross_entry_repetition_flags_similar_wording_across_entries():
    entries = [
        {"company": "Acme", "points": ["Led the migration of legacy systems to Azure cloud infrastructure."]},
        {"company": "Globex", "points": ["Led migration of legacy systems to Azure's cloud infrastructure."]},
    ]
    warnings = detect_cross_entry_repetition(entries)
    assert len(warnings) == 1
    assert "Acme" in warnings[0] and "Globex" in warnings[0]


def test_detect_cross_entry_repetition_ignores_within_entry_similarity():
    entries = [
        {"company": "Acme", "points": ["Led the migration of legacy systems to Azure cloud infrastructure.",
                                        "Led migration of legacy systems to Azure's cloud infrastructure."]},
    ]
    assert detect_cross_entry_repetition(entries) == []


def test_detect_cross_entry_repetition_no_false_positive_for_different_work():
    entries = [
        {"company": "Acme", "points": ["Led the migration to Azure."]},
        {"company": "Globex", "points": ["Implemented security hardening for PCI compliance."]},
    ]
    assert detect_cross_entry_repetition(entries) == []


def test_detect_cross_entry_repetition_never_removes_anything():
    # Purely a detection/logging function - the entries dict itself must
    # never be mutated (no bullet removed from either job).
    entries = [
        {"company": "Acme", "points": ["Led the migration of legacy systems to Azure cloud infrastructure."]},
        {"company": "Globex", "points": ["Led migration of legacy systems to Azure's cloud infrastructure."]},
    ]
    detect_cross_entry_repetition(entries)
    assert len(entries[0]["points"]) == 1
    assert len(entries[1]["points"]) == 1


# --- Orchestration: enhance_job_experience / enhance_experience -------------

def test_enhance_job_experience_leaves_career_break_untouched():
    exp = {"company": "Career Break", "is_career_break": True, "points": ["Traveled.", "Took time off."]}
    assert enhance_job_experience(exp) == exp


def test_enhance_job_experience_preserves_non_point_fields():
    exp = {
        "company": "Acme", "role": "Engineer", "duration": "2020-2024",
        "points": ["Developed the API.", "Developed the dashboard."],
        "reason_for_leaving": "Relocation", "notes": "PF settled", "is_career_break": False, "break_detail": "",
    }
    result = enhance_job_experience(exp)
    assert result["company"] == "Acme"
    assert result["duration"] == "2020-2024"
    assert result["reason_for_leaving"] == "Relocation"
    assert result["notes"] == "PF settled"


def test_enhance_job_experience_never_fabricates_new_bullets():
    exp = {"company": "Acme", "role": "Engineer", "points": ["Developed the API.", "Built the dashboard."]}
    result = enhance_job_experience(exp)
    # Every output bullet must be traceable to an input bullet (only the
    # opening verb may differ) - no wholly new sentence content invented.
    for bullet in result["points"]:
        assert any(bullet.split(" ", 1)[-1] == original.split(" ", 1)[-1] for original in exp["points"])


def test_enhance_job_experience_handles_missing_points():
    exp = {"company": "Acme", "role": "Engineer"}
    result = enhance_job_experience(exp)
    assert result["company"] == "Acme"


def test_enhance_experience_handles_empty_and_multiple_jobs():
    assert enhance_experience([]) == []
    jobs = [
        {"company": "Acme", "role": "Engineer", "points": ["Developed X.", "Built Y."]},
        {"company": "Globex", "role": "Senior Engineer", "points": ["Migrated Z.", "Automated W."]},
    ]
    result = enhance_experience(jobs)
    assert len(result) == 2
    assert {job["company"] for job in result} == {"Acme", "Globex"}


def test_enhance_experience_respects_bullet_range_from_phase1_tiering():
    # A "standard" tier role (Phase 1's existing (5, 8) range) - 20 raw
    # bullets must be trimmed down to at most 8, never regressing Phase 1's
    # own established tiering.
    points = [f"Developed feature {i} for the platform." for i in range(20)]
    exp = {"company": "Acme", "role": "Software Engineer", "points": points}
    result = enhance_job_experience(exp)
    assert len(result["points"]) <= 8


# --- Orchestration: enhance_project / enhance_projects -----------------------

def test_enhance_project_caps_at_five_bullets():
    proj = {"title": "Internal Tool", "responsibilities": [f"Built feature {i}." for i in range(10)]}
    result = enhance_project(proj)
    assert len(result["responsibilities"]) <= 5


def test_enhance_project_preserves_other_fields():
    proj = {
        "title": "Internal Tool", "role": "Lead", "description": "A tool.",
        "technologies": "Python, AWS", "responsibilities": ["Built the tool."],
    }
    result = enhance_project(proj)
    assert result["title"] == "Internal Tool"
    assert result["description"] == "A tool."
    assert result["technologies"] == "Python, AWS"


def test_enhance_project_handles_no_responsibilities():
    proj = {"title": "Internal Tool", "responsibilities": []}
    assert enhance_project(proj) == proj


def test_enhance_projects_handles_empty_and_multiple():
    assert enhance_projects([]) == []
    projects = [
        {"title": "Tool A", "responsibilities": ["Built A."]},
        {"title": "Tool B", "responsibilities": ["Built B."]},
    ]
    result = enhance_projects(projects)
    assert len(result) == 2


# --- Action Verb Variety (new verbs added for enterprise quality refinement) -

def test_diversify_action_verbs_recognizes_new_verbs():
    bullets = ["Transformed the legacy platform.", "Transformed the deployment process."]
    result = diversify_action_verbs(bullets, "Digital Transformation")
    assert result[0] == "Transformed the legacy platform."
    assert result[1] != "Transformed the deployment process."
    assert result[1].endswith("the deployment process.")


def test_diversify_action_verbs_mentored_and_reviewed_are_recognized():
    bullets = ["Mentored junior engineers.", "Reviewed pull requests daily."]
    # Neither repeats, so both stay untouched - just confirms these verbs
    # are recognized (not silently ignored as "not an opener at all").
    assert diversify_action_verbs(bullets, "Support") == bullets


# --- Weak Verb Opener Replacement (bullet writing style improvement) --------

def test_replace_weak_verb_openers_promotes_gerund_after_filler_phrase():
    bullets = ["Responsible for managing the Snowflake data warehouse and 5M+ records."]
    result = replace_weak_verb_openers(bullets)
    assert result == ["Managed the Snowflake data warehouse and 5M+ records."]


def test_replace_weak_verb_openers_preserves_every_technical_term_and_metric():
    bullet = "Was involved in developing 40+ Informatica IDMC CDI mappings, cutting effort by ~30%."
    result = replace_weak_verb_openers([bullet])
    assert result == ["Developed 40+ Informatica IDMC CDI mappings, cutting effort by ~30%."]


def test_replace_weak_verb_openers_handles_several_known_phrases():
    bullets = [
        "Worked on designing the ETL pipeline architecture.",
        "Helped with building the CI/CD deployment process.",
        "In charge of coordinating cross-team release schedules.",
    ]
    result = replace_weak_verb_openers(bullets)
    assert result == [
        "Designed the ETL pipeline architecture.",
        "Built the CI/CD deployment process.",
        "Coordinated cross-team release schedules.",
    ]


def test_replace_weak_verb_openers_leaves_unrecognized_phrase_unchanged():
    bullet = "Assisted the team with quarterly planning."  # not one of the known filler phrases
    assert replace_weak_verb_openers([bullet]) == [bullet]


def test_replace_weak_verb_openers_falls_back_to_generic_verb_when_no_gerund_follows():
    # "Responsible for" IS recognized, but "onboarding" isn't in the closed
    # gerund set - falls back to the phrase's own mapped strong verb
    # instead (case 2), rather than promoting a gerund it can't verify.
    bullet = "Responsible for onboarding new hires."
    assert replace_weak_verb_openers([bullet]) == ["Managed onboarding new hires."]


def test_replace_weak_verb_openers_fallback_verb_matches_phrases_original_scope():
    # "Worked on"/"Helped with" imply a modest, collaborative role - the
    # fallback must not inflate that into "Led"/"Directed".
    assert replace_weak_verb_openers(["Worked on the ETL pipeline design."]) == [
        "Contributed to the ETL pipeline design."
    ]
    assert replace_weak_verb_openers(["Helped with production support calls."]) == [
        "Supported production support calls."
    ]


def test_replace_weak_verb_openers_handles_standalone_handled():
    bullet = "Handled customer escalations and RCA reports for enterprise accounts."
    assert replace_weak_verb_openers([bullet]) == [
        "Managed customer escalations and RCA reports for enterprise accounts."
    ]


def test_replace_weak_verb_openers_leaves_strong_openers_unchanged():
    bullets = ["Architected the migration to AWS.", "Led a team of 5 engineers."]
    assert replace_weak_verb_openers(bullets) == bullets


def test_replace_weak_verb_openers_skips_coordinated_second_verb():
    # CONFIRMED real-world case (found scanning actual regression sample
    # resumes): promoting only the first of two coordinated verbs would
    # produce "Led & manage Business waste..." - past tense mixed with a
    # bare-form verb, grammatically broken. Must leave the whole bullet
    # untouched instead of half-rewriting it.
    bullet = "Responsible for leading & manage Business waste project for Unilever Categories."
    assert replace_weak_verb_openers([bullet]) == [bullet]


def test_replace_weak_verb_openers_wired_into_enhance_job_experience():
    exp = {
        "company": "Acme", "role": "Engineer", "duration": "2020-2024",
        "points": ["Responsible for managing the production Snowflake environment."],
    }
    enhanced = enhance_job_experience(exp)
    assert enhanced["points"][0] == "Managed the production Snowflake environment."


# --- Business Impact (expanded impact-signal keywords) ----------------------

def test_has_impact_signal_recognizes_business_impact_themes():
    assert has_impact_signal("Led the cloud migration to Azure.")
    assert has_impact_signal("Drove cost reduction across the infrastructure budget.")
    assert has_impact_signal("Implemented security hardening for compliance.")
    assert has_impact_signal("Directed the digital transformation initiative.")


def test_has_impact_signal_still_false_for_plain_responsibility():
    assert not has_impact_signal("Attended daily stand-up meetings.")


# --- Experience Deduplication: vary_repeated_generic_phrases -----------------

def test_vary_repeated_generic_phrases_keeps_first_entry_unchanged():
    entries = [
        {"company": "Acme", "points": ["Performed requirements analysis for the platform."]},
        {"company": "Globex", "points": ["Performed requirements analysis for the new system."]},
    ]
    changed = vary_repeated_generic_phrases(entries)
    assert changed == 1
    assert entries[0]["points"][0] == "Performed requirements analysis for the platform."
    assert "requirements analysis" not in entries[1]["points"][0].lower()
    assert entries[1]["points"][0].endswith("for the new system.")


def test_vary_repeated_generic_phrases_never_touches_single_occurrence():
    entries = [
        {"company": "Acme", "points": ["Performed requirements analysis for the platform."]},
        {"company": "Globex", "points": ["Deployed a new monitoring stack."]},
    ]
    original = [dict(e) for e in entries]
    changed = vary_repeated_generic_phrases(entries)
    assert changed == 0
    assert entries == original


def test_vary_repeated_generic_phrases_works_across_experience_and_projects():
    entries = [
        {"company": "Acme", "points": ["Provided technical guidance to the team."]},
        {"title": "Internal Tool", "responsibilities": ["Provided technical guidance on architecture."]},
    ]
    changed = vary_repeated_generic_phrases(entries)
    assert changed == 1
    assert "technical guidance" not in entries[1]["responsibilities"][0].lower()


def test_vary_repeated_generic_phrases_never_reuses_original_wording_across_many_repeats():
    entries = [
        {"company": f"Company{i}", "points": ["Agile grooming for the sprint backlog."]}
        for i in range(4)
    ]
    vary_repeated_generic_phrases(entries)
    # First entry keeps the original; none of the REST should have been
    # reset back to the literal original phrase, even after cycling.
    assert entries[0]["points"][0] == "Agile grooming for the sprint backlog."
    for entry in entries[1:]:
        assert "agile grooming" not in entry["points"][0].lower()


def test_vary_repeated_generic_phrases_preserves_surrounding_text():
    entries = [
        {"company": "Acme", "points": ["Analyzed business requirements for the payments platform."]},
        {"company": "Globex", "points": ["Analyzed business requirements for the billing system."]},
    ]
    vary_repeated_generic_phrases(entries)
    assert entries[1]["points"][0].endswith("for the billing system.")


def test_vary_repeated_generic_phrases_returns_zero_for_empty_input():
    assert vary_repeated_generic_phrases([]) == 0


# --- Resume Compression: recency-weighted bullet allocation -----------------

_DISTINCT_BULLETS = [
    "Built the customer-facing checkout API.",
    "Automated the nightly billing reconciliation job.",
    "Migrated the search index to Elasticsearch.",
    "Mentored two junior engineers on code review practices.",
    "Reduced API latency by optimizing database queries.",
    "Integrated the payments gateway with the order service.",
    "Established a shared component library across teams.",
    "Led the rollout of feature-flagged deployments.",
    "Improved test coverage across the notification service.",
    "Standardized logging conventions for the platform.",
]


def test_enhance_job_experience_recent_job_keeps_full_tier_max():
    exp = {"company": "Acme", "role": "Software Engineer", "points": list(_DISTINCT_BULLETS)}
    # Most recent job (index 0 of many) - never reduced below its tier's
    # own max (standard tier caps at 8).
    result = enhance_job_experience(exp, job_index=0, total_jobs=5)
    assert len(result["points"]) == 8


def test_enhance_job_experience_older_job_gets_tighter_cap():
    exp = {"company": "OldCo", "role": "Software Engineer", "points": list(_DISTINCT_BULLETS)}
    recent = enhance_job_experience(dict(exp), job_index=0, total_jobs=5)
    older = enhance_job_experience(dict(exp), job_index=4, total_jobs=5)
    assert len(older["points"]) < len(recent["points"])


def test_enhance_job_experience_never_reduces_below_two_bullet_floor():
    exp = {"company": "VeryOldCo", "role": "Software Engineer", "points": list(_DISTINCT_BULLETS)}
    result = enhance_job_experience(exp, job_index=20, total_jobs=21)
    assert len(result["points"]) >= 2


def test_enhance_job_experience_default_args_reproduce_prior_behavior():
    # No recency context given (the exact call shape every pre-existing
    # caller/test uses) - identical to calling with a single-job resume.
    exp = {"company": "Acme", "role": "Software Engineer", "points": list(_DISTINCT_BULLETS)}
    assert enhance_job_experience(dict(exp)) == enhance_job_experience(dict(exp), job_index=0, total_jobs=1)


def test_enhance_experience_prioritizes_recent_over_older_jobs():
    jobs = [
        {"company": f"Company{i}", "role": "Software Engineer", "points": list(_DISTINCT_BULLETS)}
        for i in range(5)
    ]
    result = enhance_experience(jobs)
    bullet_counts = [len(job["points"]) for job in result]
    # Non-increasing bullet counts as we move from most-recent (index 0) to
    # oldest (last index) - never the other way around.
    assert bullet_counts == sorted(bullet_counts, reverse=True)


# --- Project Selection: score_project_relevance -----------------------------

def test_score_project_relevance_rewards_architecture():
    proj = {"title": "Platform", "description": "Led the solution architecture for the enterprise platform."}
    assert score_project_relevance(proj) > 0


def test_score_project_relevance_rewards_business_impact():
    proj = {"title": "Platform", "responsibilities": ["Reduced infrastructure costs by 30%."]}
    assert score_project_relevance(proj) >= 3


def test_score_project_relevance_rewards_cloud_and_leadership():
    proj = {"title": "Migration", "description": "Led the team migrating workloads to Azure."}
    score = score_project_relevance(proj)
    generic = score_project_relevance({"title": "Generic", "description": "Did some work."})
    assert score > generic


def test_score_project_relevance_zero_for_generic_project():
    assert score_project_relevance({"title": "Tool", "description": "Built a small internal tool."}) == 0


# --- Project Selection: rank_and_select_projects -----------------------------

def test_rank_and_select_projects_noop_when_under_cap():
    projects = [{"title": f"Project {i}"} for i in range(MAX_PROJECTS)]
    assert rank_and_select_projects(projects) is projects


def test_rank_and_select_projects_keeps_strongest_when_over_cap():
    strong = {"title": "Enterprise Migration",
              "description": "Led the enterprise-scale cloud migration architecture for a global client.",
              "responsibilities": ["Reduced costs by 40%."]}
    weak_projects = [{"title": f"Minor Tool {i}", "description": "Built a small internal tool."} for i in range(MAX_PROJECTS)]
    projects = weak_projects + [strong]
    result = rank_and_select_projects(projects, max_projects=MAX_PROJECTS)
    assert strong in result
    assert len(result) == MAX_PROJECTS


def test_rank_and_select_projects_preserves_original_relative_order():
    strong = {"title": "Enterprise Migration",
              "description": "Led the enterprise-scale cloud migration architecture for a global client.",
              "responsibilities": ["Reduced costs by 40%."]}
    weak_projects = [{"title": f"Minor Tool {i}", "description": "Built a small internal tool."} for i in range(MAX_PROJECTS)]
    projects = [strong] + weak_projects
    result = rank_and_select_projects(projects, max_projects=MAX_PROJECTS)
    assert result[0] is strong  # strong was first in the input - stays first, not re-sorted to wherever


def test_rank_and_select_projects_never_rewrites_kept_project_content():
    strong = {"title": "Enterprise Migration", "description": "Led the enterprise cloud migration architecture."}
    projects = [strong] + [{"title": f"Minor {i}", "description": "Did small work."} for i in range(MAX_PROJECTS)]
    result = rank_and_select_projects(projects, max_projects=MAX_PROJECTS)
    assert strong in result
    assert result[0] == {"title": "Enterprise Migration", "description": "Led the enterprise cloud migration architecture."}


def test_rank_and_select_projects_handles_empty_and_none_max():
    assert rank_and_select_projects([]) == []


def test_score_project_relevance_industry_keywords_bonus_is_additive_and_optional():
    proj = {"title": "Platform", "description": "Built the data pipeline using Informatica PowerCenter."}
    base_score = score_project_relevance(proj)
    boosted_score = score_project_relevance(proj, industry_keywords=("informatica", "etl"))
    assert boosted_score == base_score + 2
    # No industry_keywords passed (the default) reproduces the exact prior score - no-op guarantee.
    assert score_project_relevance(proj) == base_score


def test_score_project_relevance_industry_keywords_noop_when_absent_from_project():
    proj = {"title": "Platform", "description": "Built a small internal tool."}
    assert score_project_relevance(proj, industry_keywords=("sap", "s/4hana")) == score_project_relevance(proj)


def test_rank_and_select_projects_industry_keywords_promotes_matching_project_over_cap():
    sap_project = {"title": "ERP Rollout", "description": "Configured SAP FICO modules."}
    other_projects = [{"title": f"Minor Tool {i}", "description": "Built a small internal tool."} for i in range(MAX_PROJECTS)]
    projects = other_projects + [sap_project]
    without_bonus = rank_and_select_projects(projects, max_projects=MAX_PROJECTS)
    assert sap_project not in without_bonus  # confirms the fixture is genuinely over cap and low-relevance without help

    with_bonus = rank_and_select_projects(projects, max_projects=MAX_PROJECTS, industry_keywords=("sap",))
    assert sap_project in with_bonus


# --- Priority 1 fix: true cross-company Experience Deduplication ------------
# (deduplicate_cross_entry_bullets - unlike detect_cross_entry_repetition
# above, this one ACTS: rewrites or drops a bullet repeated verbatim across
# different companies, instead of only logging it.)

def test_deduplicate_cross_entry_bullets_rewords_repeated_verbatim_bullet():
    entries = [
        {"company": "Company A", "points": ["Automated CI/CD pipelines.", "Reduced downtime by 20%."]},
        {"company": "Company B", "points": ["Automated CI/CD pipelines.", "Migrated legacy servers."]},
    ]
    changed = deduplicate_cross_entry_bullets(entries, "points")
    assert changed >= 1
    # Company A (first/most recent) keeps its original wording untouched.
    assert entries[0]["points"][0] == "Automated CI/CD pipelines."
    # Company B's duplicate no longer reads identically to Company A's.
    assert entries[1]["points"][0] != "Automated CI/CD pipelines."
    # The underlying fact/technology (CI/CD pipelines) is preserved, only
    # the opening verb changed.
    assert "CI/CD pipelines" in entries[1]["points"][0]


def test_deduplicate_cross_entry_bullets_drops_when_reword_still_duplicate():
    # A bullet with no recognized opening verb can't be safely reworded by
    # a verb swap - "keep only the strongest version" applies instead, as
    # long as the entry keeps at least one other bullet.
    entries = [
        {"company": "Company A", "points": ["Support.", "Reduced downtime by 20%."]},
        {"company": "Company B", "points": ["Support.", "Migrated legacy servers."]},
    ]
    deduplicate_cross_entry_bullets(entries, "points")
    assert entries[1]["points"] == ["Migrated legacy servers."]


def test_deduplicate_cross_entry_bullets_never_empties_an_entry():
    entries = [
        {"company": "Company A", "points": ["Support."]},
        {"company": "Company B", "points": ["Support."]},
    ]
    deduplicate_cross_entry_bullets(entries, "points")
    assert len(entries[1]["points"]) >= 1


def test_deduplicate_cross_entry_bullets_never_touches_career_break():
    entries = [
        {"company": "Company A", "points": ["Managed CI/CD pipelines."]},
        {"company": "Career Break", "is_career_break": True, "points": ["Managed CI/CD pipelines."]},
    ]
    deduplicate_cross_entry_bullets(entries, "points")
    assert entries[1]["points"] == ["Managed CI/CD pipelines."]


def test_deduplicate_cross_entry_bullets_leaves_genuinely_different_bullets_alone():
    entries = [
        {"company": "Company A", "points": ["Migrated the database to PostgreSQL."]},
        {"company": "Company B", "points": ["Built a customer-facing checkout API."]},
    ]
    before = [dict(e) for e in entries]
    deduplicate_cross_entry_bullets(entries, "points")
    assert entries == before


def test_deduplicate_cross_entry_bullets_works_on_project_responsibilities():
    entries = [
        {"title": "Project A", "responsibilities": ["Automated deployments."]},
        {"title": "Project B", "responsibilities": ["Automated deployments."]},
    ]
    changed = deduplicate_cross_entry_bullets(entries, "responsibilities")
    assert changed >= 1
    assert entries[0]["responsibilities"][0] == "Automated deployments."


def test_enhance_experience_removes_cross_company_verbatim_duplicates():
    # End-to-end regression for the confirmed defect: three jobs that each
    # repeat the exact same boilerplate bullets no longer render identically.
    boilerplate = ["Managed CI/CD pipelines.", "Automated deployments.", "Monitored production systems."]
    experience = [
        {"company": "CloudScale Inc", "role": "Senior DevOps Engineer", "duration": "2021-2024",
         "points": list(boilerplate), "is_career_break": False},
        {"company": "NetOps Systems", "role": "DevOps Engineer", "duration": "2018-2021",
         "points": list(boilerplate), "is_career_break": False},
        {"company": "Infra Solutions", "role": "DevOps Engineer", "duration": "2015-2018",
         "points": list(boilerplate), "is_career_break": False},
    ]
    result = enhance_experience(experience)
    job_a, job_b, job_c = result[0]["points"], result[1]["points"], result[2]["points"]
    assert job_a == boilerplate  # most recent job keeps the original wording
    assert job_b != job_a
    assert job_c != job_a
    assert job_c != job_b


# --- Experience/Projects overlap removal (this round's Priority 1 fix) -----

def test_remove_experience_project_overlap_drops_project_shaped_role_entry():
    experience = [
        {"company": "Acme Corp", "role": "Senior Solutions Architect", "duration": "2021-2024",
         "points": ["Led enterprise architecture initiatives."], "is_career_break": False},
        {"company": "Acme Corp, Bangalore", "role": "Digitalized Closing File", "duration": "Oct 2021-till date",
         "points": [
             "Understanding business requirements.",
             "Designing scalable, secure solutions, encompassing system design, database design, and data flow.",
             "Providing technical guidance to the team.",
         ], "is_career_break": False},
    ]
    projects = [
        {"title": "Digitalized Closing File", "responsibilities": [
            "Understanding business requirements",
            "Designing scalable, secure solutions (system design, database design and data flow)",
            "Technical guidance to team",
        ]},
    ]
    cleaned, removed = remove_experience_project_overlap(experience, projects)
    assert removed == 1
    assert len(cleaned) == 1
    assert cleaned[0]["role"] == "Senior Solutions Architect"


def test_remove_experience_project_overlap_drops_empty_role_content_duplicate():
    experience = [
        {"company": "Acme Corp", "role": "Cloud Architect", "duration": "2020-2024",
         "points": ["Directed the cloud migration program."], "is_career_break": False},
        {"company": "Acme Corp, Bangalore", "role": "", "duration": "Oct 2021-till date",
         "points": ["Understanding business requirements.", "Designing scalable, secure solutions."],
         "is_career_break": False},
    ]
    projects = [
        {"title": "Some Project", "responsibilities": [
            "Understanding business requirements", "Designing scalable, secure solutions",
        ]},
    ]
    cleaned, removed = remove_experience_project_overlap(experience, projects)
    assert removed == 1
    assert len(cleaned) == 1


def test_remove_experience_project_overlap_never_removes_content_with_no_matching_project():
    experience = [
        {"company": "Acme Corp", "role": "Cloud Architect", "duration": "2020-2024",
         "points": ["Directed the cloud migration program."], "is_career_break": False},
        {"company": "Acme Corp, Bangalore", "role": "", "duration": "Jan 2020-Mar 2020",
         "points": ["Some genuinely unique responsibility never mentioned elsewhere at all."],
         "is_career_break": False},
    ]
    projects = [{"title": "Unrelated Project", "responsibilities": ["Totally different content."]}]
    cleaned, removed = remove_experience_project_overlap(experience, projects)
    assert removed == 0
    assert len(cleaned) == 2


def test_remove_experience_project_overlap_never_touches_career_break():
    experience = [
        {"company": "Acme Corp", "role": "Engineer", "duration": "2020-2024",
         "points": ["Built services."], "is_career_break": False},
        {"company": "Career Break", "role": "", "duration": "2019-2020",
         "points": ["Period between employment roles."], "is_career_break": True},
    ]
    cleaned, removed = remove_experience_project_overlap(experience, [])
    assert removed == 0
    assert len(cleaned) == 2


def test_remove_experience_project_overlap_noop_when_no_projects():
    experience = [
        {"company": "Acme Corp", "role": "Engineer", "duration": "2020-2024",
         "points": ["Built services."], "is_career_break": False},
    ]
    cleaned, removed = remove_experience_project_overlap(experience, [])
    assert removed == 0
    assert cleaned == experience


# --- Career Break genuine-evidence gate (this round's Priority 4 fix) ------

def test_is_genuine_career_break_true_with_stated_reason():
    exp = {"is_career_break": True, "duration": "Jan 2023 - Feb 2023", "break_detail": "Medical leave."}
    assert is_genuine_career_break(exp)


def test_is_genuine_career_break_true_for_long_unexplained_gap():
    exp = {"is_career_break": True, "duration": "Jan 2020 - Dec 2020", "break_detail": ""}
    assert is_genuine_career_break(exp)


def test_is_genuine_career_break_false_for_short_unexplained_gap():
    exp = {"is_career_break": True, "duration": "September 2020 - October 2020", "break_detail": ""}
    assert not is_genuine_career_break(exp)


def test_is_genuine_career_break_false_for_non_break_entry():
    exp = {"is_career_break": False, "duration": "September 2020 - October 2020"}
    assert not is_genuine_career_break(exp)


def test_is_genuine_career_break_true_when_duration_unparseable():
    exp = {"is_career_break": True, "duration": "recently", "break_detail": ""}
    assert is_genuine_career_break(exp)


def test_filter_non_genuine_career_breaks_removes_short_unexplained_gaps():
    experience = [
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": [], "is_career_break": False},
        {"company": "Career Break", "role": "", "duration": "March 2019 - April 2019",
         "points": ["Period between employment roles."], "is_career_break": True, "break_detail": ""},
    ]
    cleaned, removed = filter_non_genuine_career_breaks(experience)
    assert removed == 1
    assert len(cleaned) == 1
    assert cleaned[0]["company"] == "Acme"


def test_filter_non_genuine_career_breaks_keeps_genuine_ones():
    experience = [
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": [], "is_career_break": False},
        {"company": "Career Break", "role": "", "duration": "Jan 2020 - Aug 2020",
         "points": ["Took a sabbatical."], "is_career_break": True, "break_detail": "Family sabbatical."},
    ]
    cleaned, removed = filter_non_genuine_career_breaks(experience)
    assert removed == 0
    assert len(cleaned) == 2


# --- Resume Length: years_of_experience / page_budget_for_years / --------
# --- max_projects_for_years (this round's page-bracket feature) ----------

def test_years_of_experience_computes_span_from_durations():
    experience = [
        {"company": "Acme", "duration": "2020-2024"},
        {"company": "Globex", "duration": "2010-2020"},
    ]
    assert years_of_experience(experience) == 14  # 2024 - 2010


def test_years_of_experience_zero_when_no_durations():
    assert years_of_experience([{"company": "Acme", "duration": ""}]) == 0.0
    assert years_of_experience([]) == 0.0
    assert years_of_experience(None) == 0.0


def test_years_of_experience_handles_present():
    import datetime
    experience = [{"company": "Acme", "duration": "2015-Present"}]
    assert years_of_experience(experience) == datetime.date.today().year - 2015


def test_years_of_experience_handles_apostrophe_year_shorthand():
    # Regression test: a real candidate's resume used "Aug'23 - till date"
    # -style shorthand for EVERY genuine job - the plain 4-digit _YEAR_RE
    # alone can't see "'23" at all, silently computing 0 years for a
    # candidate with a real 18-year career (and therefore the smallest
    # page-cap/project-cap bracket instead of the correct one).
    import datetime
    experience = [
        {"company": "InspiriSYS", "duration": "Aug'23 - till date"},
        {"company": "Trelleborg", "duration": "Nov'20 - Jul'23"},
        {"company": "Evoke", "duration": "May'19 - Aug'20"},
        {"company": "Accenture", "duration": "Apr'14 -Feb'19"},
        {"company": "Honeywell", "duration": "Apr'08 - Mar'14"},
    ]
    assert years_of_experience(experience) == datetime.date.today().year - 2008


def test_years_of_experience_handles_curly_apostrophe_year_shorthand():
    # Regression test: a REAL candidate's resume PDF-extracted with a
    # curly right-single-quote (U+2019, "Aug'23") rather than a straight
    # ASCII apostrophe - the straight-apostrophe-only regex silently never
    # matched this, computing 0 years for a real 18-year career.
    experience = [
        {"company": "InspiriSYS", "duration": "Aug’23 - till date"},
        {"company": "Honeywell", "duration": "Apr’08 - Mar’14"},
    ]
    import datetime
    assert years_of_experience(experience) == datetime.date.today().year - 2008


def test_page_budget_for_years_matches_talent_acquisition_brackets():
    assert page_budget_for_years(0) == 2
    assert page_budget_for_years(4.9) == 2
    assert page_budget_for_years(5) == 3
    assert page_budget_for_years(9.9) == 3
    assert page_budget_for_years(10) == 4
    assert page_budget_for_years(14.9) == 4
    assert page_budget_for_years(15) == 5
    assert page_budget_for_years(30) == 5


def test_max_projects_for_years_scales_with_page_budget():
    assert max_projects_for_years(0) == 3
    assert max_projects_for_years(6) == 4
    assert max_projects_for_years(12) == 5
    assert max_projects_for_years(20) == 6 == MAX_PROJECTS


def test_enhance_job_experience_default_max_pages_reproduces_prior_reduction_curve():
    # No max_pages passed (defaults to 2, the pre-existing grace period) -
    # job_index=4 is 3 jobs past the 2-job grace period, so reduction=3.
    # Uses _DISTINCT_BULLETS (genuinely different sentences), not templated
    # text - templated bullets like "Did thing N" score >0.85 similar via
    # SequenceMatcher and get merged by merge_near_duplicate_bullets before
    # the recency cap ever gets a chance to matter (a known pitfall - see
    # this file's own _DISTINCT_BULLETS comment history).
    result = enhance_job_experience(
        {"role": "Software Engineer", "points": list(_DISTINCT_BULLETS[:8]), "is_career_break": False},
        job_index=4, total_jobs=6,
    )
    assert len(result["points"]) == 8 - 3


def test_recency_adjusted_bullet_cap_grace_period_scales_with_max_pages():
    # Direct, isolated test of the pure arithmetic (no merge/cross-company-
    # dedup interference possible) - a 5-page budget's grace period covers
    # jobs 0-4 (all keep the full base_max); a 2-page budget's covers only
    # jobs 0-1, same as before this parameter existed.
    for job_index in range(5):
        assert _recency_adjusted_bullet_cap(8, job_index, total_jobs=6, max_pages=5) == 8
    assert _recency_adjusted_bullet_cap(8, 5, total_jobs=6, max_pages=5) < 8

    assert _recency_adjusted_bullet_cap(8, 2, total_jobs=6, max_pages=2) < 8
    assert _recency_adjusted_bullet_cap(8, 0, total_jobs=6, max_pages=2) == 8
    assert _recency_adjusted_bullet_cap(8, 1, total_jobs=6, max_pages=2) == 8


def test_recency_adjusted_bullet_cap_never_below_two_bullet_floor_at_any_page_budget():
    for max_pages in (2, 3, 4, 5):
        assert _recency_adjusted_bullet_cap(8, 19, total_jobs=20, max_pages=max_pages) >= 2


def test_enhance_experience_defaults_max_pages_from_its_own_experience_list():
    # No explicit max_pages - self-computed from `experience`'s own
    # durations via years_of_experience/page_budget_for_years. A single
    # job's own points list is enough to prove the wiring (job_index=0 is
    # always within the grace period regardless of budget, so this checks
    # the SPAN computation feeds through correctly rather than the taper
    # curve itself, which is covered directly above).
    senior_jobs = [
        {"company": "Recent Co", "role": "Software Engineer", "duration": "2023-2024",
         "points": list(_DISTINCT_BULLETS), "is_career_break": False},
        {"company": "First Co", "role": "Software Engineer", "duration": "2000-2001",
         "points": [], "is_career_break": False},
    ]
    # Span: 2024 - 2000 = 24 years -> 5-page budget.
    assert page_budget_for_years(years_of_experience(senior_jobs)) == 5
    result = enhance_experience(senior_jobs)
    assert len(result[0]["points"]) == len(_DISTINCT_BULLETS[:8])
