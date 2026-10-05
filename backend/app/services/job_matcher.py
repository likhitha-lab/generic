"""Job Description Matcher - Phase 9c (Enterprise features).

Covers three related asks: Job Description matching, Missing Skill
recommendations, and Resume tailoring for a specific job. All three are
pure analysis - NONE of them mutate `parsed`, and "tailoring" specifically
never rewrites resume content automatically: it only returns a SUGGESTED
skill ordering (for the caller to decide whether to apply) and a list of
suggestions. This is a deliberately more conservative stance than a
typical "auto-tailor for this job" feature, for one reason: doing this to
CONTENT (not just skill order) risks the exact thing every phase of this
engine has treated as non-negotiable - inventing or exaggerating a skill/
claim to better match a job description the candidate doesn't actually
have. A missing keyword is always surfaced as "add ONLY if genuinely
applicable", never auto-inserted.

Job-description keyword extraction reuses skill_intelligence.classify_skill
(Phase 2) against candidate phrases tokenized from the JD text - the same
technical-keyword recognition already used for a candidate's own resume,
applied to the JD instead, so "does this JD mention a real, recognized
technology" is answered the same way on both sides of the match.
"""
import re

from app.services.skill_intelligence import classify_skill


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def extract_jd_keywords(job_description: str) -> list[str]:
    """Tokenizes a job description into individual words, then keeps the
    ones skill_intelligence.classify_skill recognizes as a genuine
    technical keyword - the same recognition test already applied to a
    candidate's own Skills list, applied here to the JD side of the match
    instead. A word that doesn't classify alone gets one more chance
    combined with its IMMEDIATE neighbor (for two-word product names like
    "machine learning"), but only if that neighbor ALSO didn't already
    classify on its own - otherwise a filler word next to a real keyword
    (e.g. "and Kubernetes") would get swallowed into a two-word "keyword"
    instead of "Kubernetes" being recognized cleanly by itself. This two-
    pass, single-word-first approach is deliberate: classify_skill does a
    SUBSTRING/word-boundary search internally (by design, for checking an
    already-mostly-clean extracted skill string), so testing a whole raw
    sentence chunk against it (an earlier version of this function did)
    lets a real keyword buried inside a long, mostly-irrelevant phrase
    "match" the entire phrase - exactly the false-positive this two-pass
    word-level approach avoids. Deduplicated, original casing preserved.
    """
    if not job_description:
        return []
    raw_words = [w.strip(".,;:()!?\"'") for w in job_description.split()]
    words = [w for w in raw_words if w]
    single_match = [classify_skill(w) is not None for w in words]

    seen: set[str] = set()
    keywords: list[str] = []
    i = 0
    n = len(words)
    while i < n:
        if single_match[i]:
            key = _normalize_text(words[i])
            if key not in seen:
                seen.add(key)
                keywords.append(words[i])
            i += 1
            continue
        if i + 1 < n and not single_match[i + 1]:
            two_gram = f"{words[i]} {words[i + 1]}"
            if classify_skill(two_gram) is not None:
                key = _normalize_text(two_gram)
                if key not in seen:
                    seen.add(key)
                    keywords.append(two_gram)
                i += 2
                continue
        i += 1
    return keywords


def _resume_keyword_corpus(parsed: dict) -> str:
    skills = " ".join(str(s) for s in (parsed.get("skills") or []))
    tools = " ".join(str(t) for t in (parsed.get("tools") or []))
    bullets = " ".join(
        p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or [])
    )
    project_text = " ".join(
        f"{p.get('title', '')} {p.get('technologies', '')} " + " ".join(p.get("responsibilities") or [])
        for p in (parsed.get("projects") or [])
    )
    return _normalize_text(" ".join([skills, tools, bullets, project_text]))


def match_resume_to_job(parsed: dict, job_description: str) -> dict:
    """STEP: Job Description matching + Missing Skill recommendations.
    Returns {"match_score" (0-100), "matched_keywords", "missing_keywords",
    "jd_keyword_count"}. `missing_keywords` is a SUGGESTION list only -
    keywords the JD mentions that aren't demonstrated anywhere in the
    resume; never asserts the candidate has them, never mutates `parsed`.
    """
    jd_keywords = extract_jd_keywords(job_description)
    if not jd_keywords:
        return {"match_score": 0, "matched_keywords": [], "missing_keywords": [], "jd_keyword_count": 0}

    corpus = _resume_keyword_corpus(parsed)
    matched, missing = [], []
    for keyword in jd_keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, corpus):
            matched.append(keyword)
        else:
            missing.append(keyword)

    match_score = round(100 * len(matched) / len(jd_keywords))
    return {
        "match_score": match_score,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "jd_keyword_count": len(jd_keywords),
    }


def tailor_resume_for_job(parsed: dict, job_description: str) -> dict:
    """STEP: Resume tailoring for a specific job. Deliberately conservative
    - returns a SUGGESTED skill ordering (JD-matched skills first, nothing
    added or removed) and text suggestions, but never mutates `parsed`
    itself; the caller decides whether/how to apply the suggested order.
    Never invents a skill the candidate doesn't have - missing JD keywords
    are only ever surfaced as an optional, clearly-labeled consideration.
    """
    match = match_resume_to_job(parsed, job_description)
    skills = parsed.get("skills") or []
    matched_lower = {kw.lower() for kw in match["matched_keywords"]}
    matched_first = [s for s in skills if str(s).lower() in matched_lower]
    rest = [s for s in skills if str(s).lower() not in matched_lower]
    suggested_order = matched_first + rest

    suggestions: list[str] = []
    if match["missing_keywords"]:
        suggestions.append(
            "This job description mentions the following that aren't currently reflected in your "
            "resume - add them ONLY if genuinely applicable, never fabricate experience: "
            + ", ".join(match["missing_keywords"][:8])
        )
    if suggested_order != skills:
        suggestions.append(
            "Consider reordering Technical Skills to lead with the skills this job description "
            "emphasizes most."
        )

    return {
        "match_score": match["match_score"],
        "suggested_skill_order": suggested_order,
        "suggestions": suggestions,
    }
