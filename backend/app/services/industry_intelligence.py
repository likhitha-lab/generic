"""Industry Intelligence Engine - Phase 7 of the Resume Intelligence Engine.

Runs AFTER Phase 6's ATS Intelligence Engine (ats_intelligence.py) and
BEFORE the Resume Builder - see apply_industry_intelligence(), this
module's one public entry point, wired into resume_service.py right after
optimize_for_ats().

WHY A DETERMINISTIC ENGINE, NOT A NEW GEMINI PROMPT: "generate an industry-
specific summary/experience wording" reads as a request to rewrite prose,
which every previous instinct in this codebase would reach for Gemini to
do. That would mean a NEW prompt (a real, non-trivial change to the
Gemini-calling surface) and reintroduces the exact non-determinism Phases
2/3/5/6 were built specifically to eliminate. Instead, this module reuses
the same safe, factual-content-preserving mechanisms those phases already
proved out:
  - Industry-specific Experience wording: reuses Phase 3's
    experience_intelligence.diversify_action_verbs() - already a safe,
    tested, opening-verb-only substitution that never touches the rest of
    a sentence - just feeds it an INDUSTRY-derived verb preference instead
    of a per-job content theme (see _INDUSTRY_TO_THEME).
  - Industry-specific Professional Summary: appends a short, clearly
    factual clause naming the classified industry focus PLUS keywords the
    candidate's own Skills list already contains - never a keyword the
    candidate doesn't already have, and never touches the existing summary
    sentences Phase 1 already wrote. Skipped entirely if the summary
    already reflects the domain (no redundant, robotic-sounding repetition).
  - Industry-specific recruiter terminology / ATS optimization: a small,
    curated per-industry keyword/terminology table, used for a coverage
    check (which industry-standard keywords are present vs missing) -
    modeled on ats_intelligence.py's own missing-keyword pattern:
    suggestion-only, never invents or auto-adds a skill.

Classification (classify_industry) is intentionally its own, coarser
16-domain classifier - it does not replace or alter
resume_structuring.classify_role (Phase 4's 25-category role classifier),
which keeps its own, unrelated responsibility (adaptive resume STRUCTURE).
Both reuse the same established pattern (title-keyword match first, then
skill-keyword-hit-count, fixed tie-break order) for consistency, but are
independent, so this phase cannot regress Phase 4's own classification.

Does not modify extraction, OCR, parsing, Gemini prompts, rendering,
backend APIs, the database, storage, or the frontend.
"""
import logging
import re

from app.services.experience_intelligence import diversify_action_verbs

logger = logging.getLogger(__name__)

INDUSTRIES: tuple[str, ...] = (
    "Java", "Python", ".NET", "Cloud", "Azure", "AWS", "DevOps", "AI",
    "Machine Learning", "Data Engineering", "Cyber Security", "Data Science",
    "Business Analyst", "Project Manager", "Solution Architect", "Enterprise Architect",
    "Informatica", "SAP", "QA",
)

_DEFAULT_INDUSTRY = "Cloud"

# --- Classification -----------------------------------------------------------

# Role-title keywords - checked FIRST (a stated title is the strongest,
# most direct signal, same precedent as resume_structuring.py's seniority/
# role detection). Order matters for tie-breaking multiple title matches.
_TITLE_INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Enterprise Architect": ("enterprise architect",),
    "Solution Architect": ("solution architect", "solutions architect"),
    "Business Analyst": ("business analyst",),
    "Project Manager": ("project manager",),
    "SAP": ("sap consultant", "sap developer", "sap functional consultant", "sap abap developer",
            "sap basis administrator", "sap fico consultant", "sap mm consultant", "sap sd consultant"),
    "Informatica": ("informatica developer", "informatica consultant", "informatica administrator"),
    "QA": ("qa engineer", "test engineer", "sdet", "quality analyst", "quality assurance engineer",
           "automation test engineer", "test automation engineer", "qa lead", "qa analyst"),
}

