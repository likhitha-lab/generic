"""Experience Intelligence Engine - Phase 3 of the Resume Intelligence Engine.

Runs AFTER experience_refiner.py's per-job Gemini consolidation (and
resume_optimizer.py's _optimize_projects) and BEFORE the Resume Builder -
see enhance_experience()/enhance_projects(), this module's two public
entry points, wired into resume_optimizer.optimize_resume().

WHY THIS EXISTS (see the accompanying architecture review for the full
analysis): experience_refiner.py deliberately runs one independent Gemini
call PER JOB (to avoid the truncation failure mode a single giant call
produces on a long resume - see that module's own docstring). That
architecture is correct and is NOT changed here, but it has a structural
side effect: no single Gemini call ever sees more than one job's bullets at
once, so nothing in the existing pipeline can notice "this job opened with
the same verb as that job" or "this bullet's metric is buried three lines
down while an equally-important one from another job never got surfaced
at all". A deterministic pass that runs across the WHOLE assembled
experience/projects list, after every individual job/project has already
been refined, is the only place that kind of cross-entry consistency can
be enforced - which is exactly this module's job.

Deliberately does NOT touch prompts.py (forbidden this phase) or
experience_refiner.py/resume_optimizer.py's Gemini-calling logic - every
transformation here is pure, deterministic, and reversible-in-spirit: it
only ever (a) reorders existing bullets, (b) merges near-duplicate bullets,
(c) trims bullets beyond an already-established target range (Phase 1's
role-tier bullet ranges - see experience_refiner.bullet_range_for_role,
reused here unmodified, never re-defined, so this phase cannot regress
Phase 1's tiering), or (d) swaps a REPEATED opening verb for a different,
equally-accurate synonym while leaving the rest of the sentence untouched.
Nothing here ever adds a new claim, technology, metric, or outcome that
wasn't already in the bullet.

DELIBERATE, FLAGGED DEVIATION - Business Impact Enhancement (STEP 8): the
request's own worked examples ("Developed REST APIs" -> "Developed
scalable REST APIs supporting high-volume enterprise integrations") add
specific, unverified claims that are not present in the original bullet.
Every prior phase of this project has enforced one non-negotiable rule:
never invent a detail, metric, or outcome that isn't supported by the
source content (see experience_refiner.py's own docstring, prompts.py's
"Do NOT invent" clauses throughout, Phase 1/2's identical language). Doing
that deterministically - with no model call to exercise judgment about
what's plausible for a SPECIFIC candidate - would be fabrication, not
"enhancement". This module instead implements Business Impact Enhancement
as detection + prioritized placement: bullets that ALREADY state a
measurable impact (see extract_metrics/has_impact_signal) are surfaced
first within their job/project, and any bullet with no impact signal at
all is logged as a finding (never silently rewritten) - the same
detect-and-log philosophy Phase 1's own _quality_audit already established
for anything requiring semantic judgment rather than mechanical, factual-
content-preserving transformation.
"""
import datetime
import difflib
import logging
import re

from app.services.experience_refiner import bullet_range_for_role

logger = logging.getLogger(__name__)

# Project bullet target (STEP 6) - Phase 1 already established this exact
# range via prompts.py's build_projects_optimize_prompt (3 to 5) and a
# matching hard cap in resume_optimizer._quality_audit; reused here as this
# module's own project cap so both phases agree, never re-defined
# differently.
_PROJECT_BULLET_RANGE = (3, 5)

_BULLET_MERGE_THRESHOLD = 0.85  # bullet-level (full sentences), more conservative than a raw string compare
_CROSS_ENTRY_MERGE_THRESHOLD = 0.85


# --- STEP 3: Project Classification ------------------------------------------
#
# Automatically determines the dominant purpose of a job/project from its
# own bullet text - keyword-hit counting across every bullet, highest count
# wins (ties broken by this list's own order). Purely internal: never
# rendered, used only to (a) pick a verb-preference order for STEP 5's
# diversification and (b) as a label for the log lines STEP 10 produces.
_THEME_ORDER: tuple[str, ...] = (
    "Cloud Migration", "Cloud Modernization", "Architecture", "Automation", "AI",
    "Machine Learning", "Data Engineering", "DevOps", "Security", "Performance Optimization",
    "Infrastructure", "Application Development", "Integration", "Analytics", "Power Platform",
    "Digital Transformation", "ERP", "CRM", "Pre-sales", "Support", "Maintenance", "Migration",
    "Legacy Modernization", "Full Stack Development", "Backend Engineering", "Frontend Engineering",
    "API Development", "Microservices",
)

_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Cloud Migration": ("cloud migration", "lift and shift", "migrated to azure", "migrated to aws",
                         "migrated to the cloud", "on-premises to cloud", "workload migration"),
    "Cloud Modernization": ("cloud modernization", "cloud-native", "cloud native", "refactored for cloud",
                             "containerized", "modernized infrastructure"),
    "Architecture": ("architecture", "system design", "solution architecture", "architected",
                      "designed the architecture"),
    "Automation": ("automation", "automated", "automated deployment", "scripted", "runbook"),
    "AI": ("artificial intelligence", "generative ai", "llm", "chatbot", "ai-powered", "ai solution"),
    "Machine Learning": ("machine learning", "ml model", "predictive model", "trained a model",
                          "deep learning", "neural network"),
    "Data Engineering": ("data pipeline", "etl", "data warehouse", "data lake", "spark", "airflow"),
    "DevOps": ("ci/cd", "devops", "jenkins", "deployment pipeline", "infrastructure as code", "terraform"),
    "Security": ("security", "compliance", "vulnerability", "penetration testing", "hardening",
                 "iam", "encryption"),
    "Performance Optimization": ("performance", "latency", "throughput", "optimized query",
                                 "load time", "response time"),
    "Infrastructure": ("infrastructure", "servers", "networking", "virtual machines", "provisioned",
                        "data center"),
    "Application Development": ("application", "software application", "developed the application",
                                 "built the application"),
    "Integration": ("integration", "integrated", "api integration", "third-party integration", "middleware"),
    "Analytics": ("analytics", "dashboard", "reporting", "business intelligence", "power bi", "tableau"),
    "Power Platform": ("power apps", "power automate", "power virtual agents", "power platform"),
    "Digital Transformation": ("digital transformation", "modernized business processes", "digitized"),
    "ERP": ("erp", "sap", "oracle erp", "dynamics 365", "enterprise resource planning"),
    "CRM": ("crm", "salesforce", "dynamics crm", "customer relationship management"),
    "Pre-sales": ("pre-sales", "presales", "proof of concept", "solution proposal", "client demo", "rfp"),
    "Support": ("support", "ticket", "incident", "troubleshooting", "helpdesk", "on-call"),
    "Maintenance": ("maintenance", "patching", "upgrades", "bug fixes", "maintained the system"),
    "Migration": ("migration", "migrated", "data migration", "cutover", "legacy system migration"),
    "Legacy Modernization": ("legacy", "modernized legacy", "legacy system", "re-platformed"),
    "Full Stack Development": ("full stack", "full-stack", "frontend and backend", "end-to-end development"),
    "Backend Engineering": ("backend", "server-side", "backend services", "backend api"),
    "Frontend Engineering": ("frontend", "ui development", "user interface", "react", "angular"),
    "API Development": ("rest api", "api development", "graphql", "web services", "endpoints"),
    "Microservices": ("microservices", "microservice architecture", "service-oriented"),
}

_DEFAULT_THEME = "Application Development"


def classify_project_theme(bullets: list[str]) -> str:
    """Returns the dominant theme (see _THEME_ORDER) across every bullet in
    one job/project, by keyword-hit count - ties broken by _THEME_ORDER's
    own order. Falls back to _DEFAULT_THEME if nothing matches at all."""
    combined = " ".join(bullets).lower()
    best_theme, best_count = _DEFAULT_THEME, 0
    for theme in _THEME_ORDER:
        count = sum(combined.count(keyword) for keyword in _THEME_KEYWORDS[theme])
        if count > best_count:
            best_theme, best_count = theme, count
    return best_theme


