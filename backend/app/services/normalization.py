"""Post-processing of whatever JSON Gemini hands back.

This is ported near-verbatim from the original backend's
normalize_parsed/enforce_limits/clean_name - that logic already did its job
well (coercing Gemini's occasionally-inconsistent shapes into the schema the
file generators expect, deduping, enforcing length limits). Only cleaned up
for readability; behavior is unchanged.

normalize_skills()/normalize_tools()/normalize_certifications() (further
down) are a later addition: a dedicated, reusable normalization layer for
flat string lists - capitalization/spelling-variant merging, exact and
near-duplicate removal - applied by enforce_limits() immediately after
Stage 1 extraction and before the Resume Intelligence Engine
(resume_optimizer.py) ever sees the data. Kept in this same module rather
than a new file because this module is already "the place Stage 1's output
gets cleaned up before anything else touches it" - the exact role this new
layer plays too, just for a different set of fields.
"""
import difflib
import logging
import re

logger = logging.getLogger(__name__)

# Section headings that must never be accepted as a candidate's name. This is
# the actual root-cause fix for the "name": "Professional Summary" bug: the
# old `clean_name` only checked "is this alphabetic text, <=4 words" -
# "Professional Summary" trivially passes that check, so if Gemini ever
# mislabels a heading as the name (or the raw-text fallback below grabs a
# heading line), nothing caught it before it reached the rendered PDF/DOCX.
# Checked case-insensitively, on the fully-cleaned candidate, so it catches
# the heading regardless of which layout/document it came from.
_NON_NAME_HEADINGS = {
    "professional summary", "summary", "objective", "career objective",
    "profile", "about me", "personal details", "personal information",
    "skills", "technical skills", "core competencies", "competencies", "key skills",
    "experience", "work experience", "professional experience", "employment history",
    "education", "education qualifications", "academic details",
    "projects", "project handled", "projects handled",
    "certifications", "certification", "awards", "achievements",
    "contact", "contact details", "contact information",
    "curriculum vitae", "resume", "references", "hobbies", "interests",
}

# A different failure mode from a section heading: Gemini (or a raw-text
# fallback) hands back the OPENING WORDS of the Professional Summary sentence
# itself as the name (e.g. "Versatile Project Manager with 8 years..." ->
# truncated by the 4-word cap below to "Versatile Project Manager with", or
# "Adept at aligning business objectives..." -> "Adept at aligning business").
# None of these words are section headings, so _NON_NAME_HEADINGS never
# caught this - checked by first word only, since a real name's first word
# is never one of these summary-opener adjectives/adverbs. This list is
# necessarily incomplete (new resumes keep finding new opener words - "adept"
# wasn't here until one did) - _looks_like_descriptive_phrase below is the
# general-purpose backstop that doesn't depend on enumerating every one.
_SUMMARY_OPENER_FIRST_WORDS = {
    "experienced", "professional", "versatile", "resultsdriven", "resultoriented",
    "highly", "dynamic", "dedicated", "motivated", "accomplished", "seasoned",
    "innovative", "strategic", "detailoriented", "passionate", "proven",
    "skilled", "talented", "driven", "creative", "analytical", "hardworking",
    "selfmotivated", "goaloriented", "customerfocused", "adept", "proficient",
    "capable", "focused", "committed", "enthusiastic", "knowledgeable",
}

# General-purpose backstop for "clearly a descriptive phrase, not a human
# name" (requirement: reject candidates that contain verbs / are descriptive
# phrases) - rather than only ever growing the adjective list above one word
# at a time, this rejects ANY candidate containing a common English function
# word (preposition/conjunction/article) a real personal name essentially
# never contains. "Adept AT aligning business", "Experienced IN leading
# teams", "Responsible FOR managing budgets" all get caught by this alone,
# regardless of which adjective they start with.
_NAME_DISQUALIFYING_WORDS = {
    "a", "an", "the", "and", "or", "but", "at", "in", "on", "of", "for",
    "with", "to", "by", "from", "as", "is", "are", "was", "were", "be",
    "being", "been", "that", "who", "which",
}


