"""Entity Linking - Phase C of the Resume Intelligence Engine redesign, and
the direct fix for the confirmed real-world defect this whole redesign was
triggered by: a real candidate's resume (21 years of experience - long
enough to trigger extraction_pipeline.py's split-on-truncation retry, which
splits the source text into 2 OVERLAPPING halves and recurses) produced
between 16 and 40 "Professional Experience" entries across repeated runs of
the SAME source document, for a career that is actually about 10 real jobs.
Concretely: "Schlumberger" appeared as 2 separate entries, "CMA CGM SYSTEMS
(an IBM subsidiary)" as 4, "Punyam Management Services" as 2, "Spry
Resources" as 2, "Applitech Solution Limited" as 2 - same real jobs, each
phrased slightly differently by Gemini because it was asked about the same
overlapping text twice.

Root cause this fixes: no stage in the existing pipeline ever compared two
list ENTRIES to ask "are these the same real employer/project/credential,
described two different ways?" - every existing dedup pass
(normalization.enforce_limits, resume_optimizer._validate_and_dedupe,
resume_validator._check_and_clean_experience) only collapses EXACT-match
duplicates (identical company+role+duration+points strings), which never
fires when the same real job is reworded even slightly between two
overlapping extraction chunks.

Deterministic only - no embeddings, no extra Gemini calls (per the explicit
"deterministic Resume Intelligence Engine" requirement). Similarity is
`difflib.SequenceMatcher`-based, the same conservative, log-every-merge
approach this codebase already uses for skill-name near-duplicates
(normalization._fuzzy_merge, skill_intelligence._fuzzy_merge) - extended
here to employer/project/certification/achievement/career-break identity,
which had no equivalent before.

Every merge records `merged_from` on the surviving entity (the ids of every
entity folded into it) - auditable, never silent, and directly testable
(see tests/test_entity_linking.py).

Skills/Tools are intentionally NOT touched here - skill_intelligence.py and
tool_classifier.py already run their own alias/fuzzy dedup later in
resume_optimizer.py, and duplicating that logic here would risk the two
disagreeing. Entity Linking's job is exactly the entities that had NO
existing dedup at all: Employment, Project, Certification, Achievement,
Leadership, Publication, Language, Volunteer, Education, and (with extra
care - see _employments_are_same_career_break) Career Breaks.
"""
import datetime
import difflib
import logging
import re

from app.services.canonical_model import CanonicalResume, Employment, Project, TextEntity
from app.services.experience_intelligence import merge_near_duplicate_bullets

logger = logging.getLogger(__name__)

_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)
_YEAR_4_RE = re.compile(r"(?:19|20)\d{2}")
# A 2-digit year immediately after a hyphen, or after a 3+ letter month
# abbreviation (with a space or hyphen) - covers "May-10", "Aug-09",
# "Mar-98", "April 10" without also matching an unrelated bare 2-digit
# number elsewhere in the string.
_YEAR_2_RE = re.compile(r"(?:[A-Za-z]{3,}[\s\-]|-)(\d{2})\b")

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(ltd\.?|limited|inc\.?|incorporated|pvt\.?|private|llc|llp|corporation|corp\.?|co\.?)\b",
    re.IGNORECASE,
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _text_similarity(a: str, b: str) -> float:
    a_norm, b_norm = _normalize_text(a), _normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers_conflict(a: str, b: str) -> bool:
    """CONFIRMED false-merge guard: a short, templated phrase differing
    only in an embedded number ("Led a 5-person feature team" vs "Led a
    8-person feature team", "Employee of the Month, March 2023" vs "...,
    June 2021") scores DECEPTIVELY HIGH on raw text similarity (0.96+ for
    the team-size example) because the shared template text dominates the
    ratio - high enough to defeat even a conservative 0.95 threshold. Two
    numbers embedded in otherwise-similar text are strong, specific
    evidence of two different real facts (a different team size, a
    different year, a different percentage) - never the same fact
    reworded, which is exactly what similarity-based merging is supposed
    to catch. Returns True (never merge) only when BOTH strings contain at
    least one number AND those number sets differ; a missing number on
    either side is not evidence either way, so it defers to the normal
    similarity check instead of blocking it."""
    numbers_a = set(_NUMBER_RE.findall(a))
    numbers_b = set(_NUMBER_RE.findall(b))
    if not numbers_a or not numbers_b:
        return False
    return numbers_a != numbers_b