# --- STEP 4: Project Narrative Engine (verb preference only) ---------------
#
# Not a text-rewriting stage - see module docstring's flagged deviation.
# Instead, gives STEP 5's verb diversification a theme-appropriate ORDER to
# prefer when it needs a substitute for a repeated opening verb, so a
# Migration-themed job naturally reaches for "Migrated"/"Modernized" before
# a Security-themed job would reach for "Hardened"/"Secured" - the same
# underlying diversification mechanism, just tuned per project so two
# differently-themed jobs don't end up sounding identical either.
_THEME_VERB_PREFERENCE: dict[str, tuple[str, ...]] = {
    "Cloud Migration": ("Migrated", "Modernized", "Orchestrated", "Executed"),
    "Cloud Modernization": ("Modernized", "Architected", "Redesigned", "Refactored"),
    "Architecture": ("Architected", "Designed", "Engineered", "Established"),
    "Automation": ("Automated", "Engineered", "Streamlined", "Accelerated"),
    "AI": ("Developed", "Engineered", "Automated", "Delivered"),
    "Machine Learning": ("Developed", "Trained", "Engineered", "Optimized"),
    "Data Engineering": ("Engineered", "Built", "Automated", "Optimized"),
    "DevOps": ("Automated", "Established", "Orchestrated", "Streamlined", "Standardized"),
    "Security": ("Hardened", "Secured", "Established", "Reduced"),
    "Performance Optimization": ("Optimized", "Accelerated", "Reduced", "Improved"),
    "Infrastructure": ("Provisioned", "Engineered", "Established", "Automated"),
    "Application Development": ("Developed", "Built", "Implemented", "Delivered"),
    "Integration": ("Integrated", "Engineered", "Delivered", "Automated"),
    "Analytics": ("Developed", "Delivered", "Engineered", "Enhanced"),
    "Power Platform": ("Developed", "Automated", "Built", "Delivered"),
    "Digital Transformation": ("Led", "Directed", "Modernized", "Delivered", "Transformed", "Mentored"),
    "ERP": ("Implemented", "Configured", "Delivered", "Led"),
    "CRM": ("Implemented", "Configured", "Delivered", "Enhanced"),
    "Pre-sales": ("Delivered", "Directed", "Led", "Executed"),
    "Support": ("Resolved", "Improved", "Delivered", "Executed", "Reviewed"),
    "Maintenance": ("Maintained", "Improved", "Enhanced", "Executed", "Reviewed"),
    "Migration": ("Migrated", "Modernized", "Executed", "Orchestrated"),
    "Legacy Modernization": ("Modernized", "Redesigned", "Refactored", "Re-platformed"),
    "Full Stack Development": ("Built", "Developed", "Delivered", "Engineered"),
    "Backend Engineering": ("Engineered", "Built", "Developed", "Optimized"),
    "Frontend Engineering": ("Built", "Designed", "Developed", "Enhanced"),
    "API Development": ("Developed", "Engineered", "Built", "Integrated"),
    "Microservices": ("Architected", "Engineered", "Built", "Migrated"),
}

# Verbs recognized as an eligible OPENING verb for diversification (STEP 5's
# own list, plus a few common synonyms already used elsewhere in this
# pipeline's prompts) - only a bullet that ALREADY opens with one of these
# is ever touched; anything else (a weak verb, no verb at all) is left
# alone, since rewriting those requires semantic judgment this module
# deliberately doesn't attempt (see Quality Validation below).
_RECOGNIZED_ACTION_VERBS = {
    "architected", "modernized", "migrated", "engineered", "optimized", "delivered",
    "automated", "established", "directed", "integrated", "developed", "built", "led",
    "implemented", "redesigned", "refactored", "enhanced", "orchestrated", "accelerated",
    "reduced", "improved", "executed", "designed", "configured", "provisioned",
    "streamlined", "resolved", "maintained", "hardened", "secured", "trained",
    "re-platformed", "transformed", "standardized", "mentored", "reviewed",
}

# Weak, non-committal lead-in phrases - "Responsible for managing X" names
# no action of its own; the candidate's OWN gerund right after the phrase
# already IS the real action. Promoting it to past tense and dropping the
# filler is a pure grammar fix, never a new fact: the verb was already
# implied by the bullet's own gerund, not invented here. Deliberately a
# small, closed set on both sides (phrase AND gerund) - a bullet whose
# gerund isn't in _GERUND_TO_PAST_TENSE is left completely unchanged, same
# "never guess" discipline _RECOGNIZED_ACTION_VERBS's own docstring already
# commits to for weak verbs.
_WEAK_OPENER_PHRASES = (
    "was responsible for", "responsible for", "was involved in", "involved in",
    "worked on", "helped with", "helped in", "assisted with", "assisted in",
    "in charge of", "tasked with",
)

# Fallback strong verb for a weak phrase that ISN'T followed by a
# recognized gerund (e.g. "Responsible for the ETL pipeline design" - no
# gerund to promote, so the phrase itself is replaced directly). Chosen to
# stay as close as possible to the ORIGINAL scope of involvement the weak
# phrase itself implied - "Managed"/"In charge of" for phrases that already
# implied ownership, "Contributed to"/"Supported" (not "Led") for phrases
# that implied a modest/collaborative role, so this never overclaims
# seniority the source didn't state. Every word after the phrase (every
# technical term, number, metric) is passed through untouched either way.
_WEAK_PHRASE_FALLBACK_VERB = {
    "was responsible for": "Managed", "responsible for": "Managed",
    "in charge of": "Managed", "tasked with": "Executed",
    "was involved in": "Contributed to", "involved in": "Contributed to",
    "worked on": "Contributed to",
    "helped with": "Supported", "helped in": "Supported",
    "assisted with": "Supported", "assisted in": "Supported",
}

# Standalone single-word weak openers - unlike the phrases above, these
# already look like normal verbs and aren't followed by a gerund to
# promote; the weak word itself is the whole problem, so only the opening
# word is swapped (same one-word-only touch _swap_first_word already uses
# for diversify_action_verbs, never touching anything else in the bullet).
_WEAK_SINGLE_WORD_OPENERS = {
    "handled": "Managed",
}

_GERUND_TO_PAST_TENSE = {
    "managing": "Managed", "developing": "Developed", "designing": "Designed",
    "leading": "Led", "building": "Built", "creating": "Created",
    "maintaining": "Maintained", "testing": "Tested", "implementing": "Implemented",
    "coordinating": "Coordinated", "supporting": "Supported", "writing": "Wrote",
    "reviewing": "Reviewed", "monitoring": "Monitored", "configuring": "Configured",
    "documenting": "Documented", "analyzing": "Analyzed", "resolving": "Resolved",
    "automating": "Automated", "migrating": "Migrated", "integrating": "Integrated",
    "optimizing": "Optimized", "delivering": "Delivered", "handling": "Handled",
    "ensuring": "Ensured", "performing": "Performed", "conducting": "Conducted",
    "executing": "Executed", "deploying": "Deployed", "training": "Trained",
    "planning": "Planned", "preparing": "Prepared", "collaborating": "Collaborated",
}