# Skill/bullet-text keyword-hit counting - checked in this fixed priority
# order for tie-breaking (higher hit count always wins regardless of
# order; this only decides ties and, more importantly, keeps classification
# deterministic and reviewable).
_SKILL_INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "SAP": ("sap", "sap hana", "sap abap", "sap fico", "sap mm", "sap sd", "s/4hana", "sap ecc"),
    "Informatica": ("informatica", "powercenter", "informatica powercenter", "iics", "informatica mdm"),
    "QA": ("test automation", "selenium", "quality assurance", "manual testing", "regression testing",
           "sdet", "test cases", "qa testing"),
    "Cyber Security": ("cybersecurity", "cyber security", "penetration testing", "siem",
                        "firewall", "vulnerability", "iam", "encryption"),
    "AI": ("artificial intelligence", "generative ai", "llm", "nlp", "langchain", "computer vision"),
    "Machine Learning": ("machine learning", "tensorflow", "pytorch", "scikit-learn", "deep learning",
                          "neural network"),
    "Data Science": ("data science", "data scientist", "statistical", "pandas", "numpy"),
    "Data Engineering": ("data pipeline", "etl", "spark", "airflow", "hadoop", "data engineer",
                          "data warehousing"),
    "DevOps": ("devops", "ci/cd", "jenkins", "terraform", "ansible", "kubernetes"),
    "Azure": ("azure",),
    "AWS": ("aws", "amazon web services"),
    "Cloud": ("cloud", "gcp", "google cloud"),
    ".NET": (".net", "asp.net", "c#"),
    "Java": ("java", "spring boot", "spring"),
    "Python": ("python", "django", "flask"),
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _count_mentions(keyword: str, corpus_lower: str) -> int:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
    return len(re.findall(pattern, corpus_lower))


def classify_industry(parsed: dict) -> str:
    """Classifies the resume into one of INDUSTRIES. Checks every job
    title against _TITLE_INDUSTRY_KEYWORDS first (an explicit title beats
    an inferred score); falls back to keyword-hit counting over the
    candidate's skills + all bullet text against _SKILL_INDUSTRY_KEYWORDS,
    highest count wins, ties broken by that dict's own key order. Falls
    back to _DEFAULT_INDUSTRY only if nothing matches at all.
    """
    experience = parsed.get("experience") or []
    titles_text = _normalize_text(" | ".join(str(exp.get("role") or "") for exp in experience))
    for industry, keywords in _TITLE_INDUSTRY_KEYWORDS.items():
        if any(_count_mentions(kw, titles_text) for kw in keywords):
            return industry

    corpus = _normalize_text(
        " ".join(str(s) for s in (parsed.get("skills") or []))
        + " " + " ".join(p for exp in experience for p in (exp.get("points") or []))
        + " " + str(parsed.get("summary") or "")
    )
    best_industry, best_count = None, 0
    for industry, keywords in _SKILL_INDUSTRY_KEYWORDS.items():
        count = sum(_count_mentions(kw, corpus) for kw in keywords)
        if count > best_count:
            best_industry, best_count = industry, count
    return best_industry or _DEFAULT_INDUSTRY


# --- Industry terminology table ----------------------------------------------

