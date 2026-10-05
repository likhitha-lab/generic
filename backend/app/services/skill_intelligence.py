"""Skill Intelligence Engine - Phase 2 of the Resume Intelligence Engine.

Runs AFTER Stage 1 extraction/normalization and AFTER resume_optimizer.py's
own Gemini-based (or keyword-fallback) skills categorization, and BEFORE
the Resume Builder ever sees a "skills" value - see build_technical_skills(),
this module's one public entry point, wired into resume_optimizer.py.

WHY THIS EXISTS (see the accompanying architecture review for the full
analysis): Gemini is good at RECOGNIZING skills but inconsistent at naming/
deduping/prioritizing them the same way every time - the same candidate's
resume can produce "React" one run and "ReactJS" the next, "AKS" one run and
"Azure Kubernetes Service" the next, and an occasional business term or
certification title that happens to contain a real keyword substring (e.g.
"AWS Certified Solutions Architect" contains "aws"). Because Gemini is a
non-deterministic dependency, the ONLY way to guarantee the same input
always produces the same, consistently-named, correctly-ranked Technical
Skills section is a deterministic pass that runs regardless of whether the
candidate list came from Gemini's categorization or the keyword-based
fallback (skill_categorizer.py) - "regardless of source".

PIPELINE (each stage is its own small, pure, independently-testable
function - given as private functions below, run in this fixed order by
build_technical_skills()):

    Raw Skills
      -> Cleaning              (_clean)
      -> Normalization          (_normalize)
      -> Alias Resolution       (_resolve_aliases)
      -> Duplicate Detection    (_detect_duplicates)
      -> Related-Skill Consolidation (_consolidate_related_skills - version-
                                 variant folding + parent/child grouping;
                                 a different kind of redundancy than
                                 Duplicate Detection's string-similarity
                                 merge can catch - see that function's own
                                 docstring)
      -> Skill Classification   (_classify_and_filter - a total partition:
                                 every item is classified into a real
                                 category or the "Other Skills" catch-all,
                                 never dropped)
      -> Skill Ranking          (_rank)
      -> ATS Standardization    (already achieved by Normalization/Alias
                                 Resolution choosing ATS-conventional
                                 canonical names - see that section below)
      -> Quality Validation     (_quality_validate)
      -> Final Technical Skills (uncapped by default - every genuine skill
                                 is returned; a caller may still pass an
                                 explicit `max_items` if it wants one)

Design note on Skill Classification (formerly "Noise Removal + Skill
Classification"): every surviving item is classified into a category, used
both for ranking (always) and as a display heading when the caller renders
the "Other Skills"-aware grouped form (see build_technical_skills_grouped).
This used to be an INCLUSION whitelist that silently EXCLUDED anything
matching no known technical category (a real, confirmed defect - a
candidate's genuine but unusual skill, or one this module's keyword
dictionaries simply don't name, would vanish from the resume entirely with
no trace). It is now a total partition instead: every item classifies into
either a named category or the "Other Skills" catch-all - nothing is ever
dropped at this stage. The _clean() stage still runs first (see below) to
catch the narrower, different problem of a non-skill string (a
certification title, a degree line) that happens to contain a real keyword
substring.

Deliberately self-contained: this module does not import from
skill_categorizer.py/tool_classifier.py/normalization.py, even though some
keyword lists and helpers necessarily overlap in spirit with those modules.
Those modules keep serving their own existing callers unchanged (see
resume_optimizer.py - skill_categorizer.py's categorize_skills() is still
the deterministic fallback used INSIDE _optimize_skills_and_tools when
Gemini itself fails); this engine is the new, single, final authority that
runs afterward on top of whatever either path produced, so it cannot
depend on either without creating a fragile circular relationship between
"the fallback" and "the thing that reprocesses the fallback's own output".
"""
import difflib
import logging
import re

logger = logging.getLogger(__name__)

# Recruiter guidance: a scannable Technical Skills section lists roughly
# 20-30 items.
MAX_TECHNICAL_SKILLS = 30


# --- Stage 1: Cleaning -------------------------------------------------------
#
# Root cause this addresses: certification titles, degree/qualification
# lines, and full responsibility sentences occasionally land in the raw
# "skills" list (Stage 1 extraction is supposed to keep these in their own
# fields, but an LLM extraction pass is not perfectly reliable about it) -
# and a certification title like "AWS Certified Solutions Architect -
# Professional" genuinely CONTAINS the keyword "aws", so a naive keyword-
# match classifier alone would wrongly keep it as a "Cloud" skill. This
# stage rejects anything that reads like one of those BEFORE any keyword
# matching ever runs, regardless of what substrings it happens to contain.
_NON_SKILL_INDICATORS = (
    "certified", "certification", "certificate", "bachelor", "master of",
    "b.tech", "m.tech", "b.sc", "m.sc", "university", "college", "diploma",
)


def _looks_like_non_skill(text: str) -> bool:
    lowered = text.lower()
    if any(indicator in lowered for indicator in _NON_SKILL_INDICATORS):
        return True
    # A genuine skill name is a short phrase (a language/framework/platform
    # name) - never a full sentence, certification title, or project
    # description. More than 5 words is a strong signal it's one of those.
    return len(text.split()) > 5


# Technical Skill Eligibility (Technical Skills presentation refinement):
# RESPONSIBILITY/ACTIVITY phrases that read grammatically like a skill -
# short, not a sentence, so _looks_like_non_skill's own certification-
# keyword/>5-word checks don't catch them - but name WORK PERFORMED
# ("Requirements Gathering", "Client Interaction") rather than a
# technology, tool, platform, or practice. These belong in Professional
# Experience, never Technical Skills - a small, explicit,
# evidence-driven set (the exact confirmed examples, plus their obvious
# singular/plural/hyphenation variants), matched as a WHOLE, NORMALIZED
# PHRASE, never a substring - so a genuine technology that happens to
# share a word with one of these (there are none among current
# categories, but the discipline matters) can never be caught by
# accident. This is deliberately NOT an inclusion whitelist (a candidate's
# genuine-but-unusual technology must still survive into "Other Skills" -
# see this module's own docstring on why an inclusion whitelist was
# already tried and rejected as a real, confirmed defect) - only these
# specific, confirmed non-skill phrases are excluded.
_RESPONSIBILITY_PHRASES: frozenset[str] = frozenset({
    "requirements gathering", "client interaction", "internal review", "internal reviews",
    "external review", "external reviews", "knowledge transfer", "story prioritization",
    "walk-through", "walk-throughs", "walkthrough", "walkthroughs",
    "business needs understanding", "bug detection", "bug addressing", "bug fixing",
    "test data generation", "documentation discussion", "documentation discussions",
    # Technical Skills Curation V2 - additional confirmed examples, same
    # class as the original set above (short noun phrases naming work
    # performed, not a technology).
    "requirements documentation", "workflow monitoring", "task scheduling",
    "report building", "documentation", "business needs", "discussion notes",
    "test scenario creation",
})