def _looks_like_descriptive_phrase(words: list[str]) -> bool:
    lowered = [w.lower() for w in words]
    if any(w in _NAME_DISQUALIFYING_WORDS for w in lowered):
        return True
    # A gerund/present-participle ("aligning", "leading", "managing") is a
    # verb form - real names don't contain one. len() guard avoids false
    # positives on short surnames that happen to end in "ing" (rare, but
    # e.g. "King" at 4 letters shouldn't trip this).
    if any(w.endswith("ing") and len(w) > 5 for w in lowered):
        return True
    return False


def clean_name(raw_name: str | None) -> str | None:
    if not raw_name:
        return None

    raw_name = raw_name.strip()
    raw_name = re.sub(r"[^a-zA-Z\s]", "", raw_name)

    words = raw_name.split()
    if len(words) > 4:
        words = words[:4]

    cleaned = " ".join(words) if words else None
    if not cleaned:
        return None
    if cleaned.lower() in _NON_NAME_HEADINGS:
        return None
    if words[0].lower() in _SUMMARY_OPENER_FIRST_WORDS:
        return None
    if _looks_like_descriptive_phrase(words):
        return None
    return cleaned


def is_plausible_name(name: str | None, summary: str = "") -> bool:
    """Stricter check used to decide whether a Gemini response needs a retry
    (see resume_service.py) - catches "name IS the summary" (or its opening
    words) even when the raw value would otherwise survive `clean_name`
    (e.g. a short summary fragment with no punctuation for clean_name's
    non-alpha stripping to catch)."""
    if not name or not name.strip():
        return False
    if clean_name(name) is None:
        return False
    if summary:
        name_norm = name.strip().lower()
        summary_norm = summary.strip().lower()
        if name_norm == summary_norm or summary_norm.startswith(name_norm):
            return False
    return True


def guess_name_from_text(raw_text: str, max_lines_to_check: int = 8) -> str | None:
    """Fallback for when Gemini doesn't return a usable name: scan the first
    few non-empty lines of the raw extracted text (not just line 0 - resumes
    can have a blank line, a logo placeholder, or a heading before the actual
    name) and return the first one that survives `clean_name` - which now
    also rejects section headings, so this can't return one either."""
    for line in raw_text.splitlines()[:max_lines_to_check]:
        candidate = clean_name(line)
        if candidate:
            return candidate
    return None


