"""Resume Quality & Validation Engine - Phase 5 of the Resume Intelligence
Engine.

The final deterministic gate between Resume Optimization (Phases 1-4:
resume_optimizer.py, skill_intelligence.py, experience_intelligence.py,
resume_structuring.py) and the Resume Builder (file_generator.py) - see
run_quality_engine(), this module's one public entry point, wired into
resume_service.py right before build_resume_file() is called (after
resume_structuring.build_structure_plan(), which only decides LAYOUT, not
content).

COMPOSES rather than duplicates: resume_validator.py's structural clean/
flag pass and resume_quality_checker.py's 9-dimension score are reused
EXACTLY as they already exist (both unmodified - all of their own tests
still hold) - this module adds the 6 checks the checklist below needs that
those two don't cover (Duplicate Technologies via an alias-aware re-check,
Keyword Stuffing, Excessive Skills, Excessive Tools, Missing Business
Impact, Resume Length) and layers a small set of genuinely NEW automatic
fixes on top of whatever resume_validator.py already cleaned:

  - Duplicate Technologies: re-runs `parsed["skills"]` through Phase 2's
    own skill_intelligence.build_technical_skills() again - idempotent on
    already-clean input, but catches anything that reached this point with
    a residual alias/near-duplicate skill_intelligence.py itself would
    have caught the first time (defense in depth, not a second opinion
    that contradicts the first).
  - Repeated wording / repeated sentence openings: re-runs every
    experience job's and project's bullets through Phase 3's own
    experience_intelligence.merge_near_duplicate_bullets/
    diversify_action_verbs again - same idempotent-safety-net reasoning.
  - Weak verbs: a NEW capability (neither prior phase auto-fixes this,
    only detects it) - _fix_weak_verb_opening() substitutes a weak opening
    PHRASE for a comparably-scoped strong verb, e.g. "Worked on backend
    systems." -> "Contributed to backend systems." - a lexical
    substitution only; every noun, technology, and metric in the sentence
    is untouched, so no fact is invented or changed.
  - Redundant projects: resume_validator.py only merges EXACT-title
    duplicates; _merge_near_duplicate_projects() adds a conservative,
    content-similarity-based merge (same high-threshold, log-every-merge
    approach used throughout this pipeline) for two projects that are
    really the same one restated with a slightly different title/wording.
  - Keyword stuffing: DETECTED only (see _detect_keyword_stuffing) - an
    unnaturally long comma-separated run of terms inside one sentence of
    the summary reads as stuffed, but safely REWRITING prose without
    inventing a replacement phrase isn't a mechanical fix this module can
    make without semantic judgment (the same reasoning this whole
    pipeline has applied to every other "needs judgment, not just
    dedup/trim" finding since Phase 1) - flagged as a warning/suggestion,
    never silently rewritten.

Never rewrites a FACT: every automatic fix above either (a) removes an
exact/near-duplicate (the underlying information survives via the kept
copy), or (b) substitutes a weak opening phrase for a strong verb,
changing wording only - never a technology, employer, metric, or claim.
"""
import difflib
import logging
import re

from app.services.experience_intelligence import (
    diversify_action_verbs,
    has_impact_signal,
    classify_project_theme,
    merge_near_duplicate_bullets,
)
from app.services.experience_refiner import bullet_range_for_role
from app.services.resume_quality_checker import check_resume_quality
from app.services.resume_structuring import determine_page_budget
from app.services.resume_validator import validate_and_clean_resume
from app.services.skill_intelligence import MAX_TECHNICAL_SKILLS, build_technical_skills
from app.services.tool_classifier import MAX_TOOLS, cap_tools

logger = logging.getLogger(__name__)

# --- Automatic fixes ----------------------------------------------------------

