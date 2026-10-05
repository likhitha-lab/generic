"""Optimization Explainer - Phase 9b (Enterprise features).

Generates a plain-English explanation of every optimization applied
between a "before" and "after" resume snapshot (see resume_comparison.py
for the underlying diff). Pure analysis - never modifies either input.

WHY DETERMINISTIC, NOT A NEW GEMINI CALL: "AI explanation of every
optimization" reads as a request for LLM-generated prose, but every prior
phase of this engine (2, 3, 5, 6, 7) chose a deterministic alternative
specifically to avoid non-determinism, cost, and latency on a step that
runs on every single resume - and specifically because the underlying
diff (resume_comparison.py) is already fully known and structured, a
template-based explanation is MORE reliable here than an LLM call that
could hallucinate a reason the change wasn't actually made for. Every
explanation below is grounded directly in a concrete, computed diff value
- never invented commentary.
"""
from app.services.resume_comparison import compare_resumes


def _explain_skills(diff: dict) -> list[str]:
    explanations = []
    if diff["added"]:
        explanations.append(
            f"Added {len(diff['added'])} technology keyword(s) to Technical Skills "
            f"({', '.join(diff['added'][:5])}{'...' if len(diff['added']) > 5 else ''}) - "
            "surfaced from experience/project text or normalized from a spelling/alias variant."
        )
    if diff["removed"]:
        explanations.append(
            f"Removed {len(diff['removed'])} skill(s) that were duplicates, aliases of an "
            "already-listed technology, or not recognized as a genuine technical skill."
        )
    return explanations


def _explain_summary(diff: dict) -> list[str]:
    if diff["rewritten"]:
        direction = "expanded" if diff["after_length"] > diff["before_length"] else "condensed"
        return [
            f"Professional Summary was rewritten into a recruiter-quality version and {direction} "
            f"({diff['before_length']} -> {diff['after_length']} characters), synthesized from the "
            "candidate's actual work history and skills rather than copied from the original."
        ]
    return []


def _explain_experience(diff: dict) -> list[str]:
    explanations = []
    for job in diff["jobs"]:
        if job["bullets_compressed"] > 0:
            explanations.append(
                f"{job.get('company') or '(no company)'}: compressed {job['bullets_before']} raw bullet(s) "
                f"down to {job['bullets_after']} - merged duplicate/repetitive responsibilities while "
                "preserving every achievement, metric, and technology mentioned."
            )
        elif job["bullets_before"] == 0 and job["bullets_after"] > 0:
            explanations.append(f"{job.get('company') or '(no company)'}: added {job['bullets_after']} bullet(s).")
    return explanations


def _explain_projects(diff: dict) -> list[str]:
    explanations = []
    if diff["removed"]:
        explanations.append(f"Merged {len(diff['removed'])} redundant/duplicate project(s) with near-identical content.")
    return explanations


def _explain_scores(diff: dict) -> list[str]:
    if diff["delta"] == 0:
        return []
    direction = "improved" if diff["delta"] > 0 else "decreased"
    explanation = f"Overall Resume Score {direction} by {abs(diff['delta'])} point(s) ({diff['before_score']} -> {diff['after_score']})."
    dimension_notes = []
    for dimension, after_value in diff["after_breakdown"].items():
        before_value = diff["before_breakdown"].get(dimension, after_value)
        if after_value > before_value:
            dimension_notes.append(f"{dimension.replace('_', ' ')} improved")
    if dimension_notes:
        explanation += " Key drivers: " + ", ".join(dimension_notes[:3]) + "."
    return [explanation]


def explain_optimizations(before: dict, after: dict) -> dict:
    """Public entry point. Returns {"summary": [...], "skills": [...],
    "experience": [...], "projects": [...], "scores": [...]} - each a list
    of plain-English, diff-grounded explanation strings. Pure: never
    mutates `before` or `after`.
    """
    diff = compare_resumes(before, after)
    explanations = {
        "summary": _explain_summary(diff["summary"]),
        "skills": _explain_skills(diff["skills"]),
        "experience": _explain_experience(diff["experience"]),
        "projects": _explain_projects(diff["projects"]),
        "scores": _explain_scores(diff["scores"]),
    }
    explanations["all"] = [item for group in explanations.values() for item in group]
    return explanations
