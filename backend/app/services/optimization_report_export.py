"""Optimization Report Export - Phase 9d (Enterprise features).

Builds an exportable optimization report combining resume_scoring_engine
(Phase 8), resume_comparison + optimization_explainer (Phase 9a/9b), and
optionally a job_matcher (Phase 9c) result into one JSON-serializable
document, plus a plain-text rendering of the same content.

Deliberately does NOT produce a PDF - that would mean extending
file_generator.py (the Rendering Engine), which this feature doesn't need
to touch: a JSON/plain-text export is a complete, useful "download your
optimization report" deliverable on its own, and keeps this feature fully
decoupled from the DOCX/PDF rendering pipeline. Pure: never mutates any
resume dict it's given.
"""
import datetime

from app.services.resume_scoring_engine import score_resume


def build_optimization_report(
    parsed: dict,
    before: dict | None = None,
    job_match: dict | None = None,
    persona: str = "general",
) -> dict:
    """Assembles the full exportable report. `before` (a prior version's
    JSON, e.g. Stage 1's raw extraction or an earlier ResumeVersion) is
    optional - when given, includes a before/after comparison and
    optimization explanation; `job_match` (job_matcher.match_resume_to_job's
    own return value) is optional and included as-is when given. Every
    piece here is computed from already-produced data - this function adds
    no new analysis of its own beyond assembling the report shape.
    """
    from app.services.optimization_explainer import explain_optimizations
    from app.services.resume_comparison import compare_resumes

    report: dict = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "persona": persona,
        "scoring": score_resume(parsed, persona=persona),
    }
    if before is not None:
        report["comparison"] = compare_resumes(before, parsed)
        report["explanations"] = explain_optimizations(before, parsed)
    if job_match is not None:
        report["job_match"] = job_match
    return report


def _format_list(items: list[str], indent: str = "  ") -> str:
    return "\n".join(f"{indent}- {item}" for item in items) if items else f"{indent}(none)"


def render_report_as_text(report: dict) -> str:
    """Plain-text rendering of build_optimization_report's output, suitable
    for a downloadable .txt export. Pure formatting only - adds no new
    analysis."""
    lines = ["RESUME OPTIMIZATION REPORT", f"Generated: {report.get('generated_at', '')}",
              f"Persona: {report.get('persona', 'general')}", ""]

    scoring = report.get("scoring") or {}
    lines.append(f"OVERALL SCORE: {scoring.get('score', 'N/A')}/100")
    lines.append("Breakdown:")
    for dimension, value in (scoring.get("breakdown") or {}).items():
        lines.append(f"  - {dimension.replace('_', ' ').title()}: {value}")
    lines.append("")
    lines.append("Warnings:")
    lines.append(_format_list(scoring.get("warnings") or []))
    lines.append("")
    lines.append("Suggestions:")
    lines.append(_format_list(scoring.get("suggestions") or []))

    if "comparison" in report:
        lines.append("")
        lines.append("BEFORE vs AFTER COMPARISON")
        scores = report["comparison"].get("scores") or {}
        lines.append(
            f"  Score change: {scores.get('before_score', 'N/A')} -> {scores.get('after_score', 'N/A')} "
            f"({'+' if (scores.get('delta') or 0) >= 0 else ''}{scores.get('delta', 0)})"
        )

    if "explanations" in report:
        lines.append("")
        lines.append("WHAT CHANGED AND WHY")
        lines.append(_format_list(report["explanations"].get("all") or []))

    if "job_match" in report:
        lines.append("")
        lines.append("JOB DESCRIPTION MATCH")
        job_match = report["job_match"]
        lines.append(f"  Match score: {job_match.get('match_score', 'N/A')}%")
        lines.append("  Matched keywords:")
        lines.append(_format_list(job_match.get("matched_keywords") or [], indent="    "))
        lines.append("  Missing keywords (add ONLY if genuinely applicable):")
        lines.append(_format_list(job_match.get("missing_keywords") or [], indent="    "))

    return "\n".join(lines)