# Priority 5 fix - root cause: "responsible for"/"handled"/"tasked with"
# previously all mapped to "Managed", which overstates ownership - a
# candidate who was merely "responsible for" a task did not necessarily
# manage anyone or own the outcome, and this substitution rewrote every
# such bullet as if they had. Rewritten conservatively instead: every
# replacement below is a task-scoped, ownership-neutral verb that neither
# overstates nor understates what the original weak phrase actually
# conveyed - the intent, not the scope, is what "conservative" means here.
# "Contributed to"/"Addressed"/"Supported" are also chosen to read
# grammatically whether the original phrase was followed by a gerund
# ("responsible for developing X" -> "Contributed to developing X") or a
# noun phrase ("handled customer escalations" -> "Addressed customer
# escalations"), the same grammatical shape the original text already had.
_WEAK_VERB_REPLACEMENTS: dict[str, str] = {
    "worked on": "Contributed to",
    "responsible for": "Contributed to",
    "handled": "Addressed",
    "helped": "Supported",
    "participated in": "Contributed to",
    "was involved in": "Contributed to",
    "assisted": "Supported",
    "tasked with": "Contributed to",
}


def _fix_weak_verb_opening(bullet: str) -> tuple[str, bool]:
    """Substitutes a weak OPENING phrase for a comparably-scoped strong
    verb - a lexical fix only, never touching the rest of the sentence, so
    no fact/technology/metric is added, removed, or changed. Returns
    (possibly-fixed bullet, whether a fix was applied)."""
    stripped = bullet.strip()
    lowered = stripped.lower()
    for weak_phrase, replacement in _WEAK_VERB_REPLACEMENTS.items():
        if lowered.startswith(weak_phrase):
            return replacement + stripped[len(weak_phrase):], True
    return bullet, False


def _fix_weak_verbs_in_bullets(bullets: list[str]) -> tuple[list[str], int]:
    fixed: list[str] = []
    count = 0
    for bullet in bullets:
        new_bullet, changed = _fix_weak_verb_opening(bullet)
        if changed:
            count += 1
        fixed.append(new_bullet)
    return fixed, count


# --- Repeated-word correction (this round's Priority 2 fix) -----------------
#
# Root cause: an LLM consolidation pass occasionally emits an accidentally
# doubled word - confirmed on a real resume: "Production Production
# support" (adjacent doubling) - and the request's own example, "Delivered
# and delivered", is the same failure mode with a conjunction between the
# two occurrences instead of adjacent. Both are pure text mechanics -
# collapsing an exact repeat to one occurrence changes no fact, technology,
# or metric, so it's safe to auto-fix rather than only log.
_ADJACENT_REPEAT_RE = re.compile(r"\b(\w+)([ \t]+)\1\b", re.IGNORECASE)
_CONJUNCTION_REPEAT_RE = re.compile(r"\b(\w+)\b\s+(?:and|&)\s+\1\b", re.IGNORECASE)


def fix_repeated_words(text: str) -> str:
    """Collapses an accidentally-doubled word - "word word" or "word and
    word" - down to a single occurrence, keeping whichever occurrence's
    exact casing appeared first. A no-op on text with no such repeat."""
    fixed = _CONJUNCTION_REPEAT_RE.sub(r"\1", text)
    fixed = _ADJACENT_REPEAT_RE.sub(r"\1", fixed)
    return fixed


