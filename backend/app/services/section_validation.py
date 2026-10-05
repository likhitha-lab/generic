"""Section Validation - Phase E of the Resume Intelligence Engine redesign.

Directly implements the "Section Validation" requirement: compare Source
Resume -> Canonical Resume -> Entity-Linked Resume -> Optimized/Rendered
Resume, and for every section report how many items were extracted,
rendered, lost, duplicated, and inferred - instead of the previous state,
where the only comparison anywhere was a single log-only heuristic
(extraction_pipeline._warn_if_section_likely_dropped) covering 3 of ~11
sections, with no per-section counts and no comparison AFTER extraction
(i.e. nothing checked whether Entity Linking or the Optimizer themselves
introduced a loss).

Three checkpoints, two comparisons:

    Document Understanding  ->  Entity Linking  ->  Resume Optimizer
       (pre_linking)            (post_linking)         (final_dict)
              \\___________duplicated___________/
                                    \\_____________lost_____________/

  - extracted:  entity count at Document Understanding (pre_linking) - the
                count Stage 1 extraction + Section Recovery produced.
  - duplicated: extracted - post_linking count (floored at 0) - how many
                entities Entity Linking judged to be the same real thing
                and merged away. This should be >0 exactly when duplicates
                were actually found and fixed, not a sign of data loss.
  - rendered:   entity count in the final dict handed to file_generator.py.
  - lost:       post_linking count - rendered count (floored at 0) - a
                POSITIVE value here is a genuine regression signal: some
                entity survived Entity Linking (i.e. wasn't a judged
                duplicate) but still didn't make it to the final render.
                Should be 0 for every section in normal operation.
  - inferred:   True if section_recovery.recover_missing_sections' own
                recovery_log shows this section's content came from the
                retry/inference/deterministic tiers rather than direct
                Stage 1 extraction (reused, not re-derived).

Pure, read-only reporting - never mutates any of its three inputs, and
never itself blocks generation (see identity_validation.py for the actual
hard gate; this module is diagnostic/visibility only, surfaced in the same
quality report as `recovery_log` - see resume_quality_engine.run_quality_engine).
"""
from app.services.canonical_model import CanonicalResume, TEXT_LIST_FIELDS

_EMPLOYMENT_LEGACY_KEY = "experience"
_PROJECT_LEGACY_KEY = "projects"

# canonical field name -> legacy dict key, for every section this report
# covers (Employment/Project plus every TextEntity-shaped list).
_SECTION_LEGACY_KEYS: tuple[tuple[str, str], ...] = (
    ("employments", _EMPLOYMENT_LEGACY_KEY),
    ("projects", _PROJECT_LEGACY_KEY),
) + tuple((canonical_field, legacy_key) for legacy_key, canonical_field in TEXT_LIST_FIELDS)


def count_entities(resume: CanonicalResume) -> dict[str, int]:
    """A plain `{canonical_field: count}` snapshot - deliberately just
    integers, not references to `resume` or its entities. Entity Linking
    mutates individual entity objects (and reassigns the list attributes
    on `resume`) in place; capturing counts as plain ints here, rather than
    holding onto `resume` itself, is what makes it safe to call this both
    before and after link_entities() without the "before" snapshot
    silently changing underneath it."""
    return {canonical_field: len(getattr(resume, canonical_field)) for canonical_field, _legacy_key in _SECTION_LEGACY_KEYS}


def build_section_report(
    pre_linking_counts: dict[str, int],
    post_linking_counts: dict[str, int],
    final_dict: dict,
    recovery_log: dict | None = None,
) -> dict:
    """Returns `{legacy_section_key: {"extracted", "duplicated", "rendered",
    "lost", "inferred"}}` for every section - see module docstring for what
    each field means. `pre_linking_counts`/`post_linking_counts` come from
    count_entities(), called at the two checkpoints in resume_service.py."""
    recovery_log = recovery_log or {}
    report: dict = {}
    for canonical_field, legacy_key in _SECTION_LEGACY_KEYS:
        extracted = pre_linking_counts.get(canonical_field, 0)
        post_link = post_linking_counts.get(canonical_field, 0)
        duplicated = max(extracted - post_link, 0)
        rendered = len(final_dict.get(legacy_key) or [])
        lost = max(post_link - rendered, 0)
        report[legacy_key] = {
            "extracted": extracted,
            "duplicated": duplicated,
            "rendered": rendered,
            "lost": lost,
            "inferred": recovery_log.get(legacy_key) in ("retry", "inference", "deterministic"),
        }
    return report