def _extract_years(duration: str) -> list[int]:
    """Every year mentioned in a free-text duration string, handling both
    4-digit years and the 2-digit month-abbreviated form real resumes
    commonly use ("Mar-98", "Aug-09"). A 2-digit year >= 50 is read as
    19xx, otherwise 20xx (e.g. "98" -> 1998, "13" -> 2013) - correct for
    any career spanning 1950-2049, comfortably covering a real resume."""
    years = [int(y) for y in _YEAR_4_RE.findall(duration)]
    for match in _YEAR_2_RE.finditer(duration):
        yy = int(match.group(1))
        years.append(1900 + yy if yy >= 50 else 2000 + yy)
    if _PRESENT_RE.search(duration):
        years.append(datetime.date.today().year)
    return years


def _durations_compatible(a: str, b: str) -> bool:
    """PERMISSIVE compatibility check used for general Employment/Project
    matching - True for identical text, either side blank (can't disprove
    a match from missing data), unparseable dates, or overlapping parsed
    year ranges. False only for two dates that clearly, parseably,
    describe non-overlapping periods."""
    if _normalize_text(a) == _normalize_text(b):
        return True
    if not a.strip() or not b.strip():
        return True
    years_a, years_b = _extract_years(a), _extract_years(b)
    if not years_a or not years_b:
        return True
    return max(min(years_a), min(years_b)) <= min(max(years_a), max(years_b))


def _career_breaks_match(a: str, b: str) -> bool:
    """STRICT equality check used ONLY for Career Break vs Career Break
    comparison - deliberately NOT the same permissive overlap check
    _durations_compatible uses. A real candidate can have several genuinely
    different career breaks at different points in a long career (a
    confirmed real case: one candidate's resume has 4 distinct breaks
    spanning different years) - two breaks whose ranges merely TOUCH at a
    shared boundary year (one ending "March 1991", the next starting "July
    1991") must NOT collapse into one; only an exact date-range match (or
    identical duration text) counts as the same break."""
    if _normalize_text(a) == _normalize_text(b):
        return True
    years_a, years_b = _extract_years(a), _extract_years(b)
    if not years_a or not years_b:
        return False
    return (min(years_a), max(years_a)) == (min(years_b), max(years_b))


def _company_core(name: str) -> str:
    """The comparable "core" of a company name - text before the first
    comma or opening parenthesis (strips a trailing location/descriptor
    clause like ", Dubai" or "(an IBM subsidiary)"), with common legal-
    entity suffixes removed (so "Ltd." and "Limited" compare equal) and
    normalized for case/whitespace."""
    core = re.split(r"[,(]", name, maxsplit=1)[0]
    core = _LEGAL_SUFFIX_RE.sub("", core)
    return _normalize_text(core)


def _companies_match(a: str, b: str) -> bool:
    core_a, core_b = _company_core(a), _company_core(b)
    if not core_a or not core_b:
        return False
    if core_a == core_b:
        return True
    shorter, longer = sorted([core_a, core_b], key=len)
    if shorter and shorter in longer:
        return True
    return difflib.SequenceMatcher(None, core_a, core_b).ratio() >= 0.85


def _bullets_overlap_ratio(points_a: list[str], points_b: list[str]) -> float:
    """Fraction of `points_a` that have a near-duplicate counterpart
    somewhere in `points_b` (similarity >= 0.6) - a corroborating identity
    signal for two Employment entries whose company matches but whose
    role/duration text differs (see should_merge_employment): if most of
    what one entry says is also said, just reworded, in the other, they're
    almost certainly the same real job restated, not two different ones."""
    if not points_a or not points_b:
        return 0.0
    matched = 0
    for point in points_a:
        best = max((_text_similarity(point, other) for other in points_b), default=0.0)
        if best >= 0.6:
            matched += 1
    return matched / len(points_a)