# --- Truncated-sentence correction (this round's Priority 3 fix) ------------
#
# Root cause: an LLM generation occasionally gets cut off mid-word -
# confirmed on a real resume: "...designed solutions, and estimated the
# effo." ("effo" is a cut-off "effort"). Truncation always happens at the
# very END of the generated text (a cutoff can't occur mid-string), which
# is exactly the signal _looks_truncated checks for. Guessing the missing
# letters would be inventing content never actually generated - the ONE
# safe, non-inventing fix is to trim the incomplete trailing clause back
# to the last complete clause boundary (a comma, semicolon, "and", or "&")
# and re-terminate there; if there's no earlier clause boundary at all,
# nothing about the fragment can be salvaged without guessing, so the
# whole bullet is dropped instead of left broken.
_TRUNCATION_ALLOWED_SHORT_WORDS = frozenset((
    "team", "cost", "risk", "data", "code", "plan", "role", "goal", "cloud", "scope",
    "scale", "value", "sales", "sites", "tools", "tests", "users", "hours", "weeks",
    "years", "month", "terms", "needs", "leads", "calls", "tasks", "bugs", "docs",
    "apps", "cases", "specs", "forms", "files", "tiers", "zones", "teams", "goals",
    "plans", "risks", "costs", "gains", "funds", "staff", "tech", "java", "php",
    "sql", "aws", "gcp", "css", "xml", "json", "http", "etc", "inc", "ltd", "hr",
    "app", "web", "api", "sdk", "cli", "ide", "orm", "crud", "flow", "logic", "stack",
    "batch", "cache", "queue", "index", "table", "field", "class", "model", "layer",
    "stage", "phase", "cycle", "sprint", "epic", "story", "board", "chart", "graph",
    "trend", "issue", "topic", "focus", "level", "grade", "score", "rank", "tier",
    "range", "limit", "bound", "delta", "ratio", "sum", "total", "count", "size",
    "type", "kind", "sort", "order", "list", "array", "tree", "node", "edge", "path",
    "link", "url", "uri", "key", "hash", "seed", "token", "flag", "state", "mode",
    "style", "theme", "layout", "grid", "menu", "form", "input", "output", "error",
    "bug", "fix", "patch", "build", "test", "unit", "suite", "case", "spec", "doc",
    "note", "memo", "log", "report", "review", "audit", "check", "scan", "trace",
    "debug", "deploy", "release", "launch", "ship", "merge", "branch", "commit",
    "push", "pull", "sync", "backup", "restore", "migrate", "upgrade", "update",
    "client", "vendor", "budget", "kpi", "roi", "sla", "sme", "poc", "rfp", "erp",
    "crm", "bau", "lead", "leads", "sale", "brand", "asset", "audit", "claim",
))


def _looks_truncated(text: str) -> bool:
    stripped = text.strip()
    if not stripped.endswith("."):
        return False
    words = stripped[:-1].split()
    if not words:
        return False
    last_word = words[-1]
    if not last_word.isalpha() or not last_word.islower():
        return False
    return 2 <= len(last_word) <= 5 and last_word not in _TRUNCATION_ALLOWED_SHORT_WORDS


_CLAUSE_SEPARATOR_RE = re.compile(r",|;|\band\b|&", re.IGNORECASE)


def fix_truncated_sentence(text: str) -> str:
    """Returns `text` unchanged if it doesn't look truncated; the sentence
    trimmed back to its last complete clause if it does and a clause
    boundary exists to trim back to; or "" if nothing is salvageable
    without guessing (signals the caller to drop the bullet entirely)."""
    if not _looks_truncated(text):
        return text
    stripped = text.strip()[:-1]
    words = stripped.split()
    body_before_last_word = " ".join(words[:-1])
    matches = list(_CLAUSE_SEPARATOR_RE.finditer(body_before_last_word))
    if matches:
        cut = matches[-1].start()
        trimmed = body_before_last_word[:cut].strip().rstrip(",;")
        if trimmed:
            return trimmed + "."
    return ""


def _fix_text_mechanics_in_bullets(bullets: list[str]) -> tuple[list[str], int, int]:
    """Applies fix_repeated_words then fix_truncated_sentence to every
    bullet. Returns (fixed_bullets, repeated_word_fixes, truncation_fixes) -
    a bullet that becomes "" (unsalvageable truncation) is dropped from the
    result entirely, never rendered as an empty line."""
    fixed_bullets: list[str] = []
    repeated_fixes = 0
    truncation_fixes = 0
    for bullet in bullets:
        deduped_words = fix_repeated_words(bullet)
        if deduped_words != bullet:
            repeated_fixes += 1
        detruncated = fix_truncated_sentence(deduped_words)
        if detruncated != deduped_words:
            truncation_fixes += 1
        if detruncated:
            fixed_bullets.append(detruncated)
    return fixed_bullets, repeated_fixes, truncation_fixes


_PROJECT_MERGE_THRESHOLD = 0.85