# Curated per-industry: a short recruiter-terminology set (used only for
# the summary domain-clause and the ATS coverage check below - never
# force-substituted into bullet text, which would risk unnatural,
# stuffed-sounding wording) and a priority-keyword set (used for the ATS
# coverage check).
_INDUSTRY_TERMINOLOGY: dict[str, dict] = {
    "Java": {"summary_emphasis": "Java-based enterprise application development",
             "recruiter_terms": ("enterprise Java", "Spring Boot", "microservices", "JVM performance"),
             "priority_keywords": ("java", "spring boot", "microservices", "rest api")},
    "Python": {"summary_emphasis": "Python-based backend and automation engineering",
               "recruiter_terms": ("Python development", "scripting", "automation", "backend services"),
               "priority_keywords": ("python", "django", "flask", "automation")},
    ".NET": {"summary_emphasis": ".NET enterprise application development",
             "recruiter_terms": (".NET development", "C#", "ASP.NET", "enterprise applications"),
             "priority_keywords": (".net", "c#", "asp.net", "sql server")},
    "Cloud": {"summary_emphasis": "cloud platform engineering and architecture",
              "recruiter_terms": ("cloud architecture", "scalability", "cost optimization", "high availability"),
              "priority_keywords": ("cloud", "scalability", "kubernetes", "terraform")},
    "Azure": {"summary_emphasis": "Microsoft Azure cloud engineering",
              "recruiter_terms": ("Azure architecture", "cloud migration", "Azure DevOps", "cost optimization"),
              "priority_keywords": ("azure", "azure devops", "aks", "terraform")},
    "AWS": {"summary_emphasis": "AWS cloud engineering",
            "recruiter_terms": ("AWS architecture", "cloud migration", "cost optimization", "high availability"),
            "priority_keywords": ("aws", "ec2", "s3", "lambda")},
    "DevOps": {"summary_emphasis": "DevOps and CI/CD automation",
               "recruiter_terms": ("CI/CD pipelines", "infrastructure as code", "automation", "release engineering"),
               "priority_keywords": ("ci/cd", "terraform", "jenkins", "kubernetes")},
    "AI": {"summary_emphasis": "AI solution development",
           "recruiter_terms": ("generative AI", "model deployment", "intelligent automation", "NLP"),
           "priority_keywords": ("artificial intelligence", "llm", "nlp", "generative ai")},
    "Machine Learning": {"summary_emphasis": "machine learning model development",
                          "recruiter_terms": ("model development", "MLOps", "predictive modeling", "deep learning"),
                          "priority_keywords": ("machine learning", "tensorflow", "pytorch", "mlops")},
    "Data Engineering": {"summary_emphasis": "data pipeline and platform engineering",
                          "recruiter_terms": ("data pipelines", "ETL", "data warehousing", "data platform"),
                          "priority_keywords": ("etl", "spark", "airflow", "data pipeline")},
    "Cyber Security": {"summary_emphasis": "cybersecurity and risk mitigation",
                        "recruiter_terms": ("security posture", "vulnerability management", "compliance", "risk mitigation"),
                        "priority_keywords": ("security", "compliance", "encryption", "iam")},
    "Data Science": {"summary_emphasis": "data science and statistical analysis",
                      "recruiter_terms": ("statistical modeling", "data-driven insights", "predictive analytics"),
                      "priority_keywords": ("data science", "pandas", "numpy", "statistics")},
    "Business Analyst": {"summary_emphasis": "business analysis and requirements management",
                          "recruiter_terms": ("stakeholder management", "requirements gathering", "process improvement"),
                          "priority_keywords": ("requirements", "stakeholder", "business analysis")},
    "Project Manager": {"summary_emphasis": "project delivery and stakeholder management",
                         "recruiter_terms": ("project delivery", "stakeholder management", "risk management", "agile"),
                         "priority_keywords": ("project management", "agile", "stakeholder", "budget")},
    "Solution Architect": {"summary_emphasis": "solution architecture and technical design",
                            "recruiter_terms": ("solution architecture", "system design", "technical leadership"),
                            "priority_keywords": ("architecture", "system design", "microservices")},
    "Enterprise Architect": {"summary_emphasis": "enterprise architecture and technology strategy",
                              "recruiter_terms": ("enterprise architecture", "technology strategy", "governance"),
                              "priority_keywords": ("enterprise architecture", "governance", "roadmap")},
    "SAP": {"summary_emphasis": "SAP ERP implementation and functional consulting",
            "recruiter_terms": ("SAP implementation", "ERP", "S/4HANA", "SAP modules"),
            "priority_keywords": ("sap", "s/4hana", "sap hana", "erp")},
    "Informatica": {"summary_emphasis": "Informatica-based ETL and data integration engineering",
                     "recruiter_terms": ("ETL development", "data integration", "Informatica PowerCenter", "data quality"),
                     "priority_keywords": ("informatica", "etl", "data integration", "powercenter")},
    "QA": {"summary_emphasis": "quality assurance and test automation engineering",
           "recruiter_terms": ("test automation", "quality assurance", "test planning", "defect management"),
           "priority_keywords": ("test automation", "selenium", "quality assurance", "regression testing")},
}