def normalize_parsed(parsed: dict) -> dict:
    for field in (
        "summary", "skills", "tools",
        "achievements", "languages", "publications", "volunteer_experience", "leadership",
    ):
        if parsed.get(field) and isinstance(parsed[field], str):
            parsed[field] = [parsed[field]]

    if parsed.get("education"):
        formatted = []
        for edu in parsed["education"]:
            if isinstance(edu, dict):
                degree = edu.get("degree", "")
                institution = edu.get("institution", "")
                year = edu.get("year", "")
                line = degree
                if institution:
                    line += f" - {institution}"
                if year:
                    line += f" - {year}"
                formatted.append(line.strip())
            else:
                formatted.append(str(edu))
        parsed["education"] = formatted

    if parsed.get("certifications"):
        formatted = []
        for cert in parsed["certifications"]:
            if isinstance(cert, dict):
                name = cert.get("name", "")
                dates = cert.get("dates", "") or cert.get("credential_id", "")
                line = name
                if dates:
                    line += f" - {dates}"
                formatted.append(line.strip())
            else:
                formatted.append(str(cert))
        parsed["certifications"] = formatted

    if parsed.get("experience"):
        if isinstance(parsed["experience"], dict):
            parsed["experience"] = [parsed["experience"]]

        clean_exp = []
        for exp in parsed["experience"]:
            if isinstance(exp, str):
                # Gemini occasionally flattens an entry it couldn't fully
                # parse down to a bare string (e.g. just the company name).
                # The old code did `continue` here, silently deleting the
                # entry outright. Keep it as a minimal company-only entry
                # instead - losing role/duration/bullets for one bad entry is
                # still better than losing the job entirely.
                exp = {"company": exp.strip()}
            if not isinstance(exp, dict):
                continue

            company = (exp.get("company") or "").strip()
            role = (exp.get("role") or "").strip()
            duration = (exp.get("duration") or "").strip()
            # `.get("points", [])` only applies the [] default when the key is
            # MISSING - Gemini can return "points": null explicitly, and
            # `.get` returns that None right through the default, crashing
            # the comprehension below with "NoneType is not iterable". `or []`
            # catches both "key missing" and "key present but null".
            points = [str(p).strip() for p in (exp.get("points") or []) if str(p).strip()]
            reason_for_leaving = (exp.get("reason_for_leaving") or "").strip()
            notes = (exp.get("notes") or "").strip()
            is_career_break = bool(exp.get("is_career_break"))
            break_detail = (exp.get("break_detail") or "").strip()

            if not any([company, role, points, break_detail]):
                continue

            # Previously this dict was rebuilt with ONLY these 4 keys
            # (company/role/duration/points) no matter what Gemini actually
            # returned - the real reason "Professional Experience" rendered
            # as bare company names with everything else (reason for
            # leaving, career-break detail, notes) silently discarded even
            # when the LLM extracted it correctly.
            clean_exp.append(
                {
                    "company": company,
                    "role": role,
                    "duration": duration,
                    "points": points,
                    "reason_for_leaving": reason_for_leaving,
                    "notes": notes,
                    "is_career_break": is_career_break,
                    "break_detail": break_detail,
                }
            )
        parsed["experience"] = clean_exp

    if parsed.get("projects"):
        if isinstance(parsed["projects"], dict):
            parsed["projects"] = [parsed["projects"]]

        clean_proj = []
        for proj in parsed["projects"]:
            if isinstance(proj, str):
                # Same failure mode as experience: Gemini occasionally
                # flattens a project it can't fully structure down to a bare
                # string. `enforce_limits` below calls `.get()` on every
                # project, which would raise AttributeError on a plain str
                # and abort the whole conversion - keep it as a minimal
                # title-only entry instead.
                proj = {"title": proj.strip()}
            if not isinstance(proj, dict):
                continue

            title = (proj.get("title") or "").strip()
            role = (proj.get("role") or "").strip()
            description = (proj.get("description") or "").strip()
            technologies = (proj.get("technologies") or "").strip()
            # Same None-vs-missing-key gap as `points` above.
            responsibilities = [str(r).strip() for r in (proj.get("responsibilities") or []) if str(r).strip()]

            if not any([title, description, responsibilities]):
                continue

            clean_proj.append(
                {
                    "title": title,
                    "role": role,
                    "description": description,
                    "technologies": technologies,
                    "responsibilities": responsibilities,
                }
            )
        parsed["projects"] = clean_proj

    return parsed


# --- Reusable list normalization (skills / tools / certifications) --------
#
# Canonical display form for common spelling/punctuation/spacing variants of
# the same term - keyed by a punctuation-and-case-insensitive normalization
# of the raw string (see _normalization_key), so "ReactJS", "React.js",
# "React JS", and "react-js" all collapse to the same key ("reactjs") and
# resolve to one canonical display form. Deliberately NOT exhaustive - an
# item whose key isn't in this table still gets exact-duplicate collapsing
# (see normalize_list), it just keeps whatever spelling/casing the source
# document used instead of a hand-picked canonical one.
_CANONICAL_FORMS: dict[str, str] = {
    "reactjs": "React.js",
    "nodejs": "Node.js",
    "vuejs": "Vue.js",
    "expressjs": "Express.js",
    "nextjs": "Next.js",
    "angularjs": "AngularJS",
    "azuredevops": "Azure DevOps",
    "powershell": "PowerShell",
    "cicd": "CI/CD",
    "dotnet": ".NET",
    "aspnet": "ASP.NET",
    "githubactions": "GitHub Actions",
    "amazonwebservices": "AWS",
    "aws": "AWS",
    "googlecloudplatform": "GCP",
    "gcp": "GCP",
    "microsoftazure": "Azure",
    "azure": "Azure",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "restapi": "REST API",
    "restapis": "REST API",
    "html5": "HTML5",
    "css3": "CSS3",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
}