def should_merge_employment(a: Employment, b: Employment) -> bool:
    if a.is_career_break or b.is_career_break:
        # Career breaks are never merged with a real job, and merge with
        # EACH OTHER only under the stricter _career_breaks_match rule.
        if a.is_career_break != b.is_career_break:
            return False
        return _career_breaks_match(a.duration, b.duration)

    if not _companies_match(a.company, b.company):
        return False

    role_compatible = (not a.role.strip() or not b.role.strip()) or _text_similarity(a.role, b.role) >= 0.55
    duration_compatible = _durations_compatible(a.duration, b.duration)
    if role_compatible and duration_compatible:
        return True

    # Corroborating signal: heavy bullet-content overlap despite a role/
    # duration mismatch - catches e.g. the same Schlumberger tenure listed
    # once as "Enterprise IT Architect" and again as "Lead Architect" with
    # near-identical bullet content describing the same work.
    overlap = max(_bullets_overlap_ratio(a.points, b.points), _bullets_overlap_ratio(b.points, a.points))
    return overlap >= 0.5


def _merge_employment(primary: Employment, duplicate: Employment) -> Employment:
    """Folds `duplicate` into `primary` (primary survives, its id is kept).
    Never overwrites a populated field with a blank one; bullets are the
    UNION of both (deduped, including near-duplicate-aware merging via the
    existing experience_intelligence.merge_near_duplicate_bullets, since
    two merged entries commonly restate the same bullet slightly
    differently)."""
    primary.company = primary.company.strip() or duplicate.company
    primary.role = primary.role.strip() or duplicate.role
    primary.duration = primary.duration.strip() or duplicate.duration
    primary.reason_for_leaving = primary.reason_for_leaving.strip() or duplicate.reason_for_leaving
    primary.notes = primary.notes.strip() or duplicate.notes
    primary.break_detail = primary.break_detail.strip() or duplicate.break_detail
    primary.is_career_break = primary.is_career_break or duplicate.is_career_break
    combined_points = list(primary.points) + [p for p in duplicate.points if p not in primary.points]
    primary.points = merge_near_duplicate_bullets(combined_points)
    primary.merged_from = primary.merged_from + [duplicate.id] + duplicate.merged_from
    return primary


def link_employments(employments: list[Employment]) -> list[Employment]:
    """Greedy clustering (same "compare against what's already kept"
    approach this codebase already uses for skill near-duplicate merging -
    see normalization._fuzzy_merge/skill_intelligence._fuzzy_merge):
    O(n^2), which is fine since a resume has at most a few dozen jobs."""
    kept: list[Employment] = []
    for entry in employments:
        merged_into = None
        for existing in kept:
            if should_merge_employment(existing, entry):
                merged_into = existing
                break
        if merged_into:
            logger.info(
                "Entity Linking: merged employment %r (%s, %s) into %r (%s, %s).",
                entry.company, entry.role, entry.duration,
                merged_into.company, merged_into.role, merged_into.duration,
            )
            _merge_employment(merged_into, entry)
        else:
            kept.append(entry)
    return kept


def should_merge_project(a: Project, b: Project) -> bool:
    if not a.title.strip() or not b.title.strip():
        return False
    # e.g. "Annual Hackathon 2022" vs "Annual Hackathon 2023" - two
    # genuinely different projects sharing a template name - see
    # _numbers_conflict's docstring.
    if _numbers_conflict(a.title, b.title):
        return False
    title_similarity = _text_similarity(a.title, b.title)
    if title_similarity >= 0.85:
        return True
    if a.description.strip() and b.description.strip():
        return title_similarity >= 0.5 and _text_similarity(a.description, b.description) >= 0.7
    return False


def _merge_project(primary: Project, duplicate: Project) -> Project:
    primary.title = primary.title.strip() or duplicate.title
    primary.role = primary.role.strip() or duplicate.role
    primary.client = primary.client.strip() or duplicate.client
    primary.description = primary.description.strip() or duplicate.description
    primary.technologies = primary.technologies.strip() or duplicate.technologies
    combined = list(primary.responsibilities) + [r for r in duplicate.responsibilities if r not in primary.responsibilities]
    primary.responsibilities = merge_near_duplicate_bullets(combined)
    primary.merged_from = primary.merged_from + [duplicate.id] + duplicate.merged_from
    return primary


def link_projects(projects: list[Project]) -> list[Project]:
    kept: list[Project] = []
    for entry in projects:
        merged_into = None
        for existing in kept:
            if should_merge_project(existing, entry):
                merged_into = existing
                break
        if merged_into:
            logger.info("Entity Linking: merged project %r into %r.", entry.title, merged_into.title)
            _merge_project(merged_into, entry)
        else:
            kept.append(entry)
    return kept


