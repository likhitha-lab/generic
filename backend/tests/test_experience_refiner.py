"""Unit tests for app/services/experience_refiner.py - role-tier
classification, bullet-range targets, dedup, and degrade-on-failure
behavior for per-job bullet refinement."""
import app.services.experience_refiner as refiner
from app.services.gemini_client import GeminiInvalidResponseError


# --- classify_role_tier / bullet_range_for_role -----------------------------

def test_classify_role_tier_standard_for_normal_engineering_roles():
    for role in ["Software Engineer", "Senior Software Engineer", "Junior Developer",
                 "Business Analyst", "Registered Nurse", "", None]:
        assert refiner.classify_role_tier(role) == "standard", role


def test_classify_role_tier_lead_for_architects_leads_principals():
    for role in ["Cloud Architect", "Lead Cloud Architect", "Principal Engineer",
                 "Staff Engineer", "Technical Lead", "Engineering Manager"]:
        assert refiner.classify_role_tier(role) == "lead", role


def test_classify_role_tier_executive_for_director_vp_chief_titles():
    for role in ["Director of Engineering", "VP of Engineering", "Head of Platform",
                 "Chief Technology Officer", "President"]:
        assert refiner.classify_role_tier(role) == "executive", role


def test_bullet_range_stays_within_5_to_12_for_every_tier():
    for role in ["Junior Developer", "Software Engineer", "Principal Architect", "VP of Engineering"]:
        lo, hi = refiner.bullet_range_for_role(role)
        assert 5 <= lo <= hi <= 12, (role, lo, hi)


def test_bullet_range_tiers_by_role():
    assert refiner.bullet_range_for_role("Software Engineer") == (5, 8)
    assert refiner.bullet_range_for_role("Principal Architect") == (8, 10)
    assert refiner.bullet_range_for_role("VP of Engineering") == (10, 12)


# --- _dedupe_preserve_order --------------------------------------------------

def test_dedupe_preserve_order_removes_exact_duplicates():
    assert refiner._dedupe_preserve_order(["A", "B", "A"]) == ["A", "B"]


def test_dedupe_preserve_order_never_merges_different_bullets():
    result = refiner._dedupe_preserve_order(["Led team of 5 engineers", "Led team of 12 engineers"])
    assert len(result) == 2


# --- refine_experience_bullets / refine_one_job / refine_experience --------

def test_refine_experience_bullets_skips_gemini_for_small_job(monkeypatch):
    calls = []
    monkeypatch.setattr(
        refiner, "call_gemini", lambda prompt: calls.append(prompt) or {"points": ["should not be used"]}
    )
    result = refiner.refine_experience_bullets("Acme", "Engineer", ["Only one bullet"])
    assert result == ["Only one bullet"]
    assert calls == []  # too few bullets to be worth a Gemini call


def test_refine_experience_bullets_degrades_on_gemini_failure(monkeypatch):
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(refiner, "call_gemini", boom)
    points = ["A", "B", "C", "D", "E"]
    result = refiner.refine_experience_bullets("Acme", "Engineer", points)
    assert result == points


def test_refine_experience_bullets_never_invents_content(monkeypatch):
    # Simulated Gemini response echoing back a plausible consolidation -
    # every returned bullet must be traceable to something in the raw input,
    # not a fabricated addition. We can't verify semantic grounding
    # automatically, but we CAN verify the function returns exactly what
    # Gemini provided (no silent padding/invention added by this module
    # itself).
    monkeypatch.setattr(refiner, "call_gemini", lambda prompt: {"points": ["Merged bullet one.", "Merged bullet two."]})
    result = refiner.refine_experience_bullets(
        "Acme", "Senior Engineer", ["Did A", "Did A again", "Did B", "Did B again", "Did C"]
    )
    assert result == ["Merged bullet one.", "Merged bullet two."]


def test_refine_one_job_leaves_career_break_untouched(monkeypatch):
    monkeypatch.setattr(refiner, "call_gemini", lambda prompt: {"points": ["should not be called"]})
    exp = {"company": "Career Break", "is_career_break": True, "points": ["Took time off", "Traveled"]}
    result = refiner.refine_one_job(exp)
    assert result == exp


def test_refine_one_job_preserves_non_point_fields(monkeypatch):
    monkeypatch.setattr(refiner, "call_gemini", lambda prompt: {"points": ["Refined bullet."]})
    exp = {
        "company": "Acme", "role": "Engineer", "duration": "2020-2024",
        "points": ["A", "B", "C", "D", "E"],
        "reason_for_leaving": "Relocation", "notes": "PF settled", "is_career_break": False, "break_detail": "",
    }
    result = refiner.refine_one_job(exp)
    assert result["company"] == "Acme"
    assert result["duration"] == "2020-2024"
    assert result["reason_for_leaving"] == "Relocation"
    assert result["notes"] == "PF settled"
    assert result["points"] == ["Refined bullet."]


def test_refine_experience_handles_multiple_jobs_and_empty_input(monkeypatch):
    monkeypatch.setattr(refiner, "call_gemini", lambda prompt: {"points": ["Refined."]})
    assert refiner.refine_experience([]) == []

    jobs = [
        {"company": "Acme", "role": "Engineer", "points": ["A", "B", "C", "D", "E"]},
        {"company": "Globex", "role": "Senior Engineer", "points": ["F", "G", "H", "I", "J"]},
    ]
    result = refiner.refine_experience(jobs)
    assert len(result) == 2
    assert {job["company"] for job in result} == {"Acme", "Globex"}
    assert all(job["points"] == ["Refined."] for job in result)


def test_refine_experience_bullets_output_within_20_to_80_input_range(monkeypatch):
    # Simulates the documented worst case: 60 raw, highly repetitive bullets
    # in, consolidated down to something within the 8-12 target band.
    raw_bullets = [f"Did task variant {i}" for i in range(60)]

    def fake_consolidate(prompt):
        # A realistic Gemini response: 10 consolidated, non-repetitive bullets.
        return {"points": [f"Executed a group of related tasks (batch {i})." for i in range(10)]}

    monkeypatch.setattr(refiner, "call_gemini", fake_consolidate)
    result = refiner.refine_experience_bullets("Acme", "Senior Engineer", raw_bullets)
    assert 8 <= len(result) <= 12
