"""Evaluation Dashboard - Phase G of the Resume Intelligence Engine redesign.

Computes ONE structured evaluation report per generated resume version -
Name Extraction, Contact Preservation, Summary Completeness, per-section
Preservation %, Duplicate Entity Count, Missing Section Count, ATS Score,
overall Information Preservation %, and the Overall Resume Quality Score -
and is persisted alongside every generated resume's content.json/PDF/DOCX
(see resume_service._persist_version, which uploads it as a sibling JSON
blob using the exact same "storage-only, no DB migration needed" pattern
already established for raw_content.json - no schema change).

This is the PRIMARY regression metric this project's future development
should evaluate against: every one of the specific, confirmed defects this
whole redesign chain fixed (name loss, duplicate experience entries, lost
sections, lost skills/certifications/contact fields) shows up here as a
single number or PASS/FAIL that a future change can be checked against
directly, instead of re-deriving these signals ad hoc each time.

Deliberately a CONSOLIDATION layer, not new analysis - every metric reuses
an already-computed, already-tested signal from earlier phases:
  - Name Extraction / Contact Preservation: identity_validation.py's own
    non-raising checks (the exact same logic the hard gate itself uses -
    one definition, not two that could quietly disagree).
  - Duplicate Entity Count: identity_validation.count_duplicate_entities
    (Entity Linking's own merge predicates, re-checked).
  - Per-section Preservation % / Missing Section Count: section_validation.
    build_section_report's {extracted, rendered, duplicated} counts (Phase
    E), computed once in resume_service.py and passed in here.
  - ATS Score: ats_intelligence.optimize_for_ats's own report score.
  - Overall Resume Quality Score: resume_scoring_engine.score_resume's own
    9-dimension Overall Resume Score (Phase 8) - reused, not reinvented.
  - Summary Completeness: the "summary" dimension already inside that same
    score_resume breakdown, normalized to a 0-100 percentage.
"""
from app.services.identity_validation import check_contact_preserved, check_name_present, count_duplicate_entities
from app.services.resume_scoring_engine import get_persona_weights

# The 5 sections the user's dashboard spec names explicitly, plus every
# other section this pipeline tracks (still included in `section_detail`
# for completeness, just not surfaced as a named top-level field).
_NAMED_PRESERVATION_SECTIONS: tuple[tuple[str, str], ...] = (
    ("skills", "skills_preservation_pct"),
    ("experience", "experience_preservation_pct"),
    ("projects", "projects_preservation_pct"),
    ("certifications", "certifications_preservation_pct"),
    ("education", "education_preservation_pct"),
)


def _preservation_pct(extracted: int, rendered: int) -> float:
    """100.0 when nothing was ever extracted for a section (vacuously
    fully preserved - there was nothing to lose), otherwise
    rendered/extracted as a percentage, never above 100."""
    if extracted <= 0:
        return 100.0
    return round(min(rendered / extracted, 1.0) * 100, 1)


def _summary_completeness_pct(polished: dict, scoring_breakdown: dict) -> float:
    """Reuses resume_scoring_engine's own "summary" dimension (presence,
    length, sentence count, all-caps checks - see _score_summary) rather
    than a second, separate summary-quality heuristic; normalized to 0-100
    by dividing by that dimension's max weight under the "general" persona
    (the persona score_resume is always called with here)."""
    max_weight = get_persona_weights("general")["summary"]
    if max_weight <= 0:
        return 0.0
    return round(scoring_breakdown.get("summary", 0) / max_weight * 100, 1)


def build_evaluation_report(
    polished: dict,
    raw_text: str,
    section_report: dict,
    recovery_log: dict | None,
    ats_report: dict,
    scoring_report: dict,
) -> dict:
    """Builds the full evaluation report for one generated resume version.
    Every report parameter is something resume_service.py already computes
    on this exact `polished` dict during the normal quality-gate pass (see
    _check_quality_or_raise) - passed in rather than recomputed here, so
    this module is a pure consolidation layer, never a second source of
    truth for any of these signals.

    `polished` - the final, fully-optimized resume dict (what actually gets
      rendered/persisted).
    `raw_text` - the original source document's extracted text (needed for
      check_contact_preserved's "was it in the source" signal).
    `section_report` - section_validation.build_section_report's output
      (Phase E).
    `recovery_log` - section_recovery.recover_missing_sections's output;
      used only to also count a section "missing" when it was flagged
      present-in-source but never recovered, even if it therefore has 0
      extracted items (so it wouldn't otherwise show up as a loss).
    `ats_report` - ats_intelligence.optimize_for_ats's own report.
    `scoring_report` - resume_scoring_engine.score_resume's own report
      (called with the default "general" persona - see
      _summary_completeness_pct's matching persona lookup).
    """
    recovery_log = recovery_log or {}

    total_extracted = 0
    total_rendered = 0
    missing_section_count = 0
    named_preservation: dict[str, float] = {}
    section_detail: dict[str, dict] = {}

    for legacy_key, stats in section_report.items():
        extracted = stats.get("extracted", 0)
        duplicated = stats.get("duplicated", 0)
        rendered = stats.get("rendered", 0)
        # CONFIRMED false-regression case: comparing `rendered` against the
        # raw pre-Entity-Linking `extracted` count makes a correctly-
        # deduplicated section look like it LOST content - a real 21-year
        # resume's Experience section extracted as 25 raw (duplicate-
        # inflated) entries, correctly collapsed by Entity Linking to 16
        # real distinct jobs with zero content lost, scored a misleading
        # 64% (16/25) here before this fix. The true "how many real things
        # existed" baseline is extracted MINUS duplicated (i.e. the post-
        # Entity-Linking count) - preservation is then rendered against
        # THAT, so a fully-successful merge always reads as 100%, never as
        # a loss.
        true_baseline = max(extracted - duplicated, 0)
        pct = _preservation_pct(true_baseline, rendered)

        total_extracted += true_baseline
        total_rendered += rendered
        if recovery_log.get(legacy_key) == "unrecovered" or (extracted > 0 and rendered == 0):
            missing_section_count += 1

        section_detail[legacy_key] = {**stats, "preservation_pct": pct}
        for section_key, field_name in _NAMED_PRESERVATION_SECTIONS:
            if legacy_key == section_key:
                named_preservation[field_name] = pct

    report = {
        "name_extraction": "PASS" if check_name_present(polished) else "FAIL",
        "contact_preservation": "PASS" if check_contact_preserved(polished, raw_text) else "FAIL",
        "summary_completeness_pct": _summary_completeness_pct(polished, scoring_report["breakdown"]),
        # Duplicates REMAINING in the final generated resume - i.e. a
        # safety-net re-check on `polished`, not "how many Entity Linking
        # merged away" (that's a good-news count, already visible per-
        # section in section_detail[...]["duplicated"], and mixing it in
        # here would make a resume that HAD duplicates but got them fully
        # cleaned up score worse than one that never had any - backwards
        # for a regression metric where 0 must always mean "healthy").
        "duplicate_entity_count": count_duplicate_entities(polished),
        "missing_section_count": missing_section_count,
        "ats_score": ats_report.get("score", 0),
        "information_preservation_pct": _preservation_pct(total_extracted, total_rendered),
        "overall_resume_quality_score": scoring_report["score"],
        "section_detail": section_detail,
    }
    for _section_key, field_name in _NAMED_PRESERVATION_SECTIONS:
        report[field_name] = named_preservation.get(field_name, 100.0)
    return report
