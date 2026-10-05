"""Resume Comparison Engine - Phase 9a (Enterprise features).

Before-vs-After comparison between any two versions of a resume's
optimized JSON (typically Stage 1's raw extraction vs. the fully-
optimized Phase 1-8 result, or any two ResumeVersion snapshots). Pure
analysis - never modifies either input dict.

Deliberately field-by-field and count-based rather than a generic deep-
diff library: a resume's "before"/"after" comparison is only meaningful in
terms a candidate/recruiter cares about (skills added/removed, bullets
rewritten, summary length change, score delta), not a raw JSON diff that
would also flag irrelevant things like dict key order.
"""
import difflib
import re

from app.services.resume_scoring_engine import score_resume


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _set_diff(before: list[str], after: list[str]) -> dict:
    before_norm = {_normalize_text(item): item for item in before}
    after_norm = {_normalize_text(item): item for item in after}
    added = [after_norm[key] for key in after_norm if key not in before_norm]
    removed = [before_norm[key] for key in before_norm if key not in after_norm]
    return {"added": added, "removed": removed, "unchanged_count": len(set(before_norm) & set(after_norm))}


def compare_skills(before: dict, after: dict) -> dict:
    return _set_diff(before.get("skills") or [], after.get("skills") or [])


def compare_tools(before: dict, after: dict) -> dict:
    return _set_diff(before.get("tools") or [], after.get("tools") or [])


def compare_summary(before: dict, after: dict) -> dict:
    before_summary = str(before.get("summary") or "")
    after_summary = str(after.get("summary") or "")
    similarity = difflib.SequenceMatcher(None, before_summary.lower(), after_summary.lower()).ratio()
    return {
        "before_length": len(before_summary),
        "after_length": len(after_summary),
        "rewritten": similarity < 0.6,
        "similarity": round(similarity, 2),
    }


def _job_key(exp: dict) -> tuple:
    return (
        _normalize_text(exp.get("company") or ""),
        _normalize_text(exp.get("role") or ""),
        _normalize_text(exp.get("duration") or ""),
    )


def compare_experience(before: dict, after: dict) -> dict:
    """Matches jobs across before/after by (company, role, duration) -
    resume_optimizer.py/experience_intelligence.py never add/remove/
    reorder JOBS (only their bullets), so this key is stable across
    optimization passes. Reports, per matched job, how many bullets were
    added/removed/compressed."""
    before_jobs = {_job_key(exp): exp for exp in (before.get("experience") or [])}
    after_jobs = {_job_key(exp): exp for exp in (after.get("experience") or [])}

    jobs_report = []
    for key, after_exp in after_jobs.items():
        before_exp = before_jobs.get(key)
        before_points = before_exp.get("points") or [] if before_exp else []
        after_points = after_exp.get("points") or []
        jobs_report.append({
            "company": after_exp.get("company"),
            "role": after_exp.get("role"),
            "bullets_before": len(before_points),
            "bullets_after": len(after_points),
            "bullets_compressed": len(before_points) - len(after_points) if before_exp else 0,
        })

    return {
        "jobs_before": len(before_jobs),
        "jobs_after": len(after_jobs),
        "jobs": jobs_report,
    }


def compare_projects(before: dict, after: dict) -> dict:
    before_titles = {_normalize_text(p.get("title") or "") for p in (before.get("projects") or [])}
    after_titles = {_normalize_text(p.get("title") or "") for p in (after.get("projects") or [])}
    return {
        "projects_before": len(before.get("projects") or []),
        "projects_after": len(after.get("projects") or []),
        "added": list(after_titles - before_titles),
        "removed": list(before_titles - after_titles),
    }


def compare_scores(before: dict, after: dict) -> dict:
    """Compares resume_scoring_engine.py's Overall Resume Score between
    the two snapshots - both calls are pure/read-only (Phase 8), so this
    never mutates either `before` or `after`."""
    before_report = score_resume(before)
    after_report = score_resume(after)
    return {
        "before_score": before_report["score"],
        "after_score": after_report["score"],
        "delta": after_report["score"] - before_report["score"],
        "before_breakdown": before_report["breakdown"],
        "after_breakdown": after_report["breakdown"],
    }


def compare_resumes(before: dict, after: dict) -> dict:
    """Public entry point - the full Before vs After comparison report.
    Pure: never mutates `before` or `after`.
    """
    return {
        "skills": compare_skills(before, after),
        "tools": compare_tools(before, after),
        "summary": compare_summary(before, after),
        "experience": compare_experience(before, after),
        "projects": compare_projects(before, after),
        "scores": compare_scores(before, after),
    }