def _merge_near_duplicate_projects(projects: list[dict]) -> tuple[list[dict], int]:
    """Conservative content-similarity merge for two projects that are
    really the same one restated - resume_validator.py's own project dedup
    only catches an EXACT (post-normalization) title match; this catches a
    same-project-different-wording case via the title+description text as
    a whole, same high-threshold/log-every-merge approach used throughout
    this pipeline (skill_intelligence.py, experience_intelligence.py)."""
    kept: list[dict] = []
    removed = 0
    for proj in projects:
        proj_text = f"{proj.get('title', '')} {proj.get('description', '')}".strip().lower()
        merged = False
        for existing in kept:
            existing_text = f"{existing.get('title', '')} {existing.get('description', '')}".strip().lower()
            if not proj_text or not existing_text:
                continue
            ratio = difflib.SequenceMatcher(None, proj_text, existing_text).ratio()
            if ratio >= _PROJECT_MERGE_THRESHOLD:
                merged = True
                removed += 1
                logger.info(
                    "Resume Quality Engine: merged redundant project %r into %r (similarity=%.2f)",
                    proj.get("title"), existing.get("title"), ratio,
                )
                break
        if not merged:
            kept.append(proj)
    return kept, removed


def _apply_automatic_fixes(parsed: dict) -> list[str]:
    """Applies every automatic fix this module owns (see module docstring)
    directly to `parsed`, IN PLACE, and returns a list of human-readable
    descriptions of what was actually changed - empty if nothing needed
    fixing. Never invents content; every fix is a removal, a reordering,
    or a lexical (verb-only) substitution."""
    changes: list[str] = []

    summary = parsed.get("summary")
    if isinstance(summary, str) and summary.strip():
        deduped_summary = fix_repeated_words(summary)
        fixed_summary = fix_truncated_sentence(deduped_summary)
        if fixed_summary != summary:
            parsed["summary"] = fixed_summary
            if deduped_summary != summary:
                changes.append("Collapsed a repeated word in the Professional Summary.")
            if fixed_summary != deduped_summary:
                changes.append("Trimmed a truncated/incomplete sentence in the Professional Summary.")

    skills = parsed.get("skills")
    if isinstance(skills, list) and skills:
        reprocessed = build_technical_skills(skills)
        if reprocessed != skills:
            removed = len(skills) - len(reprocessed)
            parsed["skills"] = reprocessed
            changes.append(
                f"Removed {max(removed, 0)} duplicate/aliased technology(ies) from Technical Skills "
                "via an alias-aware re-check."
            )

    tools = parsed.get("tools")
    if isinstance(tools, list) and tools:
        # No longer truncates to MAX_TOOLS - a candidate's genuine tool must
        # never silently disappear here just because the list is long (see
        # skill_intelligence.py/tool_classifier.py's own "never drop" fix).
        # Still re-runs normalize+cluster for consistency; only reports a
        # change if that actually altered something (dedup/alias cleanup).
        reprocessed_tools = cap_tools(tools)
        if reprocessed_tools != tools:
            parsed["tools"] = reprocessed_tools
            changes.append("Normalized/deduplicated Tools entries (no entries removed for length).")

    for exp in parsed.get("experience") or []:
        if exp.get("is_career_break"):
            continue
        points = exp.get("points") or []
        if not points:
            continue
        mechanics_fixed, repeated_fixes, truncation_fixes = _fix_text_mechanics_in_bullets(points)
        if repeated_fixes:
            changes.append(
                f"Collapsed {repeated_fixes} repeated-word bullet(s) for {exp.get('company') or '(no company)'}."
            )
        if truncation_fixes:
            changes.append(
                f"Trimmed {truncation_fixes} truncated/incomplete bullet(s) for "
                f"{exp.get('company') or '(no company)'}."
            )
        deduped = merge_near_duplicate_bullets(mechanics_fixed)
        theme = classify_project_theme(deduped)
        diversified = diversify_action_verbs(deduped, theme)
        fixed, weak_count = _fix_weak_verbs_in_bullets(diversified)
        if fixed != points:
            exp["points"] = fixed
            if weak_count:
                changes.append(
                    f"Rewrote {weak_count} weak-verb bullet opening(s) for "
                    f"{exp.get('company') or '(no company)'}."
                )

    projects = parsed.get("projects") or []
    if projects:
        deduped_projects, removed_count = _merge_near_duplicate_projects(projects)
        if removed_count:
            parsed["projects"] = deduped_projects
            changes.append(f"Merged {removed_count} redundant project(s) with near-identical content.")
        for proj in parsed.get("projects") or []:
            responsibilities = proj.get("responsibilities") or []
            if not responsibilities:
                continue
            mechanics_fixed_resp, repeated_fixes, truncation_fixes = _fix_text_mechanics_in_bullets(responsibilities)
            if repeated_fixes:
                changes.append(
                    f"Collapsed {repeated_fixes} repeated-word bullet(s) in project "
                    f"{proj.get('title') or '(untitled)'}."
                )
            if truncation_fixes:
                changes.append(
                    f"Trimmed {truncation_fixes} truncated/incomplete bullet(s) in project "
                    f"{proj.get('title') or '(untitled)'}."
                )
            deduped_resp = merge_near_duplicate_bullets(mechanics_fixed_resp)
            theme = classify_project_theme(deduped_resp)
            diversified_resp = diversify_action_verbs(deduped_resp, theme)
            fixed_resp, weak_count = _fix_weak_verbs_in_bullets(diversified_resp)
            if fixed_resp != responsibilities:
                proj["responsibilities"] = fixed_resp
                if weak_count:
                    changes.append(
                        f"Rewrote {weak_count} weak-verb bullet opening(s) in project "
                        f"{proj.get('title') or '(untitled)'}."
                    )

    return changes