def replace_weak_verb_openers(bullets: list[str]) -> list[str]:
    """Removes weak, passive lead-ins ("Responsible for", "Worked on",
    "Handled", ...) in favor of a stronger action verb, never changing
    anything else in the bullet - every technical term, number, and metric
    after the opener is passed through untouched.

    Three cases, in priority order:
    1. Phrase followed by the candidate's OWN gerund ("Responsible for
       managing X") - promotes that exact gerund to past tense ("Managed
       X"). Preferred whenever it applies: the verb comes from what the
       candidate already wrote, not a generic substitute.
    2. Phrase followed by anything else, e.g. a plain noun phrase
       ("Responsible for the ETL pipeline design") - replaced with a fixed
       fallback verb chosen to match the phrase's own original scope
       (_WEAK_PHRASE_FALLBACK_VERB) - "Managed"/"In charge of"-type phrases
       keep implying ownership, "Worked on"/"Helped with"-type phrases
       keep a modest/collaborative framing, never inflated into a
       stronger claim than the source made.
    3. A standalone weak single-word opener ("Handled X") - only that one
       word is swapped (_WEAK_SINGLE_WORD_OPENERS), same one-word-only
       touch diversify_action_verbs already uses.

    A bullet matching none of the above (already opens with a real action
    verb, or an unrecognized phrasing) is returned completely unchanged -
    never guessed.

    CONFIRMED real-world case 1 guards against (found scanning actual
    regression sample resumes): "Responsible for leading & manage Business
    waste..." - promoting only the first of two coordinated verbs would
    produce "Led & manage Business waste...", mixing past tense with a
    bare-form verb into a grammatically broken sentence. Whenever the token
    right after the gerund is a coordinating conjunction ("&"/"and"), the
    WHOLE bullet is left unchanged instead (no fallback attempted either) -
    a second verb likely follows that no automatic rewrite can safely
    handle."""
    result: list[str] = []
    for bullet in bullets:
        stripped = bullet.strip()
        lowered = stripped.lower()
        phrase = next((p for p in _WEAK_OPENER_PHRASES if lowered.startswith(p + " ")), None)
        if phrase is not None:
            remainder = stripped[len(phrase):].strip()
            if not remainder:
                result.append(bullet)
                continue
            parts = remainder.split(" ", 1)
            gerund = parts[0].strip(".,;:").lower()
            if len(parts) == 2 and gerund in _GERUND_TO_PAST_TENSE:
                next_token = parts[1].split(" ", 1)[0].strip(".,;:").lower()
                if next_token in ("&", "and"):
                    result.append(bullet)
                else:
                    result.append(f"{_GERUND_TO_PAST_TENSE[gerund]} {parts[1]}")
                continue
            result.append(f"{_WEAK_PHRASE_FALLBACK_VERB[phrase]} {remainder}")
            continue

        opener = _first_word(stripped)
        if opener in _WEAK_SINGLE_WORD_OPENERS:
            result.append(_swap_first_word(stripped, _WEAK_SINGLE_WORD_OPENERS[opener]))
            continue
        result.append(bullet)
    return result


def _first_word(text: str) -> str:
    words = text.strip().split()
    return words[0].strip(".,;:").lower() if words else ""


def _swap_first_word(text: str, new_verb: str) -> str:
    words = text.strip().split()
    if not words:
        return text
    # Preserve trailing punctuation on the original first word (if any).
    trailing = ""
    stripped = words[0]
    while stripped and stripped[-1] in ".,;:":
        trailing = stripped[-1] + trailing
        stripped = stripped[:-1]
    words[0] = new_verb + trailing
    return " ".join(words)


def diversify_action_verbs(bullets: list[str], theme: str) -> list[str]:
    """STEP 5 - never begin every bullet the same way. The FIRST bullet
    opening with a given recognized action verb is left untouched; every
    SUBSEQUENT bullet opening with that SAME verb has its opening word (and
    only its opening word) swapped for the next unused verb from the
    theme's preferred pool (falling back to the full recognized-verb set if
    the theme pool runs out) - the rest of the sentence, and therefore
    every fact in it, is never touched. A bullet that doesn't open with a
    recognized action verb at all is left completely unchanged."""
    preference = _THEME_VERB_PREFERENCE.get(theme, ())
    fallback_pool = tuple(v.capitalize() for v in sorted(_RECOGNIZED_ACTION_VERBS))
    full_pool = list(preference) + [v for v in fallback_pool if v not in preference]

    openers = [_first_word(b) for b in bullets]
    # Reserve every verb that genuinely opens at least one bullet ANYWHERE
    # in the input up front - each such verb's first occurrence is always
    # kept as-is (see the loop below), so it must never ALSO be handed out
    # as a replacement for a different repeated verb; doing so would just
    # recreate a duplicate-opener collision against that genuine bullet,
    # wherever it happens to sit in the list (including a later one this
    # single left-to-right pass hasn't reached yet).
    reserved = {opener for opener in openers if opener in _RECOGNIZED_ACTION_VERBS}

    seen_verbs: set[str] = set()
    used_replacements: set[str] = set(reserved)
    result: list[str] = []
    for bullet, opener in zip(bullets, openers):
        if opener not in _RECOGNIZED_ACTION_VERBS:
            result.append(bullet)
            continue
        if opener not in seen_verbs:
            seen_verbs.add(opener)
            result.append(bullet)
            continue
        # Repeated opener - find the next verb in the theme-preferred pool
        # (falling back to the full recognized set) that isn't reserved and
        # hasn't already been used as a replacement elsewhere in this job.
        replacement = None
        for candidate in full_pool:
            if candidate.lower() not in used_replacements:
                replacement = candidate
                break
        if replacement is None:
            result.append(bullet)  # exhausted the pool - leave as-is rather than reusing a verb
            continue
        used_replacements.add(replacement.lower())
        result.append(_swap_first_word(bullet, replacement))
    return result


# --- STEP 6: Bullet Compression ----------------------------------------------

def _dedupe_exact(bullets: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for bullet in bullets:
        key = re.sub(r"\s+", " ", bullet).strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(bullet)
    return deduped


def merge_near_duplicate_bullets(bullets: list[str], threshold: float = _BULLET_MERGE_THRESHOLD) -> list[str]:
    """Conservative fuzzy merge for bullets that survived experience_refiner
    .py's own Gemini-based consolidation as near-identical restatements
    (phrasing differences the model's own dedup didn't quite catch) - same
    high-threshold, log-every-merge, never-invent approach used throughout
    this pipeline (see skill_intelligence.py's own _fuzzy_merge)."""
    deduped = _dedupe_exact(bullets)
    kept: list[str] = []
    for bullet in deduped:
        merged_into = None
        best_ratio = 0.0
        for existing in kept:
            ratio = difflib.SequenceMatcher(None, bullet.lower(), existing.lower()).ratio()
            if ratio >= threshold and ratio > best_ratio:
                merged_into, best_ratio = existing, ratio
        if merged_into:
            logger.info(
                "Experience Intelligence Engine: merged near-duplicate bullet %r into %r (similarity=%.2f)",
                bullet, merged_into, best_ratio,
            )
        else:
            kept.append(bullet)
    return kept


def enforce_bullet_cap(bullets: list[str], min_bullets: int, max_bullets: int) -> list[str]:
    """Trims to `max_bullets` if over (keeping the first N in order - see
    prioritize_bullets_with_impact, called before this, for why "first N"
    already favors impact-bearing bullets). Never pads below `min_bullets` -
    fewer genuine bullets than the target is fine, inventing one is not."""
    if len(bullets) > max_bullets:
        return bullets[:max_bullets]
    return bullets


# --- STEP 7: Achievement Detection / Metric Extraction -----------------------

_METRIC_PATTERNS = (
    re.compile(r"\b\d+(?:\.\d+)?%"),
    re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|million|billion)?\b", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s?[kKmM]?\+?\s*(?:users|customers|applications|apps|projects|servers|"
               r"engineers|team members|clients|transactions|requests)\b", re.IGNORECASE),
    re.compile(r"\bteam of \d+\b", re.IGNORECASE),
    re.compile(r"\b\d{2,}\+"),
)

_IMPACT_KEYWORDS = (
    "improving", "improved", "reducing", "reduced", "enabling", "enabled",
    "resulting in", "supporting", "increasing", "increased", "saving", "savings",
    "accelerating", "accelerated", "boosting", "streamlining",
    # Business-impact THEMES (STEP 9) - a bullet naming one of these is
    # evidence of a business-relevant outcome even without an explicit
    # metric/outcome verb alongside it (e.g. "Led the cloud migration" is
    # itself an impact signal - migrations, security work, and leadership
    # are inherently business-relevant, not just neutral task descriptions).
    "cost reduction", "cost savings", "performance improvement", "migration",
    "modernization", "digital transformation", "cloud modernization",
    "automation", "security", "leadership", "mentoring", "mentored",
    "led a team", "led the", "architected",
)