def _is_technical_skill_eligible(text: str) -> bool:
    return _lookup_key(text) not in _RESPONSIBILITY_PHRASES


def _clean(raw_candidates: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    excluded_responsibilities: list[str] = []
    for item in raw_candidates or []:
        text = str(item).strip() if item is not None else ""
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        if _looks_like_non_skill(text):
            continue
        if not _is_technical_skill_eligible(text):
            excluded_responsibilities.append(text)
            continue
        cleaned.append(text)
    if excluded_responsibilities:
        logger.info(
            "Skill Intelligence Engine: excluded %d responsibility/activity phrase(s) from "
            "Technical Skills (these belong in Professional Experience, not here): %s",
            len(excluded_responsibilities), excluded_responsibilities,
        )
    return cleaned


# --- Stage 2: Normalization Engine -------------------------------------------
#
# Root cause this addresses: the same underlying technology gets extracted
# with different spelling/spacing/casing depending on how the source resume
# happened to write it ("ReactJS" vs "React.js" vs "React"), and depending
# on how Gemini chose to phrase it back on a given run. Keyed by a
# case-and-whitespace-normalized (but NOT punctuation-stripped) lookup - see
# _lookup_key - deliberately NOT the aggressively-squashed key
# normalization.py's own _normalization_key uses (which strips every
# non-alphanumeric character): squashing would collapse "C#" down to "c"
# (the "#" stripped), colliding it with the completely different "C"
# language - keeping punctuation in the key and listing each real variant
# string explicitly avoids that false merge entirely.
_NORMALIZATION_MAP: dict[str, str] = {
    "reactjs": "React", "react.js": "React", "react": "React",
    "node": "Node.js", "nodejs": "Node.js", "node.js": "Node.js",
    "my sql": "MySQL", "azure my sql": "MySQL", "mysql": "MySQL",
    "ms sql": "SQL Server", "ms sql server": "SQL Server",
    "microsoft sql server": "SQL Server", "sql server": "SQL Server",
    "c sharp": "C#", "c#": "C#",
    "java script": "JavaScript", "javascript": "JavaScript",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    # Explicit, evidence-driven reversal of an earlier decision (Technical
    # Skills Presentation report, 2 rounds ago): "SQL Developer" (Oracle's
    # IDE) was deliberately kept distinct from bare "SQL" there. This
    # round's own confirmed example asks for "SQL Developer" -> "SQL"
    # specifically - followed here as newer, more explicit instruction,
    # not a silent contradiction (see this round's report for the
    # reasoning trail).
    "sql developer": "SQL",
    "tensor flow": "TensorFlow", "tensorflow": "TensorFlow",
    "py torch": "PyTorch", "pytorch": "PyTorch",
    # C#.NET / ASP.NET - deliberately kept as two DISTINCT possible entries
    # rather than merged into one ("without losing important distinctions"):
    # C# is the language, ASP.NET is the framework built on it. A garbled
    # "C#.NET" entry maps to the language alone; "C# ASP.NET" (both named
    # together) maps to the more specific signal, the framework - never
    # collapsed into a single vague catch-all term.
    "c#.net": "C#", "c# asp.net": "ASP.NET", "asp.net": "ASP.NET",
    # Microsoft Azure - a verbose compound phrase normalized to the name a
    # recruiter/ATS actually expects; bare "Azure" is left alone (already
    # the common short form, not a variant needing correction).
    "microsoft azure cloud": "Microsoft Azure",
    # Azure SQL Database is Microsoft's own product name for what almost
    # every resume/job posting shortens to "Azure SQL" - a real alias, NOT
    # the same as the deliberately-excluded bare "Azure SQL" -> MySQL
    # mapping noted below (this maps TO "Azure SQL", not away from it).
    "azure sql database": "Azure SQL",
    # Azure App Service - dropping the redundant "Azure" prefix here (and
    # ONLY here, not from every Azure service name) matches how this
    # specific service is displayed once already grouped under an "Azure"
    # heading/context elsewhere in the resume (see _AZURE_FAMILY_KEYWORDS
    # below) - repeating "Azure" on every individual service name reads as
    # redundant once the surrounding context already establishes it.
    "azure app service": "App Service",
}

# NOTE on "Azure SQL": the request's own Step 5 example lists "Azure SQL" /
# "Azure My SQL" both merging to "MySQL" - but Azure SQL (Microsoft's cloud
# database service) is actually SQL-Server-based, NOT MySQL; "Azure Database
# for MySQL" (which "Azure My SQL" plausibly means) is the genuinely
# MySQL-compatible one. Mapping bare "Azure SQL" to "MySQL" would misstate a
# real candidate's actual technology experience - a factual error, not a
# cleanup - so it is deliberately NOT included here. Only "Azure My SQL"
# (and plain "My SQL"/"MySQL") map to "MySQL"; "Azure SQL" is left as its
# own distinct, unmodified term. Flagged explicitly rather than silently
# "fixed" per the letter of the request, since correctness of what a
# candidate is credited with knowing outweighs matching the example
# verbatim.


def _lookup_key(text: str) -> str:
    """Case-and-whitespace-normalized key (collapsed internal whitespace,
    lowercased) - deliberately keeps punctuation (".", "#", "+") intact so
    distinct real terms ("C#" vs "C") never collide - see module docstring."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize(cleaned: list[str]) -> list[str]:
    return [_NORMALIZATION_MAP.get(_lookup_key(item), item) for item in cleaned]


# --- Stage 3: Alias Resolution -----------------------------------------------
#
# Root cause this addresses: two genuinely different-LOOKING strings that
# name the exact same real-world product/service ("Azure Kubernetes
# Service" is the full name Microsoft uses; "AKS" is the abbreviation
# nearly every resume and job posting actually uses) - this is conceptually
# distinct from Stage 2's spelling-variant normalization (same word,
# different spelling) since here the strings don't share a root spelling at
# all, only a real-world referent. Modeled as its own lookup table/stage so
# each is independently reviewable and extensible.
_ALIAS_MAP: dict[str, str] = {
    "azure container service": "AKS",
    "azure kubernetes service": "AKS",
    "aks": "AKS",
    "asp.net core": ".NET Core",
    ".net core": ".NET Core",
    "power platform": "Microsoft Power Platform",
    "microsoft power platform": "Microsoft Power Platform",
    "git hub": "GitHub",
    "github": "GitHub",
    "visual studio code": "VS Code",
    "vs code": "VS Code",
}


def _resolve_aliases(normalized: list[str]) -> list[str]:
    return [_ALIAS_MAP.get(_lookup_key(item), item) for item in normalized]


def canonical_technology_name(term: str) -> str | None:
    """Public lookup - the ATS-standard canonical form for a single known
    technology name/variant (checks _NORMALIZATION_MAP then _ALIAS_MAP),
    or None if `term` isn't a recognized variant at all. Exposed for reuse
    by anything that needs to standardize a technology mention OUTSIDE the
    Technical Skills list itself (see ats_intelligence.py, which
    standardizes technology mentions inside free-text Experience/Project
    bullets) - the Skills-list pipeline (build_technical_skills) already
    applies this same lookup via _normalize/_resolve_aliases; this just
    exposes it for a single term rather than a whole list."""
    key = _lookup_key(term)
    return _NORMALIZATION_MAP.get(key) or _ALIAS_MAP.get(key)


def known_technology_variants() -> dict[str, str]:
    """Public accessor - every known variant-key -> canonical-form pair
    from _NORMALIZATION_MAP and _ALIAS_MAP combined, for callers that need
    to scan free text for a recognized technology mention (see
    ats_intelligence.py) rather than look up one term at a time."""
    combined = dict(_NORMALIZATION_MAP)
    combined.update(_ALIAS_MAP)
    return combined


# --- Stage 4: Duplicate Detection --------------------------------------------
#
# Root cause this addresses: after Stages 2-3 collapse spelling variants and
# aliases to one canonical string each, most duplicates are already exact
# matches - a straightforward case-insensitive exact dedup catches those.
# The conservative fuzzy pass underneath catches anything Stages 2-3 didn't
# anticipate (e.g. a typo neither table lists) WITHOUT risking a false merge
# of two genuinely different technologies - same high-threshold, log-every-
# merge approach normalization.py's own _fuzzy_merge uses, reimplemented
# locally so this module has no cross-module dependency (see module
# docstring).
_FUZZY_MERGE_THRESHOLD = 0.93


def _dedupe_exact(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = _lookup_key(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _fuzzy_merge(items: list[str]) -> list[str]:
    kept: list[str] = []
    for item in items:
        merged_into = None
        best_ratio = 0.0
        for existing in kept:
            ratio = difflib.SequenceMatcher(None, item.lower(), existing.lower()).ratio()
            if ratio >= _FUZZY_MERGE_THRESHOLD and ratio > best_ratio:
                merged_into, best_ratio = existing, ratio
        if merged_into:
            logger.info(
                "Skill Intelligence Engine: fuzzy-merged %r into %r (similarity=%.2f)",
                item, merged_into, best_ratio,
            )
        else:
            kept.append(item)
    return kept


def _detect_duplicates(resolved: list[str]) -> list[str]:
    return _fuzzy_merge(_dedupe_exact(resolved))


# --- Technical Skills consolidation (version-variant folding + parent/child --
# --- grouping) - a genuinely different kind of redundancy than the fuzzy
# --- string-similarity merge above catches -------------------------------
#
# CONFIRMED by direct testing, not assumed: _fuzzy_merge (threshold 0.93)
# does not and cannot catch either pattern below - "Oracle SQL (11g)" vs
# "Oracle SQL (12c)" scores 0.875 (below threshold - and no threshold that
# also avoids false-merging genuinely different skills would clear it),
# and "PL/SQL" vs "Stored Procedures"/"Cursors" scores 0.15-0.17 (nowhere
# close - these are semantically related but textually unrelated, which
# string similarity can never detect regardless of threshold).

# A trailing parenthetical containing at least one digit - a version/
# edition marker ("(11g)", "(12c)", "(3.x)", "(2020)") - vs. one that
# doesn't ("(Cloud)", "(Advanced)"), which is meaningful qualifying
# content and is never stripped.
_VERSION_SUFFIX_RE = re.compile(r"\s*\([^()]*\d[^()]*\)\s*$")


def _fold_version_variants(items: list[str]) -> list[str]:
    """Collapses skills that are the same base technology at different
    version/edition numbers ("Oracle SQL (11g)" + "Oracle SQL (12c)") into
    one version-agnostic entry - a resume skill LIST bullet rarely needs a
    specific version number the way, say, a certification date does.
    Never invents a new name: the surviving entry is always either an
    already-bare occurrence of that skill, or the first version-tagged
    one with its version suffix stripped."""
    seen_base: dict[str, str] = {}
    result: list[str] = []
    for item in items:
        base = _VERSION_SUFFIX_RE.sub("", item).strip()
        base_key = base.lower()
        if base_key in seen_base:
            if base != item:
                logger.info("Skill Intelligence Engine: folded version-variant %r into %r.", item, seen_base[base_key])
            continue
        seen_base[base_key] = base
        result.append(base)
    return result


# Small, EXPLICIT, deliberately bounded set of shared "anchor" words safe
# to fold wording variants under. NOT a generic 2-6-letter-acronym rule -
# tested and rejected: a generic acronym rule would treat "SQL" (present
# in "Oracle SQL", "SQL Developer", and bare "SQL" - three genuinely
# different things) the same way it treats "ETL" (which, unlike "SQL", is
# used in only one sense across ordinary resume text), incorrectly folding
# a database dialect, an IDE, and a language into one nonsense bullet.
# Confirmed empirically before shipping this, not assumed safe. Extend
# this set only with words shown by real evidence to be as unambiguous as
# "ETL", never a blanket pattern.
_SHARED_ANCHOR_WORDS: tuple[str, ...] = ("ETL",)


def _group_shared_anchor_variants(items: list[str]) -> list[str]:
    """Folds skills that all mention the same _SHARED_ANCHOR_WORDS token
    ("ETL mappings", "ETL mapping creation", "Workflows (ETL)",
    "Transformations (ETL)") into one grouped entry - "ETL (Mappings,
    Mapping creation, Workflows, Transformations)". Only acts when the
    anchor appears in 2+ DIFFERENT skills (a lone mention is just a normal
    skill, not a repeated-wording pattern) - every distinguishing word is
    preserved as a sub-item, never dropped."""
    result = list(items)
    for anchor in _SHARED_ANCHOR_WORDS:
        pattern = re.compile(rf"\b{re.escape(anchor)}\b", re.IGNORECASE)
        matches = [item for item in result if pattern.search(item)]
        if len(matches) < 2:
            continue
        bare = next((m for m in matches if m.strip().upper() == anchor), None)
        primary = bare or anchor
        remainders: list[str] = []
        for m in matches:
            if m is bare:
                continue
            remainder = pattern.sub("", m).strip(" ()").strip()
            if remainder and remainder not in remainders:
                remainders.append(remainder[:1].upper() + remainder[1:])
        if not remainders:
            continue
        grouped = f"{primary} ({', '.join(remainders)})"
        logger.info(
            "Skill Intelligence Engine: grouped %d wording variant(s) sharing %r into %r.",
            len(remainders), anchor, grouped,
        )
        new_result, written = [], False
        for item in result:
            if item in matches:
                if not written:
                    new_result.append(grouped)
                    written = True
                continue
            new_result.append(item)
        result = new_result
    return result


# Generic (not vendor-specific) database "feature" terms that commonly get
# extracted as separate flat bullets alongside a SQL-family language/
# platform skill, when they're really implementation details OF that
# skill, not distinct standalone technologies. Grouping happens ONLY when
# a recognized SQL-family anchor is ALSO present in the same list - a
# feature term with no such anchor present is left completely untouched,
# never dropped, never grouped under a guessed/invented parent.
# Specific dialects/products checked FIRST - preferred over the generic
# "sql" fallback below whenever both are present in the same list (e.g. a
# candidate listing both bare "SQL" and "PL/SQL": Stored Procedures/
# Triggers are a PL/SQL-specific construct, not a generic ANSI SQL one, so
# the more specific anchor is the correct one to group them under).
_SQL_FAMILY_SPECIFIC_RE = re.compile(
    r"\b(pl/sql|t-sql|transact-sql|mysql|postgresql|oracle sql|sql server)\b", re.IGNORECASE
)
_SQL_FAMILY_ANCHOR_RE = re.compile(
    r"\b(pl/sql|t-sql|transact-sql|mysql|postgresql|oracle sql|sql server|sql)\b", re.IGNORECASE
)
_SQL_FEATURE_TERMS = {
    "stored procedures", "functions", "views", "triggers", "cursors",
    "indexes", "packages", "joins", "subqueries", "constraints",
}


def _group_sql_family_features(items: list[str]) -> list[str]:
    """Folds generic SQL feature terms (Stored Procedures, Functions,
    Views, Triggers, Cursors, ...) beneath the first recognized SQL-family
    skill in the list - "PL/SQL", "Stored Procedures", "Functions",
    "Views", "Triggers", "Cursors" -> "PL/SQL (Stored Procedures,
    Functions, Views, Triggers, Cursors)"."""
    anchor = next((item for item in items if _SQL_FAMILY_SPECIFIC_RE.search(item)), None)
    if anchor is None:
        anchor = next((item for item in items if _SQL_FAMILY_ANCHOR_RE.search(item)), None)
    if anchor is None:
        return items
    features = [item for item in items if item.strip().lower() in _SQL_FEATURE_TERMS]
    if not features:
        return items
    grouped_anchor = f"{anchor} ({', '.join(features)})"
    logger.info(
        "Skill Intelligence Engine: grouped %d SQL feature term(s) under %r.", len(features), anchor,
    )
    result: list[str] = []
    anchor_written = False
    for item in items:
        if item.strip().lower() in _SQL_FEATURE_TERMS:
            continue
        if item == anchor and not anchor_written:
            result.append(grouped_anchor)
            anchor_written = True
        else:
            result.append(item)
    return result