# Priority 4 fix - root cause: `priority_keywords` above are deliberately
# stored all-lowercase for case-insensitive MATCHING against resume text,
# but generate_industry_aligned_summary previously interpolated them
# directly into displayed prose ("...with hands-on expertise in java and
# spring boot."), reading as an unproofread typo rather than a genuine
# recruiter-quality sentence. `.title()` alone would mangle the acronyms
# this table also contains ("aws" -> "Aws", "ci/cd" -> "Ci/Cd"), so this is
# a small curated override table for exactly those cases, falling back to
# `.title()` for everything else (which is correct for the rest - "spring
# boot" -> "Spring Boot", "python" -> "Python", etc.).
_ACRONYM_DISPLAY_OVERRIDES: dict[str, str] = {
    "aws": "AWS", "gcp": "GCP", "ec2": "EC2", "s3": "S3", "etl": "ETL",
    "iam": "IAM", "aks": "AKS", "ci/cd": "CI/CD", "llm": "LLM", "nlp": "NLP",
    "mlops": "MLOps", "rest api": "REST API", "sql server": "SQL Server",
    ".net": ".NET", "c#": "C#", "asp.net": "ASP.NET", "azure devops": "Azure DevOps",
    "tensorflow": "TensorFlow", "pytorch": "PyTorch", "numpy": "NumPy",
    "generative ai": "Generative AI",
    "sap": "SAP", "s/4hana": "S/4HANA", "sap hana": "SAP HANA", "erp": "ERP",
    "powercenter": "PowerCenter",
}


def _display_case(term: str) -> str:
    """Proper-cases a lowercase terminology keyword for DISPLAY in a
    generated summary sentence - see _ACRONYM_DISPLAY_OVERRIDES above for
    why this isn't just `.title()`."""
    return _ACRONYM_DISPLAY_OVERRIDES.get(term.lower(), term.title())


def get_industry_terminology(industry: str) -> dict:
    """Returns the terminology table entry for `industry`, falling back to
    _DEFAULT_INDUSTRY's entry for an unrecognized value (defensive - never
    raises)."""
    return _INDUSTRY_TERMINOLOGY.get(industry, _INDUSTRY_TERMINOLOGY[_DEFAULT_INDUSTRY])


# --- Industry-specific Professional Summary ----------------------------------

# Leadership evidence - a job TITLE containing one of these, or a bullet
# stating one of these leadership actions, is treated as genuine evidence
# of leadership experience (STEP 7: Professional Summary Improvement asks
# for a Leadership dimension "where explicit evidence exists" - same
# never-invent standard as Business Impact/STEP 9).
_LEADERSHIP_TITLE_KEYWORDS = ("lead", "manager", "director", "head of", "chief", "vp", "principal", "staff")
_LEADERSHIP_BULLET_KEYWORDS = ("led a team", "led the team", "managed a team", "mentored", "mentoring")

# Pre-sales / enterprise-solution-design evidence (STEP 5: Executive
# Summary asks for these two dimensions specifically, alongside Leadership
# - same never-invent standard: only added when the candidate's own
# titles/bullets/skills genuinely name one of these.
_PRESALES_KEYWORDS = ("pre-sales", "presales", "solution proposal", "rfp", "proof of concept", "client demo")
_ENTERPRISE_SOLUTION_KEYWORDS = ("enterprise architecture", "enterprise solution", "solution architecture", "digital transformation")


def _has_leadership_evidence(parsed: dict) -> bool:
    for exp in parsed.get("experience") or []:
        role = str(exp.get("role") or "").lower()
        if any(kw in role for kw in _LEADERSHIP_TITLE_KEYWORDS):
            return True
        bullets_lower = " ".join(exp.get("points") or []).lower()
        if any(kw in bullets_lower for kw in _LEADERSHIP_BULLET_KEYWORDS):
            return True
    return False


def _resume_text_corpus(parsed: dict) -> str:
    skills_text = " ".join(str(s) for s in (parsed.get("skills") or []))
    bullets_text = " ".join(p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or []))
    return _normalize_text(f"{skills_text} {bullets_text}")