def _normalization_key(text: str) -> str:
    """Punctuation/case/whitespace-insensitive key used to detect spelling/
    formatting variants of the same term - "ReactJS", "React.js", and
    "React JS" all produce the key "reactjs"."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _canonical_display_form(variants: list[str], key: str) -> str:
    """Picks the display form for a group of items that share the same
    normalization key: the known canonical form if this key is in
    _CANONICAL_FORMS, otherwise the first-seen original spelling/casing -
    preserves the source document's own wording for anything not common
    enough to have a hand-picked canonical form here."""
    return _CANONICAL_FORMS.get(key, variants[0])


def _fuzzy_merge(items: list[str], threshold: float) -> list[str]:
    """Merges near-duplicate strings that survive exact/canonical-key dedup
    - e.g. a typo ("Kubernetess") the canonical-form table doesn't know
    about. Deliberately conservative: a high similarity threshold, and an
    item only ever merges INTO an already-kept one (never invents a new
    merged form). Two DIFFERENT items that happen to look similar as raw
    strings (e.g. two different certification levels of the same
    certification, "...- Associate" vs "...- Professional") would be an
    information-loss bug if merged incorrectly - every merge is logged
    with its similarity score so it's auditable, never silent. Callers for
    whom that risk matters more than catching typos (see
    normalize_certifications) can disable this pass entirely."""
    kept: list[str] = []
    for item in items:
        merged_into = None
        best_ratio = 0.0
        for existing in kept:
            ratio = difflib.SequenceMatcher(None, item.lower(), existing.lower()).ratio()
            if ratio >= threshold and ratio > best_ratio:
                merged_into, best_ratio = existing, ratio
        if merged_into:
            logger.info(
                "Fuzzy-merged %r into %r (similarity=%.2f, threshold=%.2f)",
                item, merged_into, best_ratio, threshold,
            )
        else:
            kept.append(item)
    return kept


def normalize_list(
    items: list[str] | None,
    field_name: str = "items",
    use_fuzzy: bool = True,
    fuzzy_threshold: float = 0.92,
) -> list[str]:
    """Reusable normalization for any flat list of short strings (skills,
    tools, certifications, or similar): strips whitespace and drops empty
    entries, removes exact duplicates (case/whitespace-insensitive),
    merges known spelling/formatting variants to one canonical form (see
    _CANONICAL_FORMS), and optionally merges remaining near-duplicates via
    conservative fuzzy matching (see _fuzzy_merge). Logs a before/after
    count either way.
    """
    before_count = len(items) if items else 0
    logger.info("Before normalization (%s): count = %d", field_name, before_count)

    if not items:
        logger.info("After normalization (%s): count = 0", field_name)
        return []

    # Strip whitespace (leading/trailing + collapse internal), drop empties.
    cleaned = [re.sub(r"\s+", " ", item).strip() for item in items if item and item.strip()]

    # Group by normalization key - collapses exact duplicates AND known
    # spelling/formatting variants into one entry each, first-seen order.
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for item in cleaned:
        key = _normalization_key(item)
        if not key:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    deduped = [_canonical_display_form(groups[key], key) for key in order]
    result = _fuzzy_merge(deduped, fuzzy_threshold) if use_fuzzy else deduped

    logger.info(
        "After normalization (%s): count = %d (removed %d duplicate/invalid entries)",
        field_name, len(result), before_count - len(result),
    )
    return result


def normalize_skills(skills: list[str] | None) -> list[str]:
    return normalize_list(skills, field_name="skills")


def normalize_tools(tools: list[str] | None) -> list[str]:
    return normalize_list(tools, field_name="tools")


def normalize_certifications(certifications: list[str] | None) -> list[str]:
    # Fuzzy matching is OFF by default here: two DIFFERENT certification
    # levels of the same certification (e.g. "AWS Certified Solutions
    # Architect - Associate" vs "... - Professional") can score a high raw-
    # string similarity ratio despite being genuinely different credentials
    # - merging them would silently lose a real credential, which matters
    # more for certifications than catching an occasional spelling variant
    # does. Exact/canonical-key dedup (typos aside) still applies.
    return normalize_list(certifications, field_name="certifications", use_fuzzy=False)


def enforce_limits(parsed: dict) -> dict:
    if isinstance(parsed.get("summary"), list):
        parsed["summary"] = " ".join(parsed["summary"])

    if isinstance(parsed.get("summary"), str):
        parsed["summary"] = ". ".join(parsed["summary"].split(".")[:3]).strip()

    # Dedup/normalize only - no [:N] cap. The prompt now explicitly asks
    # Gemini to extract EVERY skill/tool rather than a "top N" subset (see
    # prompts.py), so capping here would silently re-impose the exact limit
    # the prompt change was meant to remove. normalize_skills/normalize_tools
    # (above) do more than the old dict.fromkeys exact-match dedup: they also
    # merge spelling/formatting variants of the same term ("ReactJS"/
    # "React.js"/"React JS" -> one entry) and catch near-duplicate typos via
    # conservative fuzzy matching - this is the "Normalization" stage of the
    # Extraction -> Normalization -> Resume Intelligence Engine pipeline,
    # applied right here, immediately after extraction/before anything else
    # touches this data.
    if parsed.get("skills"):
        parsed["skills"] = normalize_skills(parsed["skills"])

    if parsed.get("tools"):
        parsed["tools"] = normalize_tools(parsed["tools"])

    if parsed.get("certifications"):
        parsed["certifications"] = normalize_certifications(parsed["certifications"])

    # Achievements/Languages/Publications/Volunteer Experience/Leadership -
    # same conservative, no-cap normalization (whitespace/exact-duplicate
    # cleanup only) as every other flat list field above. Fuzzy matching
    # off, same reasoning as certifications: two genuinely different
    # achievements/languages can read as similar raw strings, and merging
    # them would be an information-loss bug, not a cleanup.
    for field in ("achievements", "languages", "publications", "volunteer_experience", "leadership"):
        if parsed.get(field):
            parsed[field] = normalize_list(parsed[field], field_name=field, use_fuzzy=False)

    if parsed.get("experience"):
        seen = set()
        clean_exp = []
        for exp in parsed["experience"]:
            # Include a snapshot of the bullet points in the dedup key, not
            # just company/role/duration/break_detail - two DIFFERENT real
            # jobs can plausibly share an identical company+role+duration
            # (e.g. Gemini failed to extract dates for either), and without
            # something that actually varies per job, the second one was
            # silently treated as a duplicate of the first and dropped -
            # exactly the "some experience missing" symptom this fixes.
            # Genuine duplicate emissions of the same job still collide
            # (same points too), which is the actual case this dedup exists
            # to catch.
            key = (
                exp.get("company"), exp.get("role"), exp.get("duration"),
                exp.get("break_detail"), tuple(exp.get("points") or []),
            )
            if key not in seen:
                seen.add(key)
                # No [:N] cap - see prompts.py, every real bullet point must
                # be preserved, not truncated to a round number.
                clean_exp.append(exp)
        parsed["experience"] = clean_exp

    # No sentence-count truncation - the prompt now asks for the FULL project
    # description, not a capped one; truncating it here would silently undo
    # that regardless of what Gemini actually returned.

    return parsed