def extract_metrics(bullet: str) -> list[str]:
    """Regex-only extraction of quantifiable signals ALREADY present in a
    bullet's text (percentages, dollar amounts, scale counts, team sizes) -
    never infers or invents one; a bullet with no such pattern simply
    returns an empty list."""
    found: list[str] = []
    for pattern in _METRIC_PATTERNS:
        found.extend(match.group(0) for match in pattern.finditer(bullet))
    return found


def has_impact_signal(bullet: str) -> bool:
    """True if the bullet already states a measurable metric OR uses
    outcome-oriented language (see _IMPACT_KEYWORDS) - used to prioritize
    placement (STEP 8) and to flag (never rewrite) bullets with neither."""
    if extract_metrics(bullet):
        return True
    lowered = bullet.lower()
    return any(keyword in lowered for keyword in _IMPACT_KEYWORDS)


def detect_achievements(bullets: list[str]) -> list[str]:
    """Returns the subset of bullets that already state a measurable
    metric (STEP 7) - in their original order, never reworded."""
    return [b for b in bullets if extract_metrics(b)]


# --- STEP 8: Business Impact Enhancement (prioritization, not invention) ----

def prioritize_bullets_with_impact(bullets: list[str]) -> list[str]:
    """Moves bullets that already carry a measurable metric or outcome-
    oriented language (see has_impact_signal) toward the front, via a
    STABLE partition - relative order is preserved within each group, so
    this never reorders two impact-bearing (or two non-impact-bearing)
    bullets relative to each other, only promotes the impact-bearing group
    as a whole. This is what makes a real, already-stated achievement
    survive a later bullet-cap trim instead of being cut off the bottom of
    a long list."""
    impactful = [b for b in bullets if has_impact_signal(b)]
    rest = [b for b in bullets if not has_impact_signal(b)]
    return impactful + rest


# --- STEP 9: Deduplication (cross-entry) -------------------------------------

def detect_cross_entry_repetition(entries: list[dict], threshold: float = _CROSS_ENTRY_MERGE_THRESHOLD) -> list[str]:
    """Detects (never auto-removes) near-duplicate wording/technologies/
    responsibilities repeated across DIFFERENT jobs or projects - each
    entry is a different employer/engagement, so a bullet that looks
    similar to one in another entry may still be a genuine, independently-
    true fact (the candidate may really have done similar work twice); only
    logged as a finding, exactly like resume_validator.py/resume_
    quality_checker.py's own established "detect and flag, never silently
    delete real content across different real-world contexts" pattern.
    Returns the list of warning messages produced (also logged)."""
    warnings: list[str] = []
    all_bullets: list[tuple[str, str]] = []  # (label, bullet)
    for entry in entries:
        label = entry.get("company") or entry.get("title") or "(unnamed entry)"
        for bullet in (entry.get("points") or entry.get("responsibilities") or []):
            all_bullets.append((label, bullet))

    for i in range(len(all_bullets)):
        label_a, bullet_a = all_bullets[i]
        for j in range(i + 1, len(all_bullets)):
            label_b, bullet_b = all_bullets[j]
            if label_a == label_b:
                continue  # within-entry repetition is Bullet Compression's job, not this stage's
            ratio = difflib.SequenceMatcher(None, bullet_a.lower(), bullet_b.lower()).ratio()
            if ratio >= threshold:
                message = (
                    f"Similar wording repeated across {label_a!r} and {label_b!r}: "
                    f"{bullet_a!r} / {bullet_b!r} (similarity={ratio:.2f})"
                )
                warnings.append(message)
                logger.info("Experience Intelligence Engine: %s", message)
    return warnings


# Small, hand-curated table of generic responsibility phrases that
# resumes commonly repeat VERBATIM across otherwise-unrelated jobs/
# projects (copy-pasted boilerplate, not a genuinely repeated fact) -
# mapped to a pool of meaning-preserving alternate phrasings. Index 0 of
# each pool is always the phrase's own original wording (used as the
# "already seen" marker below, never re-selected as a "variant").
_GENERIC_PHRASE_VARIANTS: dict[str, tuple[str, ...]] = {
    "designed scalable solutions": (
        "Designed scalable solutions", "Architected scalable systems", "Engineered scalable architectures",
    ),
    "architected scalable systems": (
        "Architected scalable systems", "Designed scalable solutions", "Engineered scalable architectures",
    ),
    "analyzed business requirements": (
        "Analyzed business requirements", "Assessed business needs", "Evaluated stakeholder requirements",
    ),
    "requirements analysis": (
        "Requirements analysis", "Requirements gathering", "Business needs assessment",
    ),
    "technical guidance": (
        "Technical guidance", "Technical mentorship", "Engineering direction",
    ),
    "agile grooming": (
        "Agile grooming", "Sprint planning support", "Backlog refinement",
    ),
    "understanding business requirements": (
        "Understanding business requirements", "Gathering stakeholder needs", "Clarifying business objectives",
    ),
    "requirement analysis": (
        "Requirement analysis", "Requirements gathering", "Business needs assessment",
    ),
    "designing scalable solutions": (
        "Designing scalable solutions", "Architecting scalable systems", "Engineering scalable architectures",
    ),
    "proposal creation": (
        "Proposal creation", "Solution proposal development", "Client proposal authoring",
    ),
    "grooming calls": (
        "Grooming calls", "Backlog grooming sessions", "Sprint refinement sessions",
    ),
    "sprint planning": (
        "Sprint planning", "Iteration planning", "Release planning",
    ),
    "support": (
        "Support", "Production support", "Operational support",
    ),
    "maintenance": (
        "Maintenance", "System upkeep", "Ongoing maintenance",
    ),
}


def _phrase_pattern(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![a-zA-Z0-9])" + re.escape(phrase) + r"(?![a-zA-Z0-9])", re.IGNORECASE)


def vary_repeated_generic_phrases(entries: list[dict]) -> int:
    """STEP 4 - Experience Deduplication (cross-entry narrative variety).
    Some raw resumes reuse the exact same generic responsibility phrase
    (e.g. "Requirements analysis", "Technical guidance") verbatim across
    multiple, otherwise-unrelated jobs or projects - copy-pasted
    boilerplate, not a genuinely repeated fact worth stating identically
    every time. This varies the WORDING of a repeated phrase across
    DIFFERENT entries only (never within the same entry - that's Bullet
    Compression's job, see merge_near_duplicate_bullets) using the small,
    curated _GENERIC_PHRASE_VARIANTS table: the FIRST entry to use a given
    phrase keeps its original wording completely untouched; every
    SUBSEQUENT entry's occurrence of that SAME phrase is swapped for a
    different, meaning-preserving synonym from the same table (never
    re-using the literal original wording, even if the phrase repeats
    across 3+ entries) - purely a synonym substitution for an already-
    generic phrase, never a fact/technology/metric change, so nothing is
    invented and nothing true becomes false. Returns the number of
    bullets changed.
    """
    changed = 0
    for phrase_key, variants in _GENERIC_PHRASE_VARIANTS.items():
        pattern = _phrase_pattern(phrase_key)
        matching_entry_indices = []
        for entry_index, entry in enumerate(entries):
            bullets_key = "points" if "points" in entry else "responsibilities"
            bullets = entry.get(bullets_key) or []
            if any(pattern.search(b) for b in bullets):
                matching_entry_indices.append(entry_index)

        if len(matching_entry_indices) < 2:
            continue  # used in at most one entry - nothing repeated to vary

        rotation_pool = variants[1:] or variants  # never re-select the literal original wording
        for occurrence, entry_index in enumerate(matching_entry_indices):
            if occurrence == 0:
                continue  # first entry to use this phrase keeps its original wording
            entry = entries[entry_index]
            bullets_key = "points" if "points" in entry else "responsibilities"
            bullets = entry.get(bullets_key) or []
            variant = rotation_pool[(occurrence - 1) % len(rotation_pool)]
            new_bullets = list(bullets)
            for i, bullet in enumerate(new_bullets):
                if pattern.search(bullet):
                    # Only the first matching bullet in this entry - a
                    # second repeat WITHIN the same entry is Bullet
                    # Compression's job (merge_near_duplicate_bullets),
                    # not this cross-entry stage's.
                    new_bullets[i] = pattern.sub(variant, bullet, count=1)
                    changed += 1
                    break
            entry[bullets_key] = new_bullets

    return changed