def _has_presales_evidence(parsed: dict) -> bool:
    return any(kw in _resume_text_corpus(parsed) for kw in _PRESALES_KEYWORDS)


def _has_enterprise_solution_evidence(parsed: dict) -> bool:
    return any(kw in _resume_text_corpus(parsed) for kw in _ENTERPRISE_SOLUTION_KEYWORDS)


def generate_industry_aligned_summary(parsed: dict, industry: str) -> str:
    """Appends short, factual clauses to the EXISTING summary (never
    rewrites or removes any existing sentence): a domain-focus clause
    naming the classified industry focus plus up to two priority keywords
    the candidate's own Skills list (or the summary itself) already
    evidences; a leadership clause ONLY if the work history evidences it
    (see _has_leadership_evidence); a pre-sales clause ONLY if evidenced
    (see _has_presales_evidence); and an enterprise-solution-design clause
    ONLY if evidenced (see _has_enterprise_solution_evidence) - STEP 5's
    Executive Summary dimensions beyond the domain/technology focus
    itself. Each clause is added independently, only when it has genuine
    evidence to draw on and isn't already redundant with the existing
    summary text; any combination (including none) may be added.
    """
    summary = str(parsed.get("summary") or "").strip()
    terminology = get_industry_terminology(industry)
    skills_lower = {_normalize_text(str(s)) for s in (parsed.get("skills") or [])}
    summary_lower = _normalize_text(summary)

    clauses: list[str] = []

    present_keywords = [
        kw for kw in terminology["priority_keywords"]
        if _normalize_text(kw) in skills_lower or _count_mentions(kw, summary_lower)
    ]
    if present_keywords and not any(_count_mentions(kw, summary_lower) for kw in present_keywords):
        tag_keywords = [_display_case(kw) for kw in present_keywords[:2]]
        clauses.append(f"Focused on {terminology['summary_emphasis']}, with hands-on expertise in {' and '.join(tag_keywords)}.")

    if _has_leadership_evidence(parsed) and "leader" not in summary_lower and "mentor" not in summary_lower:
        clauses.append("Brings hands-on leadership experience guiding and mentoring engineering teams.")

    if _has_presales_evidence(parsed) and "pre-sales" not in summary_lower and "presales" not in summary_lower:
        clauses.append("Experienced in pre-sales activities, including solution proposals and client-facing engagements.")

    if _has_enterprise_solution_evidence(parsed) and "enterprise" not in summary_lower:
        clauses.append("Skilled in enterprise solution design and driving digital transformation initiatives.")

    if not clauses:
        return summary
    if summary and not summary.endswith((".", "!", "?")):
        summary += "."
    return (summary + " " + " ".join(clauses)).strip()


# --- Industry-specific Experience wording ------------------------------------

# Maps each industry onto one of experience_intelligence.py's own theme
# keys (see _THEME_VERB_PREFERENCE there) - reuses that module's already-
# tested, opening-verb-only diversification rather than a second,
# duplicate verb-preference table.
_INDUSTRY_TO_THEME: dict[str, str] = {
    "Java": "Backend Engineering", "Python": "Backend Engineering", ".NET": "Backend Engineering",
    "Cloud": "Cloud Modernization", "Azure": "Cloud Migration", "AWS": "Cloud Migration",
    "DevOps": "DevOps", "AI": "AI", "Machine Learning": "Machine Learning",
    "Data Engineering": "Data Engineering", "Cyber Security": "Security", "Data Science": "Machine Learning",
    "Business Analyst": "Analytics", "Project Manager": "Digital Transformation",
    "Solution Architect": "Architecture", "Enterprise Architect": "Architecture",
    # SAP's own theme ("ERP") already existed in experience_intelligence.
    # py's _THEME_VERB_PREFERENCE (Implemented/Configured/Delivered/Led -
    # fits ERP module implementation work exactly) but was never reachable
    # from here before, since no industry mapped to it.
    "SAP": "ERP",
    # Informatica is fundamentally ETL/data-pipeline work - "Data
    # Engineering"'s verb pool (Engineered/Built/Automated/Optimized) fits
    # better than a generic "Integration" theme would.
    "Informatica": "Data Engineering",
    # No dedicated "Testing"/"QA" theme exists in experience_intelligence.py
    # - "Automation" (Automated/Engineered/Streamlined/Accelerated) is the
    # closest genuine fit for modern test-automation-heavy QA work.
    "QA": "Automation",
}