# Generic, non-distinguishing descriptor words - "Oracle SQL"/"Oracle DB"
# add nothing beyond restating "this is a database product" over bare
# "Oracle". Deliberately small and generic (not vendor-specific) so it
# only fires on genuinely redundant naming, never a real distinguishing
# product name.
_GENERIC_VENDOR_SUFFIX_WORDS = {"sql", "db", "database", "server"}


def _fold_generic_vendor_suffixes(items: list[str]) -> list[str]:
    """Collapses redundant naming variants of the same vendor/product -
    "Oracle" + "Oracle SQL" + "Oracle DB" -> "Oracle" - when the ONLY
    difference between them is a generic, non-distinguishing descriptor
    word. All-or-nothing per first-word group, deliberately conservative:
    if ANY member of the group adds a genuinely distinguishing word (e.g.
    "Oracle Fusion", "Oracle EBS"), the WHOLE group is left untouched -
    never risks collapsing away a real, distinct product to save a
    borderline case elsewhere in the same group."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for item in items:
        first_word = item.split()[0].lower() if item.split() else ""
        groups.setdefault(first_word, []).append(item)
        if first_word not in order:
            order.append(first_word)

    canonical_by_first_word: dict[str, str] = {}
    for first_word in order:
        group = groups[first_word]
        if len(group) < 2:
            continue
        bare = next((entry for entry in group if len(entry.split()) == 1), None)
        all_generic_or_bare = all(
            len(entry.split()) == 1
            or (len(entry.split()) == 2 and entry.split()[1].lower() in _GENERIC_VENDOR_SUFFIX_WORDS)
            for entry in group
        )
        if not all_generic_or_bare:
            continue
        canonical_by_first_word[first_word] = bare or group[0].split()[0]

    if not canonical_by_first_word:
        return items

    result: list[str] = []
    seen_canonical: set[str] = set()
    for item in items:
        first_word = item.split()[0].lower() if item.split() else ""
        canonical = canonical_by_first_word.get(first_word)
        if canonical is None:
            result.append(item)
            continue
        if canonical.lower() in seen_canonical:
            logger.info("Skill Intelligence Engine: folded redundant vendor-naming variant %r into %r.", item, canonical)
            continue
        seen_canonical.add(canonical.lower())
        result.append(canonical)
    return result


# ETL vendor names recognized as an anchor for grouping generic
# "<Type> Transformation(s)" items - e.g. "Router Transformation",
# "Lookup Transformation" - under a vendor-prefixed label. The vendor
# word is only ever drawn from an ALREADY-PRESENT skill in the same list
# (never guessed) - see _group_etl_transformation_types.
_ETL_VENDOR_CANONICAL = {
    "informatica": "Informatica", "talend": "Talend", "ssis": "SSIS",
    "datastage": "DataStage", "ab initio": "Ab Initio", "abinitio": "AbInitio",
}
_ETL_VENDOR_RE = re.compile(r"\b(informatica|talend|ssis|datastage|ab initio|abinitio)\b", re.IGNORECASE)
_TRANSFORMATION_SUFFIX_RE = re.compile(r"^(.+?)\s+Transformations?$", re.IGNORECASE)


def _group_etl_transformation_types(items: list[str]) -> list[str]:
    """"Router Transformation" + "Lookup Transformation" + ... ->
    "Informatica Transformations (Router, Lookup, ...)" when an
    Informatica/Talend/SSIS/DataStage/Ab Initio anchor is ALSO present in
    the same list - the vendor name is drawn from that already-present
    skill, never invented. Falls back to the generic "Transformations"
    label (no vendor prefix) when no such anchor exists, rather than
    guessing one. Only acts when 2+ "<Type> Transformation(s)" items are
    present - a lone one is just a normal skill."""
    matches: list[tuple[str, str]] = []
    for item in items:
        match = _TRANSFORMATION_SUFFIX_RE.match(item.strip())
        if match:
            matches.append((item, match.group(1).strip()))
    if len(matches) < 2:
        return items

    vendor_match = next((m for m in (_ETL_VENDOR_RE.search(item) for item in items) if m), None)
    label = f"{_ETL_VENDOR_CANONICAL[vendor_match.group(1).lower()]} Transformations" if vendor_match else "Transformations"

    matched_items = {item for item, _ in matches}
    types = [t for _, t in matches]
    grouped = f"{label} ({', '.join(types)})"
    logger.info("Skill Intelligence Engine: grouped %d transformation type(s) into %r.", len(types), grouped)

    result: list[str] = []
    written = False
    for item in items:
        if item in matched_items:
            if not written:
                result.append(grouped)
                written = True
            continue
        result.append(item)
    return result


def _consolidate_related_skills(items: list[str]) -> list[str]:
    """Public-within-this-module orchestration of the consolidation
    passes above, run in this order (version-folding first, since a
    version-tagged duplicate needs to become its bare form before any
    grouping pass can recognize it as an anchor/feature term; the
    vendor-suffix fold and transformation-type grouping run last since
    neither depends on the earlier passes' output shape). Every skill in
    `items` survives as either its own entry or as a sub-item inside a
    grouped one - none is ever dropped, none invented."""
    items = _fold_version_variants(items)
    items = _group_shared_anchor_variants(items)
    items = _group_sql_family_features(items)
    items = _fold_generic_vendor_suffixes(items)
    items = _group_etl_transformation_types(items)
    return items


# --- Stages 5 & 6: Noise Removal + Skill Classification ----------------------
#
# Internal-only categories (STEP 7) - never rendered, used exclusively to
# decide (a) whether an item is a genuine technical skill at all (Noise
# Removal) and (b) its priority tier for ranking (Skill Ranking, below).
# Order here is also the ranking priority order (STEP 8): Primary
# Technologies (a candidate's core Programming Language) first, then
# Frameworks, Cloud, Databases, DevOps, Infrastructure, AI (folding the
# closely-related Machine Learning in immediately after it), Analytics, and
# finally "Supporting Technologies" (Container/Power Platform/Networking/
# Security - real, valuable, but not what a recruiter scans for first).
_CATEGORY_RANK_ORDER: tuple[str, ...] = (
    "Programming Language",
    "Framework",
    "Cloud",
    "Database",
    "DevOps",
    "Infrastructure",
    "AI",
    "Machine Learning",
    "Analytics",
    "ETL Tools",
    "Data Quality",
    "API Integration",
    "ERP Systems",
    "Container",
    "Power Platform",
    "Networking",
    "Security",
    "Testing",
    "Project Management",
    "Business Skills",
    "Soft Skills",
    "Other Skills",
)

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Programming Language": (
        "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
        "rust", "ruby", "php", "swift", "kotlin", "scala", "perl", "bash",
        "powershell", "sql", "html", "css", "r", "dart", "objective-c",
    ),
    "Framework": (
        "react", "angular", "vue", "django", "flask", "spring", "spring boot",
        "express", "next.js", "node.js", ".net", ".net core", "asp.net",
        "laravel", "rails", "fastapi", "bootstrap", "jquery", "svelte",
    ),
    "Cloud": (
        "aws", "amazon web services", "azure", "gcp", "google cloud",
        "ibm cloud", "oracle cloud", "digitalocean", "heroku", "cloudflare",
        "azure portal", "aks", "ec2", "s3", "azure functions", "aws lambda",
        "lambda", "app service", "data factory", "synapse",
        "azure data lake", "data lake",
    ),
    "Database": (
        "mysql", "postgresql", "sql server", "oracle database", "redis",
        "cassandra", "dynamodb", "sqlite", "mariadb", "elasticsearch",
        "neo4j", "cosmos db", "nosql", "azure sql", "mongodb",
    ),
    "DevOps": (
        "ci/cd", "jenkins", "github actions", "gitlab ci", "azure devops",
        "ansible", "puppet", "chef", "terraform", "git", "github", "gitlab",
        "bitbucket", "devops", "circleci", "travis ci",
    ),
    "Infrastructure": (
        "windows server", "windows", "linux", "ubuntu", "centos", "red hat",
        "macos", "unix", "load balancer", "cdn", "vnet", "virtual network",
        "expressroute", "vpn", "subnetting", "traffic manager",
    ),
    "AI": (
        "artificial intelligence", "generative ai", "llm", "langchain",
        "hugging face", "openai", "nlp", "natural language processing",
        "computer vision",
    ),
    "Machine Learning": (
        "machine learning", "deep learning", "tensorflow", "pytorch",
        "scikit-learn", "keras", "neural networks", "mlops", "data science",
    ),
    "Analytics": (
        "etl", "data pipeline", "spark", "hadoop", "airflow",
        "data warehousing", "snowflake", "databricks", "bigquery", "redshift",
        "power bi", "tableau", "data visualization",
    ),
    # Industry-specific templates (Informatica/SAP): named ETL/ERP PRODUCTS
    # aren't covered by "Analytics"'s generic "etl" keyword or by "Database"
    # at all - a candidate whose skills are "Informatica PowerCenter"/"SAP
    # HANA"/"S/4HANA" previously matched no category and got silently
    # excluded as noise (same class of defect Testing/Project Management/
    # Business Skills/Soft Skills already closed for other non-mainstream-
    # tech-stack domains).
    "ETL Tools": (
        "informatica", "powercenter", "informatica powercenter", "iics",
        "informatica cloud", "informatica mdm", "idq", "informatica data quality",
        "b2b data exchange", "talend", "ssis", "datastage", "ab initio", "abinitio",
        "data integration", "mdm", "master data management",
    ),
    # Data Quality (Technical Skills presentation pass): concept/practice
    # keywords distinct from ETL Tools' own PRODUCT names (Informatica IDQ
    # etc. stay in ETL Tools - a tool name, not a practice) - a "Data
    # Quality" bucket recruiters recognize as its own capability rather
    # than folded anonymously into ETL Tools or "Other Skills".
    "Data Quality": (
        "data quality", "data profiling", "data validation", "dq rule",
        "dq scorecard", "dq monitoring", "data quality framework",
        "data quality test", "data cleansing",
    ),
    # API Integration (Technical Skills presentation pass): "rest api"/
    # "rest apis"/"api gateway" moved here from Networking, "oauth" moved
    # here from Security - these are API-integration concepts, not general
    # networking/security concepts, and grouping them under their own
    # recruiter-recognizable label reads far better than splitting one
    # candidate's API work across two unrelated-looking headings.
    "API Integration": (
        "rest api", "rest apis", "soap api", "soap apis", "api gateway",
        "api integration", "oauth", "oauth2", "jwt", "webhook", "graphql",
    ),
    "ERP Systems": (
        "sap", "sap hana", "sap abap", "sap fico", "sap mm", "sap sd",
        "sap basis", "sap bw", "sap successfactors", "sap ariba", "s/4hana",
        "sap ecc", "oracle ebs", "oracle apps", "peoplesoft", "workday",
    ),
    "Container": (
        "docker", "kubernetes", "containerd", "helm", "openshift",
        "docker swarm", "podman", "k8s",
    ),
    "Power Platform": (
        "power apps", "power automate", "power virtual agents",
        "microsoft power platform",
    ),
    "Networking": (
        "dns", "nsg", "bgp", "vlan", "tcp/ip", "http", "https",
    ),
    "Security": (
        "ssl", "tls", "iam", "mfa", "encryption", "firewall",
        "siem", "cybersecurity", "key vault", "zero trust",
        "penetration testing",
    ),
    # Non-technical categories (Priority 2/6 fix): the Skill Intelligence
    # Engine's Noise Removal stage (_classify_and_filter) is an INCLUSION
    # whitelist - an item must positively match a known category to survive
    # at all (see module docstring). Before these categories existed, a
    # non-technical candidate's entire skill set (a Project Manager's
    # "Agile", "Stakeholder Management", "Risk Management", ...) matched NO
    # category, so every one of them was silently excluded as "noise" and
    # the whole Technical Skills section rendered empty - a real,
    # confirmed defect (a resume must never lose its Skills section just
    # because the candidate's skills happen to be process/business skills
    # rather than a tech stack). These three categories close that gap the
    # same way every existing one already works: keyword-presence
    # whitelisting, ranked (never alphabetically) same as every other
    # category, at the tail of _CATEGORY_RANK_ORDER since a recruiter still
    # scans a listed tech stack first when one exists - for a candidate
    # whose ENTIRE skill set is process/business skills, these are simply
    # the whole rendered list, so tier position doesn't disadvantage them.
    "Testing": (
        "selenium", "junit", "pytest", "cypress", "postman", "test automation",
        "manual testing", "unit testing", "integration testing", "load testing",
        "test cases", "quality assurance", "qa testing", "regression testing",
        "test planning", "defect management", "sdet", "jmeter", "loadrunner",
        "test management", "appium", "testng",
    ),
    "Project Management": (
        "project management", "program management", "product management",
        "scrum", "agile", "kanban", "waterfall", "business analysis",
        "requirements gathering", "change management", "sprint planning",
        "jira",
    ),
    "Business Skills": (
        "stakeholder management", "risk management", "budget management",
        "budgeting", "budget", "vendor management", "client management",
        "negotiation", "presentation", "presentation skills",
    ),
    "Soft Skills": (
        "communication", "leadership", "team leadership", "collaboration",
        "problem solving", "critical thinking", "time management",
        "mentoring",
    ),
}


def _matches_keyword(text_lower: str, keyword: str) -> bool:
    if text_lower == keyword:
        return True
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text_lower) is not None


def _count_mentions(keyword: str, corpus_lower: str) -> int:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
    return len(re.findall(pattern, corpus_lower))


# --- Technology Grouping (family clustering within the Cloud category) -----
#
# Rather than nested sub-headings (which would mean "skills" becomes a
# category -> list dict instead of a flat list - a shape change that would
# break every downstream consumer expecting a flat list of individual
# strings: resume_quality_engine.py, ats_intelligence.py, and resume_
# scoring_engine.py all count/iterate `parsed["skills"]` as plain strings,
# and file_generator.py's rendering is explicitly out of scope this
# revision), related same-vendor items are clustered ADJACENT to each
# other within the still-flat list instead - "grouped" through ordering
# discipline, not a nested structure. "AWS, Python, Azure, Cosmos DB,
# Functions" reads as randomly stuffed; "Python, Azure, Cosmos DB,
# Functions, AWS" reads as organized - both are the same flat list to
# every downstream consumer, only the ORDER differs.
_AZURE_FAMILY_KEYWORDS = (
    "azure", "app service", "azure functions", "cosmos db", "data factory",
    "synapse", "databricks", "azure sql", "key vault", "aks",
    "azure devops", "azure portal", "azure data lake", "data lake",
)
_AWS_FAMILY_KEYWORDS = (
    "aws", "amazon web services", "ec2", "s3", "lambda", "aws lambda",
    "dynamodb", "redshift", "aws glue", "cloudfront",
)
_GCP_FAMILY_KEYWORDS = (
    "gcp", "google cloud", "google cloud platform", "bigquery",
)
_FAMILY_RANK = {"azure": 0, "aws": 1, "gcp": 2}


def _technology_family(skill: str) -> str | None:
    lowered = _lookup_key(skill)
    if any(_matches_keyword(lowered, kw) for kw in _AZURE_FAMILY_KEYWORDS):
        return "azure"
    if any(_matches_keyword(lowered, kw) for kw in _AWS_FAMILY_KEYWORDS):
        return "aws"
    if any(_matches_keyword(lowered, kw) for kw in _GCP_FAMILY_KEYWORDS):
        return "gcp"
    return None


# --- Skill Prioritization (relevance-based ranking) --------------------------
#
# Root cause this addresses: a flat category-tier ranking (below) is
# consistent, but doesn't distinguish a skill the candidate's own resume
# heavily evidences (mentioned in a recent job title, used across several
# bullets) from one mentioned only once in passing - both would otherwise
# rank purely by first-seen order within their shared category tier. This
# computes an evidence-based relevance score PURELY from the candidate's
# own resume text (never external keyword-popularity data) - frequency in
# the Professional Summary, a job-TITLE mention (the strongest single
# signal, weighted higher for the most recent job - resumes list jobs
# most-recent-first by convention throughout this pipeline), and bullet/
# project mentions - used as _rank()'s tie-break WITHIN each category tier
# instead of plain first-seen order, when a `relevance_context` is given.
def _compute_relevance_scores(
    skills: list[str], context: dict, industry_keywords: tuple[str, ...] = (),
) -> dict[str, int]:
    scores = {skill: 0 for skill in skills}

    summary_lower = _lookup_key(str(context.get("summary") or ""))
    for skill in skills:
        scores[skill] += _count_mentions(skill, summary_lower) * 2

    experience = context.get("experience") or []
    for index, exp in enumerate(experience):
        recency_weight = 2 if index == 0 else 1
        role_lower = _lookup_key(str(exp.get("role") or ""))
        bullets_lower = _lookup_key(" ".join(exp.get("points") or []))
        for skill in skills:
            if _count_mentions(skill, role_lower):
                scores[skill] += 3 * recency_weight
            scores[skill] += _count_mentions(skill, bullets_lower) * recency_weight

    for proj in context.get("projects") or []:
        proj_text = _lookup_key(
            f"{proj.get('title', '')} {proj.get('description', '')} {proj.get('technologies', '')} "
            + " ".join(proj.get("responsibilities") or [])
        )
        for skill in skills:
            scores[skill] += _count_mentions(skill, proj_text)

    # Industry-specific templates: a small, fixed bonus (not a multiplier -
    # never lets an industry-keyword match override GENUINE, heavily-
    # evidenced relevance from the candidate's own summary/experience/
    # projects above) for a skill that also appears in the candidate's
    # classified industry's own priority-keyword list (see
    # industry_intelligence.py's _INDUSTRY_TERMINOLOGY) - within a category
    # tier, a Java candidate's "Spring Boot" now edges out an incidental
    # "Docker" mention, without needing a separate per-industry ranking
    # table. Empty tuple (no industry context given) is a complete no-op,
    # identical to every caller before this parameter existed.
    if industry_keywords:
        for skill in skills:
            skill_lower = _lookup_key(skill)
            if any(_matches_keyword(skill_lower, kw.lower()) for kw in industry_keywords):
                scores[skill] += 4

    return scores


def classify_skill(skill: str) -> str | None:
    """Returns the internal category name (see _CATEGORY_RANK_ORDER) a
    skill belongs to, or None if it matches no known NAMED category at all -
    a business term, soft skill, methodology, company name, project name,
    or anything else that doesn't match a keyword-defined category (see
    _CATEGORY_KEYWORDS - "Other Skills" deliberately has no keyword list of
    its own; it's a catch-all applied by callers, not something matched
    here). Used to decide category (Skill Classification/ranking) - see
    _classify_and_filter for how a None result is handled."""
    lowered = _lookup_key(skill)
    for category in _CATEGORY_RANK_ORDER:
        if any(_matches_keyword(lowered, keyword) for keyword in _CATEGORY_KEYWORDS.get(category, ())):
            return category
    return None


def _classify_and_filter(deduped: list[str]) -> list[tuple[str, str]]:
    """Skill Classification: classifies every item into one of
    _CATEGORY_RANK_ORDER's categories. An item that matches none of the
    named technical/business/soft-skill categories is bucketed into
    "Other Skills" rather than dropped - a candidate's genuine skill must
    never silently disappear just because this module's keyword
    dictionaries don't happen to name it (a real, confirmed defect: an
    unusual technology, a niche industry tool, or a skill phrased
    differently than the keyword lists expect was previously excluded
    entirely with no trace on the rendered resume). See module docstring -
    this used to be an exclusion whitelist; it is now a total partition:
    every input item ends up in exactly one category, "Other Skills" being
    the catch-all for anything unclassifiable."""
    survivors: list[tuple[str, str]] = []
    bucketed_as_other = 0
    for item in deduped:
        category = classify_skill(item) or "Other Skills"
        if category == "Other Skills":
            bucketed_as_other += 1
        survivors.append((item, category))
    if bucketed_as_other:
        logger.info(
            "Skill Intelligence Engine: %d item(s) matched no named category and were kept "
            "under 'Other Skills' (never dropped).", bucketed_as_other,
        )
    return survivors


# --- Stage 7: Skill Ranking ---------------------------------------------------
#
# "Never sort alphabetically" - always ranks by category priority (see
# _CATEGORY_RANK_ORDER) first. WITHIN a category tier, the secondary key
# depends on whether real evidence is available: if `relevance_scores` is
# given (Skill Prioritization - resume_optimizer.py's real call, with
# actual candidate content to draw on), genuine evidenced relevance
# dominates outright - a heavily-evidenced skill in one category tier
# should never be held back by a same-vendor grouping preference. Without
# relevance evidence (the deterministic-fallback/no-context path - ats_
# intelligence.py's/resume_quality_engine.py's own re-checks, or a bare
# call with no context), same-vendor items are clustered together instead
# (see _technology_family/Technology Grouping) as a sensible default
# ordering, falling back to first-seen input order for anything with no
# recognized family. Never alphabetical at any level.
def _rank(survivors: list[tuple[str, str]], relevance_scores: dict[str, int] | None = None) -> list[str]:
    rank_index = {category: i for i, category in enumerate(_CATEGORY_RANK_ORDER)}

    def sort_key(pair: tuple[str, str]):
        item, category = pair
        if relevance_scores:
            return (rank_index[category], -relevance_scores.get(item, 0))
        family = _technology_family(item)
        return (rank_index[category], _FAMILY_RANK.get(family, 2))

    ordered = sorted(survivors, key=sort_key)
    return [item for item, _category in ordered]


# --- ATS Standardization ------------------------------------------------------
#
# Achieved by Stages 2-3 choosing ATS-conventional canonical names (e.g.
# "SQL Server" not "MS SQL", "PostgreSQL" not "Postgres", "JavaScript" not
# "Java Script", "VS Code" not "Visual Studio Code") - there is deliberately
# no separate rewriting step here, since re-touching an already-canonical
# name after Stages 2-3 have run would only risk introducing an
# inconsistency, not add one.


# --- Stage 8: Quality Validation ----------------------------------------------
#
# Final, deterministic safety net before returning - re-checks the exact
# invariants STEP 10 asks for (no duplicates, nothing unclassifiable, within
# the size cap). Should be a no-op in normal operation, since every earlier
# stage already guarantees these; kept as its own explicit, logged pass so a
# defect in an earlier stage can never silently reach the rendered resume.
def _quality_validate(ranked: list[str]) -> list[str]:
    """Final, deterministic safety net - re-checks for duplicates that
    survived earlier stages. Does NOT re-run classification: every item
    reaching this stage already has a category (including the "Other
    Skills" catch-all - see _classify_and_filter), so there is no longer a
    "failed re-classification" case that would justify dropping an item
    here."""
    validated = _dedupe_exact(ranked)
    if len(validated) != len(ranked):
        logger.warning(
            "Skill Intelligence Engine quality validation: caught %d duplicate(s) that "
            "survived earlier stages.", len(ranked) - len(validated),
        )
    return validated


def build_technical_skills(
    raw_candidates: list[str] | None,
    max_items: int | None = None,
    relevance_context: dict | None = None,
    industry_keywords: tuple[str, ...] = (),
) -> list[str]:
    """Runs the full, deterministic Skill Intelligence Engine pipeline (see
    module docstring) over a flat list of candidate skill strings - from
    Gemini's own categorization, skill_categorizer.py's keyword-based
    fallback, or a raw extracted list directly, "regardless of source" -
    and returns the FINAL Technical Skills list: cleaned, normalized,
    alias-resolved, deduplicated (even across differently-spelled/differently
    -named variants), noise-free, ranked by recruiter-relevance (never
    alphabetically), and capped at `max_items`. This is the single
    authoritative last step for Technical Skills content - nothing
    downstream (the Resume Builder) further reorders, recategorizes, or
    caps it; it renders exactly what this function returns, as ONE
    un-subsectioned "Technical Skills" section (a flat list, matching
    file_generator.py's existing flat-list rendering branch - no rendering
    change needed).

    `relevance_context` is OPTIONAL (default None, preserving this
    function's exact prior behavior for every existing caller - ats_
    intelligence.py, resume_quality_engine.py, and resume_scoring_engine.py
    all still call this with no context and get identical results to
    before). When given (the candidate's `summary`/`experience`/`projects`,
    passed by resume_optimizer.py - the one caller with real resume
    content available), skills within the same category tier are ordered
    by how strongly the resume's own text evidences each one (see
    _compute_relevance_scores) instead of by first-seen order.

    `industry_keywords` (industry-specific templates) is OPTIONAL (default
    `()`, a no-op - every existing caller is unaffected) - the classified
    industry's own priority keywords (see industry_intelligence.py), used
    as a small additional relevance bonus so a Java candidate's "Spring
    Boot" ranks ahead of an incidental "Docker" mention within the same
    category tier, without a separate per-industry ranking table.
    """
    cleaned = _clean(raw_candidates)
    normalized = _normalize(cleaned)
    resolved = _resolve_aliases(normalized)
    deduped = _consolidate_related_skills(_detect_duplicates(resolved))
    survivors = _classify_and_filter(deduped)
    relevance_scores = (
        _compute_relevance_scores(deduped, relevance_context or {}, industry_keywords)
        if (relevance_context or industry_keywords) else None
    )
    ranked = _rank(survivors, relevance_scores)
    validated = _quality_validate(ranked)
    # A candidate's genuine skill must never silently disappear for length
    # reasons - see module docstring/_classify_and_filter. `max_items` is
    # None (no cap) by default; a caller may still pass an explicit cap
    # (e.g. a UI that wants a short preview), which is honored as-is.
    return validated if max_items is None else validated[:max_items]


# --- Technical Skills PRESENTATION: dynamic, recruiter-friendly category ---
# --- display labels - build_technical_skills_grouped only. The underlying
# --- classify_skill()/_classify_and_filter/_rank pipeline, and every
# --- caller of the flat build_technical_skills() (ats_intelligence.py,
# --- resume_scoring_engine.py, resume_quality_engine.py), are completely
# --- unaffected - this only changes the HEADING an already-classified
# --- item is displayed under, never which items exist or what category
# --- they were classified into.
#
# Simple 1:1 renames - same items, friendlier label, no content change.
_SIMPLE_LABEL_RENAMES: dict[str, str] = {
    "Programming Language": "Languages",
    "Database": "Databases",
    "Cloud": "Cloud & Storage",
    "ETL Tools": "ETL / ELT Tools",
}

# "Analytics" bundles several genuinely distinct recruiter-facing
# capabilities - a data-warehouse specialist's Snowflake/Redshift skills
# read very differently from a BI analyst's Power BI/Tableau skills or a
# pipeline engineer's Spark/Airflow skills. Split DYNAMICALLY per
# candidate based on which of these are actually present, rather than one
# blanket "Analytics" label for all three. An item matching none of these
# three keyword sets keeps the "Analytics" label as a safe fallback -
# nothing is ever dropped for not matching a more specific bucket.
_DATA_WAREHOUSE_TERMS = ("snowflake", "redshift", "bigquery", "synapse", "data warehousing", "warehouse")
_BI_REPORTING_TERMS = ("power bi", "tableau", "data visualization", "looker", "qlik")
_DATA_ENGINEERING_TERMS = ("etl", "data pipeline", "spark", "hadoop", "databricks")
# Orchestration/scheduling tools - split OUT of Data Engineering (Technical
# Skills Curation V2 - "Orchestration" is now a named allowed category):
# these are genuinely about task/job SCHEDULING, a distinct recruiter-
# facing capability from Spark/Hadoop-style data PROCESSING, even though
# both used to land under the same "Data Engineering" label.
_ORCHESTRATION_TERMS = ("airflow", "oozie", "luigi", "prefect", "dagster", "control-m", "autosys", "scheduler")

_DISPLAY_RANK_ORDER: tuple[str, ...] = (
    "Languages", "Framework", "Cloud & Storage", "Databases", "DevOps", "Infrastructure",
    "AI", "Machine Learning", "Data Warehouse", "Data Engineering", "Orchestration", "BI & Reporting",
    "Analytics", "ETL / ELT Tools", "Data Quality", "API Integration", "ERP Systems", "Container",
    "Power Platform", "Networking", "Security", "Testing", "Practices", "Soft Skills", "Other Skills",
)


def _split_analytics_items(items: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {}
    for item in items:
        lowered = item.lower()
        if any(term in lowered for term in _DATA_WAREHOUSE_TERMS):
            label = "Data Warehouse"
        elif any(term in lowered for term in _BI_REPORTING_TERMS):
            label = "BI & Reporting"
        elif any(term in lowered for term in _ORCHESTRATION_TERMS):
            label = "Orchestration"
        elif any(term in lowered for term in _DATA_ENGINEERING_TERMS):
            label = "Data Engineering"
        else:
            label = "Analytics"
        buckets.setdefault(label, []).append(item)
    return buckets


def _apply_dynamic_display_labels(grouped: dict[str, list[str]]) -> dict[str, list[str]]:
    """Presentation-only relabeling of the already-final {category: [items]}
    mapping. Never moves an item out of the base classification
    _classify_and_filter assigned it, never drops one: an Analytics item
    stays Analytics-derived (just split into a more specific display
    label); a Business Skills/Project Management item stays exactly that
    content, just filed under one shared "Practices" heading instead of
    two separate ones."""
    result: dict[str, list[str]] = {}
    for category, items in grouped.items():
        if category == "Analytics":
            for label, sub_items in _split_analytics_items(items).items():
                result.setdefault(label, []).extend(sub_items)
        elif category in ("Business Skills", "Project Management"):
            result.setdefault("Practices", []).extend(items)
        else:
            result.setdefault(_SIMPLE_LABEL_RENAMES.get(category, category), []).extend(items)
    return result


def build_technical_skills_grouped(
    raw_candidates: list[str] | None,
    max_items: int | None = None,
    relevance_context: dict | None = None,
    industry_keywords: tuple[str, ...] = (),
) -> dict[str, list[str]]:
    """Same pipeline and same final skill SET/ORDER as build_technical_skills
    (every argument means exactly the same thing - this is not a second,
    divergent classification pass), but returned as an ordered
    {category: [items]} mapping instead of one flat list, for a caller that
    wants to render actual category subheadings (Programming Languages,
    Frameworks, Cloud, ..., "Other Skills") rather than one un-subsectioned
    block. Category order follows _CATEGORY_RANK_ORDER; within a category,
    order matches build_technical_skills' own ranking exactly."""
    cleaned = _clean(raw_candidates)
    normalized = _normalize(cleaned)
    resolved = _resolve_aliases(normalized)
    deduped = _consolidate_related_skills(_detect_duplicates(resolved))
    survivors = _classify_and_filter(deduped)
    relevance_scores = (
        _compute_relevance_scores(deduped, relevance_context or {}, industry_keywords)
        if (relevance_context or industry_keywords) else None
    )
    category_by_item = dict(survivors)
    ranked_flat = _rank(survivors, relevance_scores)
    validated_flat = _quality_validate(ranked_flat)
    final_items = validated_flat if max_items is None else validated_flat[:max_items]

    grouped: dict[str, list[str]] = {}
    for item in final_items:
        category = category_by_item.get(item, "Other Skills")
        grouped.setdefault(category, []).append(item)
    # Dynamic, recruiter-friendly display labels (Languages/Databases/
    # Cloud & Storage/ETL & ELT Tools/Practices/Data Warehouse/Data
    # Engineering/BI & Reporting - see _apply_dynamic_display_labels'
    # own docstring) - presentation only, same items, same underlying
    # classification, just a friendlier heading.
    grouped = _apply_dynamic_display_labels(grouped)
    # Re-order the dict itself by _DISPLAY_RANK_ORDER (Python dicts preserve
    # insertion order) rather than whatever order labels were first
    # encountered - keeps display order consistent with the same
    # recruiter-priority tiering build_technical_skills already applies.
    return {
        label: grouped[label] for label in _DISPLAY_RANK_ORDER if label in grouped
    }