# --- STEP 9b: Deduplication (cross-entry, ACTS - not just log-only) ---------

_CROSS_JOB_DUPLICATE_THRESHOLD = 0.80  # full-sentence comparison across different employers, more
                                        # permissive than within-entry's 0.85 since the goal here is
                                        # rewriting/dropping the visual duplicate, not a stricter merge


def deduplicate_cross_entry_bullets(entries: list[dict], bullets_key: str = "points") -> int:
    """True cross-company Experience Deduplication - unlike detect_cross_
    entry_repetition (log-only, above), this ACTS on a near-duplicate
    bullet repeated across DIFFERENT entries (different employers/projects
    - within-entry duplicates are Bullet Compression's job, see
    merge_near_duplicate_bullets). Entries are walked in their given order
    (most-recent-first, the same convention _recency_adjusted_bullet_cap
    already relies on), so the FIRST (most recent/strongest) entry to use a
    given responsibility always keeps its original wording untouched - a
    recruiter reading top to bottom sees the real version first.

    Every later entry's near-duplicate (SequenceMatcher ratio >=
    _CROSS_JOB_DUPLICATE_THRESHOLD against ANY bullet already kept, in ANY
    earlier entry) is handled two ways, in order:

      1. Reworded by swapping its opening verb for one not yet used
         ANYWHERE else in the resume (same mechanism diversify_action_verbs
         already uses within one job - see _RECOGNIZED_ACTION_VERBS/
         _swap_first_word - just reserved resume-wide here instead of per-
         job). Every noun, technology, and metric in the sentence is
         untouched, so this can never invent or change a fact. Only kept if
         the verb swap actually drops the similarity below the threshold.
      2. If a verb swap alone can't make the two bullets read as genuinely
         different responsibilities (identical past the opening verb, or no
         recognized opening verb to swap at all), "keep only the strongest
         version" - the same conservative reasoning merge_near_duplicate_
         bullets already applies within one entry, just applied across
         entries: the duplicate is dropped. Never drops an entry's LAST
         surviving bullet - a company is never left with an empty
         Experience/Project entry just because every one of its bullets
         happened to duplicate an earlier, stronger entry.

    Never touches career-break entries. Returns the number of bullets
    changed (reworded) or dropped."""
    changed = 0
    kept_bullets: list[str] = []  # every bullet already kept, across ALL earlier entries, in order
    used_verbs: set[str] = set()

    for entry in entries:
        if entry.get("is_career_break"):
            continue
        bullets = entry.get(bullets_key) or []
        if not bullets:
            continue

        decisions: list[tuple[str, str, str | None, float]] = []
        for bullet in bullets:
            opener = _first_word(bullet)
            if opener in _RECOGNIZED_ACTION_VERBS:
                used_verbs.add(opener)

            match, best_ratio = None, 0.0
            for existing in kept_bullets:
                ratio = difflib.SequenceMatcher(None, bullet.lower(), existing.lower()).ratio()
                if ratio >= _CROSS_JOB_DUPLICATE_THRESHOLD and ratio > best_ratio:
                    match, best_ratio = existing, ratio

            if match is None:
                decisions.append(("keep", bullet, None, 0.0))
                kept_bullets.append(bullet)
                continue

            rewritten = None
            if opener in _RECOGNIZED_ACTION_VERBS:
                for candidate in sorted(_RECOGNIZED_ACTION_VERBS):
                    if candidate in used_verbs:
                        continue
                    candidate_bullet = _swap_first_word(bullet, candidate.capitalize())
                    candidate_ratio = difflib.SequenceMatcher(
                        None, candidate_bullet.lower(), match.lower()
                    ).ratio()
                    if candidate_ratio < _CROSS_JOB_DUPLICATE_THRESHOLD:
                        rewritten = candidate_bullet
                        used_verbs.add(candidate)
                        break

            if rewritten:
                decisions.append(("rewrite", rewritten, match, best_ratio))
                kept_bullets.append(rewritten)
            else:
                decisions.append(("drop", bullet, match, best_ratio))

        # Never leave an entry with zero bullets - if every bullet in this
        # entry was marked "drop", keep the first one as-is instead. A
        # real, if repeated, responsibility beats an empty entry.
        if decisions and all(kind == "drop" for kind, *_ in decisions):
            kind, bullet, match, ratio = decisions[0]
            decisions[0] = ("keep", bullet, match, ratio)
            kept_bullets.append(bullet)

        label = entry.get("company") or entry.get("title") or "(unnamed entry)"
        new_bullets: list[str] = []
        for kind, bullet, match, ratio in decisions:
            if kind == "keep":
                new_bullets.append(bullet)
            elif kind == "rewrite":
                logger.info(
                    "Experience Intelligence Engine: reworded cross-company duplicate bullet -> %r "
                    "(was %.2f similar to an earlier entry's %r).", bullet, ratio, match,
                )
                new_bullets.append(bullet)
                changed += 1
            else:  # drop
                logger.info(
                    "Experience Intelligence Engine: dropped cross-company duplicate bullet %r "
                    "(%.2f similar to an earlier entry's %r) - %r still communicates unique value.",
                    bullet, ratio, match, label,
                )
                changed += 1
        entry[bullets_key] = new_bullets

    return changed


# --- Experience/Projects overlap removal (this round's Priority 1 fix) ----
#
# Root cause: extraction sometimes splits ONE job into several "experience"
# rows - one per client project worked on there - each stamped with the
# same company and either an empty role or the project's own name in the
# role field, while ALSO correctly capturing that same project under
# Projects Handled. The result is a Professional Experience section that
# repeats, near-verbatim, what Projects Handled already states (confirmed
# on a real resume: a "Trelleborg Sealing Solutions, Bangalore" experience
# entry with role "Digitalized Closing File" and 4 bullets that are the
# same content, just reworded, as the "Digitalized Closing File" project's
# own responsibilities). This can't be fixed at extraction (out of scope
# this round) - only removed downstream once both lists are assembled.

_OVERLAP_CONTENT_THRESHOLD = 0.55  # comparing a whole bullet BLOCK against
                                    # a whole project BLOCK, not one bullet
                                    # against another - phrasing differs
                                    # slightly between the two sections, so
                                    # this is deliberately looser than the
                                    # single-bullet merge thresholds above


def _company_key(company: str) -> str:
    """The company name's first comma-segment (drops a ", City" suffix),
    normalized - "Trelleborg Sealing Solutions" and "Trelleborg Sealing
    Solutions, Bangalore" must resolve to the same employer for overlap
    detection to work at all, since extraction inconsistently appends a
    city to some rows but not others for the exact same job."""
    return re.sub(r"\s+", " ", (company or "").split(",")[0]).strip().lower()


def _companies_overlap(a: str, b: str) -> bool:
    key_a, key_b = _company_key(a), _company_key(b)
    if not key_a or not key_b:
        return False
    return key_a == key_b or key_a in key_b or key_b in key_a


def _project_signatures(projects: list[dict]) -> list[tuple[str, str]]:
    """(lowercased title, lowercased title+responsibilities text) per
    project - used both for an exact role==title check and a fuzzy
    content-similarity check."""
    return [
        (
            str(proj.get("title") or "").strip().lower(),
            " ".join([str(proj.get("title") or "")] + (proj.get("responsibilities") or [])).lower(),
        )
        for proj in projects
    ]


def _role_is_project_shaped(role: str, project_signatures: list[tuple[str, str]]) -> bool:
    role_lower = role.strip().lower()
    return bool(role_lower) and any(role_lower == title for title, _text in project_signatures)