def apply_industry_wording(parsed: dict, industry: str) -> int:
    """STEP: Industry-specific Experience wording. For every non-career-
    break job, re-runs experience_intelligence.diversify_action_verbs()
    with the industry's own verb-preference theme (see _INDUSTRY_TO_THEME)
    instead of that job's own project theme - a repeated opening verb now
    gets replaced with an industry-appropriate synonym (e.g. "Hardened"/
    "Secured" for Cyber Security, "Trained"/"Engineered" for Machine
    Learning) rather than a generically-themed one. Only ever swaps an
    OPENING verb that's already repeated - never touches a unique opening,
    never touches anything after the opening verb. Returns the number of
    bullets changed.
    """
    theme = _INDUSTRY_TO_THEME.get(industry, "Application Development")
    changed = 0
    for exp in parsed.get("experience") or []:
        if exp.get("is_career_break"):
            continue
        points = exp.get("points") or []
        if not points:
            continue
        new_points = diversify_action_verbs(points, theme)
        if new_points != points:
            changed += sum(1 for old, new in zip(points, new_points) if old != new)
            exp["points"] = new_points
    return changed


# --- Industry-specific ATS optimization / recruiter terminology -------------

def check_industry_ats_alignment(parsed: dict, industry: str) -> dict:
    """STEP: Industry-specific ATS optimization. Checks how many of the
    industry's priority keywords are present anywhere (skills or bullet
    text) - returns {"coverage_ratio", "present", "missing"}. `missing` is
    a SUGGESTION list only (mirrors ats_intelligence.detect_missing_
    keywords's own contract) - never mutates `parsed`, never asserts the
    candidate has a missing keyword; a caller must present these as
    optional, "add only if genuinely applicable" considerations.
    """
    terminology = get_industry_terminology(industry)
    corpus = _normalize_text(
        " ".join(str(s) for s in (parsed.get("skills") or []))
        + " " + " ".join(p for exp in (parsed.get("experience") or []) for p in (exp.get("points") or []))
    )
    present = [kw for kw in terminology["priority_keywords"] if _count_mentions(kw, corpus)]
    missing = [kw for kw in terminology["priority_keywords"] if kw not in present]
    coverage_ratio = round(len(present) / len(terminology["priority_keywords"]), 2)
    return {
        "coverage_ratio": coverage_ratio,
        "present": present,
        "missing": [kw.title() if kw.islower() else kw for kw in missing],
    }


# --- Orchestration -------------------------------------------------------------

def apply_industry_intelligence(parsed: dict) -> tuple[dict, dict]:
    """Public entry point. Classifies the resume's industry, applies the
    two safe content adjustments (summary domain-clause,
    industry-weighted verb diversification) directly to `parsed`, and
    returns (parsed, report) - `report` is for internal logging only,
    never rendered.
    """
    industry = classify_industry(parsed)
    terminology = get_industry_terminology(industry)

    original_summary = str(parsed.get("summary") or "")
    parsed["summary"] = generate_industry_aligned_summary(parsed, industry)
    summary_changed = parsed["summary"] != original_summary

    bullets_changed = apply_industry_wording(parsed, industry)
    ats_alignment = check_industry_ats_alignment(parsed, industry)

    report = {
        "industry": industry,
        "recruiter_terms": terminology["recruiter_terms"],
        "summary_updated": summary_changed,
        "bullets_updated": bullets_changed,
        "ats_alignment": ats_alignment,
    }
    logger.info(
        "Industry Intelligence Engine: classified as %r (summary_updated=%s, bullets_updated=%d, "
        "ats_coverage=%.2f)", industry, summary_changed, bullets_changed, ats_alignment["coverage_ratio"],
    )
    return parsed, report