# --- New quality checks (STEP: Quality Checks not covered elsewhere) --------

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)
_KEYWORD_STUFFING_TOKEN_THRESHOLD = 12
_MIN_IMPACT_RATIO = 0.3
_CHARS_PER_PAGE = 3200  # same calibration file_generator.py's own estimator uses, reimplemented locally
_PROJECT_BULLET_TARGET_MAX = 5


def _years_of_experience(experience: list[dict] | None) -> float:
    """Reimplemented locally (same approach as file_generator.py/
    resume_structuring.py/experience_intelligence.py's own copies) rather
    than importing a private cross-module helper - an established pattern
    in this pipeline."""
    import datetime
    current_year = datetime.date.today().year
    years: list[int] = []
    for exp in experience or []:
        duration = str(exp.get("duration") or "")
        if _PRESENT_RE.search(duration):
            years.append(current_year)
        years.extend(int(y) for y in _YEAR_RE.findall(duration))
    if not years:
        return 0.0
    return float(max(years) - min(years))


def _check_duplicate_technologies(fix_changes: list[str], warnings: list[str]) -> int:
    matches = [c for c in fix_changes if "duplicate/aliased technology" in c]
    if matches:
        warnings.extend(matches)
        return 4
    return 7


def _check_keyword_stuffing(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    summary = str(parsed.get("summary") or "")
    for sentence in re.split(r"[.!?]+", summary):
        tokens = [t for t in sentence.split(",") if t.strip()]
        if len(tokens) > _KEYWORD_STUFFING_TOKEN_THRESHOLD:
            warnings.append("Professional Summary reads as a keyword-stuffed list rather than natural prose.")
            suggestions.append("Rewrite the summary as flowing sentences instead of a comma-separated keyword list.")
            return 2
    return 6


def _check_excessive_skills(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    skills = parsed.get("skills") or []
    count = len(skills) if isinstance(skills, list) else sum(len(v) for v in skills.values())
    if count > MAX_TECHNICAL_SKILLS:
        warnings.append(f"Technical Skills has {count} entries - above the recommended {MAX_TECHNICAL_SKILLS}.")
        suggestions.append("Trim Technical Skills to the most relevant, senior, and frequently-used items.")
        return 2
    return 6


def _check_excessive_tools(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    tools = parsed.get("tools") or []
    if len(tools) > MAX_TOOLS:
        warnings.append(f"Tools has {len(tools)} entries - above the recommended {MAX_TOOLS}.")
        suggestions.append("Trim Tools to the most relevant, frequently-used software.")
        return 2
    return 6


def _check_missing_business_impact(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    bullets = [p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or [])]
    bullets += [r for proj in (parsed.get("projects") or []) for r in (proj.get("responsibilities") or [])]
    if not bullets:
        return 7
    with_impact = sum(1 for b in bullets if has_impact_signal(b))
    ratio = with_impact / len(bullets)
    if ratio < _MIN_IMPACT_RATIO:
        warnings.append(
            f"Only {with_impact}/{len(bullets)} bullets state a measurable metric or business outcome."
        )
        suggestions.append("Add quantifiable metrics or business outcomes to more bullet points.")
        return round(7 * ratio / _MIN_IMPACT_RATIO)
    return 7


def _check_long_experience_and_projects(parsed: dict, warnings: list[str]) -> tuple[int, int]:
    experience_score, project_score = 6, 6
    for exp in parsed.get("experience") or []:
        if exp.get("is_career_break"):
            continue
        points = exp.get("points") or []
        _lo, hi = bullet_range_for_role(exp.get("role"))
        if len(points) > hi:
            warnings.append(
                f"{exp.get('company') or '(no company)'} has {len(points)} bullets - above the "
                f"{hi}-bullet target for this role."
            )
            experience_score = max(experience_score - 2, 0)
    for proj in parsed.get("projects") or []:
        responsibilities = proj.get("responsibilities") or []
        if len(responsibilities) > _PROJECT_BULLET_TARGET_MAX:
            warnings.append(
                f"Project {proj.get('title') or '(untitled)'} has {len(responsibilities)} bullets - above "
                f"the {_PROJECT_BULLET_TARGET_MAX}-bullet target."
            )
            project_score = max(project_score - 2, 0)
    return experience_score, project_score


def _estimate_content_chars(parsed: dict) -> int:
    chars = len(str(parsed.get("summary") or ""))
    for field in ("skills", "tools", "education", "certifications", "achievements"):
        chars += sum(len(str(item)) for item in (parsed.get(field) or []))
    for exp in parsed.get("experience") or []:
        chars += len(str(exp.get("company") or "")) + len(str(exp.get("role") or ""))
        chars += sum(len(str(p)) for p in (exp.get("points") or []))
    for proj in parsed.get("projects") or []:
        chars += len(str(proj.get("title") or "")) + len(str(proj.get("description") or ""))
        chars += sum(len(str(r)) for r in (proj.get("responsibilities") or []))
    return chars


def _check_resume_length(parsed: dict, warnings: list[str], suggestions: list[str]) -> int:
    years = _years_of_experience(parsed.get("experience"))
    budget_pages = determine_page_budget(years)
    estimated_pages = max(1, round(_estimate_content_chars(parsed) / _CHARS_PER_PAGE))
    if estimated_pages > budget_pages:
        warnings.append(
            f"Estimated content volume (~{estimated_pages} page(s)) exceeds the {budget_pages}-page "
            f"target for ~{years:.0f} years of experience."
        )
        suggestions.append("Compress lower-priority sections (Certifications, Education) before shortening Experience.")
        return 3
    return 7


# --- Orchestration -------------------------------------------------------------

# Weights for the 15 named checks - sums to 100. Reuses (rather than
# re-detects) resume_validator.py's own findings for Duplicate Bullets/
# Duplicate Projects/Section Completeness, and resume_quality_checker.py's
# own sub-scores for Weak Action Verbs/AI-sounding language/Generic
# Summary/Missing Metrics - see _build_breakdown.
_WEIGHTS = {
    "duplicate_technologies": 7, "duplicate_bullets": 7, "duplicate_projects": 7,
    "weak_action_verbs": 7, "ai_sounding_language": 5, "keyword_stuffing": 6,
    "excessive_skills": 6, "excessive_tools": 6, "generic_summary": 8,
    "missing_metrics": 7, "missing_business_impact": 7, "long_projects": 6,
    "long_experience": 6, "section_completeness": 8, "resume_length": 7,
}
assert sum(_WEIGHTS.values()) == 100, "resume_quality_engine weights must sum to 100"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = re.sub(r"\s+", " ", item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _build_breakdown(parsed: dict, validation_warnings: list[str], fix_changes: list[str]) -> tuple[dict, list[str], list[str]]:
    warnings: list[str] = []
    suggestions: list[str] = []

    base_report = check_resume_quality(parsed, validation_warnings)
    base_warnings, base_breakdown = base_report["warnings"], base_report["breakdown"]

    duplicate_bullet_warnings = [w for w in validation_warnings if "duplicate bullet" in w.lower()]
    duplicate_project_warnings = [w for w in validation_warnings if "repeated project" in w.lower()]
    warnings.extend(duplicate_bullet_warnings)
    warnings.extend(duplicate_project_warnings)

    breakdown = {
        "duplicate_technologies": _check_duplicate_technologies(fix_changes, warnings),
        "duplicate_bullets": max(_WEIGHTS["duplicate_bullets"] - len(duplicate_bullet_warnings), 0),
        "duplicate_projects": max(_WEIGHTS["duplicate_projects"] - len(duplicate_project_warnings), 0),
        # Weak Action Verbs / AI-sounding language / Generic Summary /
        # Missing Metrics reuse resume_quality_checker.py's own dimension
        # scores (rescaled to this module's weights) rather than
        # re-detecting the same thing a second, potentially inconsistent
        # way.
        "weak_action_verbs": round(_WEIGHTS["weak_action_verbs"] * base_breakdown["experience"] / 20),
        "ai_sounding_language": round(_WEIGHTS["ai_sounding_language"] * base_breakdown["readability"] / 10),
        "keyword_stuffing": _check_keyword_stuffing(parsed, warnings, suggestions),
        "excessive_skills": _check_excessive_skills(parsed, warnings, suggestions),
        "excessive_tools": _check_excessive_tools(parsed, warnings, suggestions),
        "generic_summary": round(_WEIGHTS["generic_summary"] * base_breakdown["summary"] / 15),
        "missing_metrics": round(_WEIGHTS["missing_metrics"] * base_breakdown["readability"] / 10),
        "missing_business_impact": _check_missing_business_impact(parsed, warnings, suggestions),
        "section_completeness": round(_WEIGHTS["section_completeness"] * base_breakdown["section_completeness"] / 5),
        "resume_length": _check_resume_length(parsed, warnings, suggestions),
    }
    long_experience_score, long_projects_score = _check_long_experience_and_projects(parsed, warnings)
    breakdown["long_experience"] = long_experience_score
    breakdown["long_projects"] = long_projects_score

    warnings.extend(base_warnings)
    suggestions.extend(base_report["suggestions"])
    return breakdown, warnings, suggestions


def run_quality_engine(parsed: dict, recovery_log: dict | None = None) -> tuple[dict, dict]:
    """Public entry point - the Phase 5 final gate. Runs
    resume_validator.py's structural clean/flag pass (unmodified),
    applies this module's own automatic fixes on top, then computes one
    unified quality report covering all 15 named checks. Returns
    (cleaned_parsed, report) - `report` is `{"score", "warnings",
    "suggestions", "breakdown", "automatic_fixes_applied",
    "section_recovery"}`, for internal validation/logging only (never
    rendered).

    `recovery_log` is OPTIONAL (default None - every existing caller that
    doesn't pass one, e.g. the manual Resume Generator flow which has no
    raw source text to recover from, gets an empty `{}` and identical
    behavior to before this parameter existed) - the dict
    section_recovery.recover_missing_sections() already produced earlier in
    the pipeline (right after Stage 1 extraction, BEFORE this engine runs),
    mapping each section it had to act on to which tier recovered it
    ("retry"/"inference"/"deterministic") or "unrecovered". Surfaced here
    rather than only in application logs, so a caller/test can inspect
    exactly what the Section Recovery Engine did (or couldn't do) for a
    given resume without re-deriving it."""
    parsed, validation_report = validate_and_clean_resume(parsed)
    fix_changes = _apply_automatic_fixes(parsed)

    breakdown, warnings, suggestions = _build_breakdown(parsed, validation_report.warnings, fix_changes)
    report = {
        "score": sum(breakdown.values()),
        "warnings": _dedupe_preserve_order(warnings),
        "suggestions": _dedupe_preserve_order(suggestions),
        "breakdown": breakdown,
        "automatic_fixes_applied": fix_changes,
        "section_recovery": dict(recovery_log or {}),
    }
    if fix_changes:
        logger.info("Resume Quality Engine applied %d automatic fix(es): %r", len(fix_changes), fix_changes)
    logger.info("Resume Quality Engine: overall score=%d", report["score"])
    return parsed, report