def _duplicates_a_project(exp: dict, project_signatures: list[tuple[str, str]]) -> bool:
    """True only if this entry's OWN content is verified to already exist
    in Projects Handled - either its role IS a project's title outright,
    or its bullet text is a near-duplicate (whole-block SequenceMatcher)
    of some project's title+responsibilities. An entry with no matching
    project is never considered a duplicate, however empty its role -
    removing it would risk losing real, un-preserved information."""
    role = (exp.get("role") or "").strip().lower()
    bullet_text = " ".join(exp.get("points") or []).strip().lower()
    for title, proj_text in project_signatures:
        if role and title and role == title:
            return True
        if bullet_text and proj_text:
            ratio = difflib.SequenceMatcher(None, bullet_text, proj_text).ratio()
            if ratio >= _OVERLAP_CONTENT_THRESHOLD:
                return True
    return False


def remove_experience_project_overlap(experience: list[dict], projects: list[dict]) -> tuple[list[dict], int]:
    """Removes an Experience entry that is really the SAME engagement
    already captured in Projects Handled, not a distinct employer/role.
    Detection requires ALL of:
      1. The entry's company matches an already-established GENUINE job's
         company (a company's first-seen entry with a real, non-project-
         shaped role - e.g. "Senior Solutions Architect" - is always
         treated as genuine and is itself never removed).
      2. Its role is empty OR IS a project's title outright.
      3. Its bullet content is verified to already exist in Projects
         Handled (see _duplicates_a_project) - an entry whose content has
         NO matching project is always kept, even if 1-2 hold, since
         removing it unverified would be a real information loss.
    Never touches career-break entries. Returns (cleaned_experience,
    removed_count)."""
    project_signatures = _project_signatures(projects)

    genuine_companies: list[str] = []
    seen_keys: set[str] = set()
    for exp in experience:
        if exp.get("is_career_break"):
            continue
        company = exp.get("company") or ""
        key = _company_key(company)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        role = (exp.get("role") or "").strip()
        if role and not _role_is_project_shaped(role, project_signatures):
            genuine_companies.append(company)

    cleaned: list[dict] = []
    removed = 0
    for exp in experience:
        if exp.get("is_career_break"):
            cleaned.append(exp)
            continue
        role = (exp.get("role") or "").strip()
        is_genuine_record = bool(role) and not _role_is_project_shaped(role, project_signatures)
        company = exp.get("company") or ""
        if (
            not is_genuine_record
            and any(_companies_overlap(company, g) for g in genuine_companies)
            and _duplicates_a_project(exp, project_signatures)
        ):
            removed += 1
            logger.info(
                "Experience Intelligence Engine: removed Experience entry for %r (role=%r, %s) - its "
                "content duplicates a Projects Handled entry already covering the same engagement.",
                company, role or "(no role)", exp.get("duration") or "unknown dates",
            )
            continue
        cleaned.append(exp)
    return cleaned, removed


# --- Career Break genuine-evidence gate (this round's Priority 4 fix) ------
#
# Root cause: a "Career Break" entry was rendered any time is_career_break
# was set, regardless of how short the gap was or whether any reason was
# ever stated - inferring an "employment gap" narrative from bare date
# arithmetic alone, with zero actual evidence, is not materially different
# from stating something unverified as fact. Confirmed on a real resume:
# two "Career Break" rows, each spanning exactly 2 months between two
# listed jobs, both with an empty break_detail and a placeholder bullet
# ("Period between employment roles.") - a normal notice-period/transition
# gap, not a break worth its own resume line.
_MIN_GENUINE_CAREER_BREAK_MONTHS = 4

_MONTH_NAMES: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
# `['‘’]?` (straight AND curly apostrophe), not just `'?` - a real resume's
# "Aug'23"-style shorthand PDF-extracts with a curly right-single-quote
# (U+2019), not a straight ASCII apostrophe (confirmed on a real candidate's
# resume - "Aug’23"), which the plain `'?` alone silently never matched.
_MONTH_YEAR_TOKEN_RE = re.compile(r"([A-Za-z]{3,9})\.?\s*['‘’]?\s*(\d{2,4})")
_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)


def _parse_month_year_token(text: str) -> tuple[int, int] | None:
    """Parses the FIRST recognizable 'Month Year' / "Mon'YY" token in
    `text` into (year, month) - returns None if nothing parses; never
    guesses a date from ambiguous text."""
    match = _MONTH_YEAR_TOKEN_RE.search(text)
    if not match:
        return None
    month = _MONTH_NAMES.get(match.group(1).lower())
    if month is None:
        return None
    year = int(match.group(2))
    if year < 100:
        year += 2000
    return (year, month)


def _duration_span_months(duration: str) -> int | None:
    """Whole-month span of a 'Start - End' duration string, inclusive of
    both ends - or None if either end can't be confidently parsed. Used
    ONLY to judge whether an inferred gap is substantial enough to be
    genuine evidence of a career break, never anywhere dates are rendered."""
    if not duration:
        return None
    parts = re.split(r"\s*[-–—]\s*", duration, maxsplit=1)
    if len(parts) != 2:
        return None
    start_text, end_text = parts[0].strip(), parts[1].strip()
    start = _parse_month_year_token(start_text)
    if start is None:
        return None
    if _PRESENT_RE.search(end_text):
        today = datetime.date.today()
        end = (today.year, today.month)
    else:
        end = _parse_month_year_token(end_text)
    if end is None:
        return None
    return (end[0] * 12 + end[1]) - (start[0] * 12 + start[1]) + 1


def is_genuine_career_break(exp: dict) -> bool:
    """A Career Break entry is genuine only if there's real evidence for
    it: either an explicit stated reason (`break_detail`), or a gap of at
    least `_MIN_GENUINE_CAREER_BREAK_MONTHS` months - a 1-2 month gap
    between two listed jobs is normal transition time (notice period,
    relocation, onboarding lag), not a break worth calling out on its own.
    An unparseable duration is treated as genuine (can't disprove a stated
    break, so it's never silently hidden)."""
    if not exp.get("is_career_break"):
        return False
    if str(exp.get("break_detail") or "").strip():
        return True
    span = _duration_span_months(str(exp.get("duration") or ""))
    if span is None:
        return True
    return span >= _MIN_GENUINE_CAREER_BREAK_MONTHS


def filter_non_genuine_career_breaks(experience: list[dict]) -> tuple[list[dict], int]:
    """Drops an is_career_break entry that doesn't meet is_genuine_career_
    break's bar. Never touches a real job entry. Returns (cleaned_
    experience, removed_count)."""
    cleaned: list[dict] = []
    removed = 0
    for exp in experience:
        if exp.get("is_career_break") and not is_genuine_career_break(exp):
            removed += 1
            logger.info(
                "Experience Intelligence Engine: removed a non-genuine Career Break entry (%s, no "
                "stated reason and under %d months) - a short unexplained gap between two listed "
                "roles is normal transition time, not a break worth its own resume line.",
                exp.get("duration") or "unknown dates", _MIN_GENUINE_CAREER_BREAK_MONTHS,
            )
            continue
        cleaned.append(exp)
    return cleaned, removed


# --- STEP 10: Quality Validation (detect + log, never silently rewrite) -----

_WEAK_VERB_STARTS = ("worked", "responsible for", "handled", "helped", "participated", "was involved", "assisted")
_VERBOSE_WORD_COUNT = 40


def _quality_validate_entry(label: str, bullets: list[str]) -> None:
    openers = [_first_word(b) for b in bullets if b.strip()]
    repeated = {o for o in openers if o and openers.count(o) > 1}
    if repeated:
        logger.warning("Experience Intelligence Engine quality check: %r still has repeated opening verb(s) "
                        "after diversification: %r", label, repeated)

    weak = [b for b in bullets if b.strip().lower().startswith(_WEAK_VERB_STARTS)]
    if weak:
        logger.warning("Experience Intelligence Engine quality check: %r has weak-verb bullet(s): %r", label, weak)

    verbose = [b for b in bullets if len(b.split()) > _VERBOSE_WORD_COUNT]
    if verbose:
        logger.warning("Experience Intelligence Engine quality check: %r has overly verbose bullet(s) "
                        "(>%d words): %r", label, _VERBOSE_WORD_COUNT, verbose)

    missing_impact = [b for b in bullets if not has_impact_signal(b)]
    if missing_impact:
        logger.info("Experience Intelligence Engine quality check: %r has %d bullet(s) with no stated "
                     "metric/outcome - consider whether the source material has one to surface.",
                     label, len(missing_impact))