def link_text_entities(entities: list[TextEntity], threshold: float = 0.85) -> list[TextEntity]:
    """Generic near-duplicate merge for the simple, label-shaped entities
    (Achievements, Leadership, Publications, Languages, Volunteer,
    Education) that had no fuzzy dedup at all before this module - only
    exact-match dedup elsewhere in the pipeline. NOT used for
    Certifications (see link_certifications - a higher threshold is needed
    there since two DIFFERENT certification levels of the same credential,
    e.g. "...- Associate" vs "...- Professional", can score deceptively
    high on raw text similarity, and merging those would be an
    information-loss bug, not a cleanup - the exact same reasoning
    normalization.normalize_certifications already documents for why ITS
    fuzzy matching is off by default). Also guarded by _numbers_conflict -
    see that function's docstring for the confirmed real false-merge case
    (two Education entries for different school levels/years) this closes."""
    kept: list[TextEntity] = []
    for entity in entities:
        merged_into = None
        for existing in kept:
            if _numbers_conflict(existing.value, entity.value):
                continue
            if _text_similarity(existing.value, entity.value) >= threshold:
                merged_into = existing
                break
        if merged_into:
            logger.info("Entity Linking: merged %r into %r (threshold=%.2f).", entity.value, merged_into.value, threshold)
            merged_into.merged_from = merged_into.merged_from + [entity.id] + entity.merged_from
        else:
            kept.append(entity)
    return kept


def link_certifications(certifications: list[TextEntity]) -> list[TextEntity]:
    """Certifications get a much higher similarity threshold than the
    generic text-entity merge (see link_text_entities's docstring for why -
    different certification LEVELS of the same credential must never
    collapse into one)."""
    return link_text_entities(certifications, threshold=0.95)


def link_education(education: list[TextEntity]) -> list[TextEntity]:
    """Education gets the same high threshold as certifications, for the
    same underlying reason - CONFIRMED false-merge case: "12th, Green
    Valley Public School, 75.4% (2014)" and "10th, Green Valley Public
    School, 85% (2012)" share the institution name and overall sentence
    shape, scoring 0.91 raw text similarity despite being two genuinely
    different qualifications (different level, different year, different
    percentage) - the 0.9 threshold the generic link_text_entities default
    uses collapsed these into one, silently losing a real qualification.
    0.95 keeps them distinct while still catching an exact/near-exact
    duplicate (e.g. the same degree line duplicated verbatim by a Stage 1
    split-retry)."""
    return link_text_entities(education, threshold=0.95)


def drop_achievements_duplicated_in_employments(
    achievements: list[TextEntity], employments: list[Employment]
) -> list[TextEntity]:
    """An achievement that's just a near-verbatim restatement of an
    existing Experience bullet point is dropped from Achievements (it's
    already represented once, in Experience) rather than kept as a second,
    duplicated copy - directly implements "Achievements are not duplicated
    from experience.\""""
    all_points = [point for emp in employments for point in emp.points]
    if not all_points:
        return list(achievements)
    kept: list[TextEntity] = []
    for achievement in achievements:
        best = max((_text_similarity(achievement.value, point) for point in all_points), default=0.0)
        if best >= 0.75:
            logger.info(
                "Entity Linking: dropped achievement %r - already stated as an Experience bullet (similarity=%.2f).",
                achievement.value, best,
            )
            continue
        kept.append(achievement)
    return kept


def link_entities(resume: CanonicalResume) -> CanonicalResume:
    """Public entry point - Phase C. Runs every entity-type-specific
    linking pass over `resume` IN PLACE (mutates and returns the same
    object) and returns it. Skills/Tools are deliberately untouched (see
    module docstring)."""
    resume.employments = link_employments(resume.employments)
    resume.projects = link_projects(resume.projects)
    resume.certifications = link_certifications(resume.certifications)
    resume.achievements = link_text_entities(resume.achievements)
    resume.achievements = drop_achievements_duplicated_in_employments(resume.achievements, resume.employments)
    resume.leadership = link_text_entities(resume.leadership)
    resume.publications = link_text_entities(resume.publications)
    resume.languages = link_text_entities(resume.languages)
    resume.volunteer = link_text_entities(resume.volunteer)
    resume.education = link_education(resume.education)
    return resume