# --- STEP 1: Resume Compression (recency-weighted bullet allocation) -------
#
# Root cause this addresses: bullet_range_for_role's own tier (standard/
# lead/executive) is based ONLY on job TITLE, so a candidate's very first
# job 15 years ago gets the exact same bullet budget as their current role
# if the titles happen to fall in the same tier - producing an equally-
# detailed wall of bullets for career-history depth that a recruiter skims
# in two seconds. This narrows the tier's own MAXIMUM (never its minimum,
# and never below a 2-bullet floor - a job is summarized, never erased)
# for jobs further back in the candidate's OWN listed order - resumes list
# jobs most-recent-first by convention throughout this pipeline (same
# assumption ats_intelligence.py's ranking and resume_structuring.py's
# seniority detection already make). The two most recent jobs are NEVER
# reduced below their tier's own maximum - only earlier jobs progressively
# tighten, exactly "prioritize recent experience" / "summarize older
# experience" / "compress junior-level work" without touching
# bullet_range_for_role's own tiering (Phase 1, never redefined here).
#
# Page-budget-aware compression: the SAME years-of-experience bracket that
# decides a candidate's page cap (file_generator._max_pages_allowed - under
# 5y->2 pages, 5-10y->3, 10-15y->4, 15+y->5) also decides how many of the
# candidate's most-recent jobs are exempt from compression before the
# per-job taper starts - a candidate with a bigger page budget genuinely
# has more room to keep older jobs detailed, so their compression should
# kick in later and taper more gently than a 2-page candidate's. Reusing
# `page_budget_for_years` here (not a separately-tuned constant) is what
# keeps "compress older experience / expand recent experience" scaled to
# the SAME bracket the rendered page cap uses, rather than two
# independently-guessed numbers that could drift apart.
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# Real resumes commonly abbreviate a job's dates as "Aug'23"/"Nov'20"
# rather than a full 4-digit year - confirmed on a real candidate's resume
# where EVERY genuine job's duration used this shorthand ("Aug'23 - till
# date", "Apr'08 - Mar'14", ...), which _YEAR_RE alone can't see at all,
# computing 0 years of experience for a candidate with a real 16+ year
# career. Matches straight AND curly apostrophe - a real resume's "Aug'23"
# PDF-extracts with a curly right-single-quote (U+2019), not a straight
# ASCII apostrophe, which a plain `'` alone silently never matched. Bare
# "'23" is ambiguous only in the 1900s-vs-2000s sense - always resolved as
# 20XX here, a safe assumption for any resume a person is actively using
# today.
_APOSTROPHE_YEAR_RE = re.compile(r"['‘’](\d{2})\b")

# (upper bound, page budget) - lower-bound inclusive, same reasoning and
# same numbers as file_generator.py's own _PAGE_BUDGET_BRACKETS (kept as
# an independent, self-contained reimplementation on purpose - see module
# docstring's "no cross-module private imports" convention - so if these
# two ever need to change, change both).
_PAGE_BUDGET_BRACKETS: tuple[tuple[float, int], ...] = ((5, 2), (10, 3), (15, 4))
_MAX_PAGE_BUDGET = 5


def years_of_experience(experience: list[dict] | None) -> float:
    """Estimated career span in years, from the years mentioned in each
    job's `duration` string (full 4-digit years AND "Mon'YY" shorthand -
    see _APOSTROPHE_YEAR_RE) - same approach (and, deliberately, the same
    rough free-text-driven estimate) as file_generator._years_of_experience,
    reimplemented locally rather than imported, so this module has no
    cross-module dependency on the renderer. Covers the whole calendar
    span including career-break entries, not just active working years."""
    current_year = datetime.date.today().year
    years: list[int] = []
    for exp in experience or []:
        duration = str(exp.get("duration") or "")
        if _PRESENT_RE.search(duration):
            years.append(current_year)
        years.extend(int(y) for y in _YEAR_RE.findall(duration))
        years.extend(2000 + int(y) for y in _APOSTROPHE_YEAR_RE.findall(duration))
    if not years:
        return 0.0
    return float(max(years) - min(years))


def page_budget_for_years(years: float) -> int:
    """Page cap for `years` of experience - see _PAGE_BUDGET_BRACKETS."""
    for threshold, pages in _PAGE_BUDGET_BRACKETS:
        if years < threshold:
            return pages
    return _MAX_PAGE_BUDGET


def max_projects_for_years(years: float) -> int:
    """Projects Handled cap, scaled to the SAME page-budget bracket
    (page_budget_for_years) - one more project per page tier above the
    2-page floor (2 pages->3 projects, 3->4, 4->5, 5->6), so a candidate
    with more page budget also gets room to show more of their strongest
    projects, not just more experience detail. 6 (the 5-page/most-senior
    tier's result) is the same MAX_PROJECTS ceiling already established
    and tuned elsewhere in this module - this never raises it further."""
    return page_budget_for_years(years) + 1


def _recency_adjusted_bullet_cap(base_max: int, job_index: int, total_jobs: int, max_pages: int = 2) -> int:
    """`max_pages` (default 2 - reproduces the EXACT prior behavior for any
    caller that doesn't pass one) sets how many of the candidate's most
    recent jobs are exempt from compression: `max_pages` itself, since a
    2-page candidate's grace period was always 2 jobs before this
    parameter existed. A 5-page candidate's 5 most recent jobs all keep
    their full bullet count; only the 6th job back and beyond starts
    tapering - the same per-job reduction shape as before (1 bullet lost
    per job past the grace period, never below a 2-bullet floor), just
    starting later for a candidate with more room to spend."""
    grace_jobs = max(max_pages, 2)
    if total_jobs <= 2 or job_index < grace_jobs:
        return base_max
    reduction = job_index - (grace_jobs - 1)
    return max(base_max - reduction, 2)


# --- STEP 2: Project Selection (relevance-based ranking) --------------------
#
# Ranking factors, each a keyword-presence check purely over the project's
# OWN text (title/description/technologies/responsibilities) - never
# external data. Recency is deliberately NOT scored: unlike Experience
# (which has a "duration" field this pipeline already relies on
# elsewhere), a project entry in this schema has no date field at all -
# scoring "recent work" here would mean inventing a signal that doesn't
# exist, which this module treats as out of bounds the same way it treats
# inventing a metric.
_ARCHITECTURE_KEYWORDS = (
    "architecture", "architected", "system design", "solution design",
    "microservices", "solution architecture", "enterprise solution",
)
_LEADERSHIP_KEYWORDS = ("led", "directed", "mentored", "managed a team", "team of")
_CLOUD_TECH_KEYWORDS = ("azure", "aws", "gcp", "cloud")
_MODERN_TECH_KEYWORDS = (
    "kubernetes", "docker", "machine learning", "artificial intelligence",
    "serverless", "microservices", "devops", "ci/cd",
)
_ENTERPRISE_SCALE_KEYWORDS = ("enterprise", "global", "large-scale", "multi-region", "high availability", "high-availability")
_CLIENT_VISIBILITY_KEYWORDS = ("client", "customer", "stakeholder", "presales", "pre-sales")

_PROJECT_SCORE_WEIGHTS = {
    "architecture": 3, "business_impact": 3, "leadership": 2, "cloud": 2,
    "enterprise_scale": 2, "modern_tech": 1, "client_visibility": 1,
    "industry_alignment": 2,
}

# Recruiter guidance: a scannable Projects section shows the strongest
# handful of projects, not every project ever worked on - see
# rank_and_select_projects(). Deliberately generous (never remove
# important accomplishments) - only trims when there are genuinely MORE
# projects than this to begin with.
MAX_PROJECTS = 6


def _project_text(proj: dict) -> str:
    return " ".join([
        str(proj.get("title") or ""), str(proj.get("description") or ""),
        str(proj.get("technologies") or ""), " ".join(proj.get("responsibilities") or []),
    ]).lower()


def score_project_relevance(proj: dict, industry_keywords: tuple[str, ...] = ()) -> int:
    """STEP 2 - Project Selection. Scores one project by architecture
    complexity, business impact (has_impact_signal on any bullet -
    metrics/outcomes ALREADY stated, never invented), leadership, cloud
    technology presence, enterprise scale, modern technology presence, and
    client visibility - each a keyword-presence signal over the project's
    own text. Higher score = stronger candidate for keeping when trimming
    (see rank_and_select_projects); never used to alter the project's
    content, only its survival/ordering.

    `industry_keywords` (industry-specific templates) is OPTIONAL (default
    `()`, a no-op for every caller before this parameter existed) - the
    classified industry's own priority keywords (see industry_
    intelligence.py); a project whose own text mentions any of them gets a
    modest bonus, so an SAP candidate's SAP-module project or a Data
    Engineering candidate's ETL-pipeline project ranks ahead of an
    otherwise-equal, less domain-relevant one."""
    text = _project_text(proj)
    score = 0
    if any(kw in text for kw in _ARCHITECTURE_KEYWORDS):
        score += _PROJECT_SCORE_WEIGHTS["architecture"]
    if any(has_impact_signal(b) for b in proj.get("responsibilities") or []):
        score += _PROJECT_SCORE_WEIGHTS["business_impact"]
    if any(kw in text for kw in _LEADERSHIP_KEYWORDS):
        score += _PROJECT_SCORE_WEIGHTS["leadership"]
    if any(kw in text for kw in _CLOUD_TECH_KEYWORDS):
        score += _PROJECT_SCORE_WEIGHTS["cloud"]
    if any(kw in text for kw in _ENTERPRISE_SCALE_KEYWORDS):
        score += _PROJECT_SCORE_WEIGHTS["enterprise_scale"]
    if any(kw in text for kw in _MODERN_TECH_KEYWORDS):
        score += _PROJECT_SCORE_WEIGHTS["modern_tech"]
    if any(kw in text for kw in _CLIENT_VISIBILITY_KEYWORDS):
        score += _PROJECT_SCORE_WEIGHTS["client_visibility"]
    if industry_keywords and any(kw.lower() in text for kw in industry_keywords):
        score += _PROJECT_SCORE_WEIGHTS["industry_alignment"]
    return score


def rank_and_select_projects(
    projects: list[dict], max_projects: int = MAX_PROJECTS, industry_keywords: tuple[str, ...] = (),
) -> list[dict]:
    """STEP 2 - Project Selection. Keeps the `max_projects` highest-scoring
    projects (see score_project_relevance) - a no-op (returns `projects`
    completely unchanged, same objects, same order) if there aren't more
    than `max_projects` to begin with, which is the common case; a
    candidate's project list is only ever trimmed when there are genuinely
    MORE projects than a recruiter would scan. Kept projects are returned
    in their ORIGINAL relative order (never reordered by score - a resume
    reading its projects out of the order the candidate listed them would
    look stranger than the compression is worth), and ties are broken by
    that same original order (stable sort). Never rewrites a kept
    project's content. `industry_keywords` defaults to `()` (no-op) - see
    score_project_relevance's own docstring."""
    if len(projects) <= max_projects:
        return projects
    scored = sorted(
        enumerate(projects), key=lambda pair: (-score_project_relevance(pair[1], industry_keywords), pair[0])
    )
    kept_indices = {index for index, _proj in scored[:max_projects]}
    dropped = len(projects) - len(kept_indices)
    logger.info(
        "Experience Intelligence Engine: kept the %d strongest of %d projects by relevance "
        "(architecture/business-impact/leadership/cloud/scale) - %d older/lower-relevance "
        "project(s) summarized out of the rendered Projects section.",
        max_projects, len(projects), dropped,
    )
    return [proj for index, proj in enumerate(projects) if index in kept_indices]


# --- Orchestration ------------------------------------------------------------

def _enhance_bullets(bullets: list[str], theme: str, min_bullets: int, max_bullets: int) -> list[str]:
    bullets = merge_near_duplicate_bullets(bullets)
    bullets = replace_weak_verb_openers(bullets)
    bullets = diversify_action_verbs(bullets, theme)
    bullets = prioritize_bullets_with_impact(bullets)
    bullets = enforce_bullet_cap(bullets, min_bullets, max_bullets)
    return bullets


def enhance_job_experience(exp: dict, job_index: int = 0, total_jobs: int = 1, max_pages: int = 2) -> dict:
    """Runs the full pipeline over one already-refined job entry's
    "points". Career-break entries are passed through untouched - there's
    no responsibility list to classify/diversify/compress for one, matching
    experience_refiner.py's own treatment of career breaks. `job_index`/
    `total_jobs`/`max_pages` (all default to values that reproduce the
    exact prior behavior for any direct caller not specifying them) drive
    STEP 1's recency-weighted bullet cap - see _recency_adjusted_bullet_cap."""
    if exp.get("is_career_break"):
        return dict(exp)

    points = exp.get("points") or []
    if not points:
        return dict(exp)

    theme = classify_project_theme(points)
    min_bullets, max_bullets = bullet_range_for_role(exp.get("role"))
    max_bullets = _recency_adjusted_bullet_cap(max_bullets, job_index, total_jobs, max_pages)
    min_bullets = min(min_bullets, max_bullets)
    enhanced = dict(exp)
    enhanced["points"] = _enhance_bullets(points, theme, min_bullets, max_bullets)
    _quality_validate_entry(exp.get("company") or "(no company)", enhanced["points"])
    return enhanced


def enhance_experience(experience: list[dict], max_pages: int | None = None) -> list[dict]:
    """Public entry point - runs enhance_job_experience over every job
    (passing each job's position/total for STEP 1's recency-weighted
    compression - resumes list jobs most-recent-first by convention), then
    a cross-entry repetition check (log-only) over the fully-enhanced
    list. Returns a new list, one entry per input job, in the same order -
    never adds, removes, or reorders JOBS, only each job's own bullets.
    `max_pages` (the candidate's page budget - see page_budget_for_years)
    defaults to None, meaning "compute it from this same `experience`
    list" (self-sufficient default for any direct caller/test that doesn't
    pass one) - resume_optimizer.py's real call passes the ALREADY-
    computed budget explicitly instead, since it's derived from the same
    cleaned job list this function receives anyway."""
    if not experience:
        return []
    if max_pages is None:
        max_pages = page_budget_for_years(years_of_experience(experience))
    total_jobs = len(experience)
    enhanced = [enhance_job_experience(exp, index, total_jobs, max_pages) for index, exp in enumerate(experience)]
    detect_cross_entry_repetition(enhanced)
    deduplicate_cross_entry_bullets(enhanced, "points")
    return enhanced


def enhance_project(proj: dict) -> dict:
    """Same pipeline as enhance_job_experience, applied to a project's
    "responsibilities" with the fixed (3, 5) project bullet range (STEP 6) -
    reusing Phase 1's own established project cap, never redefined
    differently."""
    responsibilities = proj.get("responsibilities") or []
    if not responsibilities:
        return dict(proj)

    theme = classify_project_theme(responsibilities)
    min_bullets, max_bullets = _PROJECT_BULLET_RANGE
    enhanced = dict(proj)
    enhanced["responsibilities"] = _enhance_bullets(responsibilities, theme, min_bullets, max_bullets)
    _quality_validate_entry(proj.get("title") or "(untitled project)", enhanced["responsibilities"])
    return enhanced


def enhance_projects(projects: list[dict]) -> list[dict]:
    """Public entry point for Projects - mirrors enhance_experience()."""
    if not projects:
        return []
    enhanced = [enhance_project(proj) for proj in projects]
    detect_cross_entry_repetition(enhanced)
    deduplicate_cross_entry_bullets(enhanced, "responsibilities")
    return enhanced
