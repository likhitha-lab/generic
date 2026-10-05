"""Resume Structuring Engine - Phase 4 of the Resume Intelligence Engine.

Runs AFTER Phase 1-3 (resume_optimizer.optimize_resume, which itself
delegates to skill_intelligence.py and experience_intelligence.py) have
fully produced `parsed`, and BEFORE the Resume Builder (file_generator.py)
renders it - see build_structure_plan(), this module's one public entry
point, wired into resume_service.py right before the existing
build_resume_file() call.

WHY THIS EXISTS (see the accompanying architecture review for the full
analysis): file_generator.py's section order and heading text were, before
this phase, a single hardcoded sequence used for every candidate -
Contact, Professional Summary, Technical Skills, Tools, Education,
Certifications, Professional Experience, Projects, Achievements,
regardless of whether the candidate is a fresher with no professional
experience or a CTO with two decades of enterprise leadership. A real
recruiter/resume writer would never structure those two resumes the same
way: a fresher's strongest asset (Education, Projects) needs to lead;
a CTO's strongest asset (Enterprise Experience, Leadership) needs to lead,
under executive-appropriate section names ("Executive Summary" instead of
"Professional Summary"). This module is the deterministic decision-making
layer that figures out WHICH structure fits a given candidate and hands
file_generator.py a plan (see file_generator.py's Phase 4 docstring
paragraph and its `section_plan`/`extra_content`/`max_pages_override`
parameters) instead of file_generator.py ever having its own opinion about
who a candidate is.

PIPELINE (see build_structure_plan(), which runs these in order):

    Resume JSON (parsed, already fully optimized by Phase 1-3)
      -> Candidate Analysis      (_analyze_candidate)
      -> Seniority Detection     (detect_seniority)
      -> Role Classification     (classify_role)
      -> Section Prioritization  (_TEMPLATES lookup by seniority's layout family)
      -> Section Ordering        (the chosen template's fixed (key, label) list)
      -> Adaptive Resume Layout  (executive-section content derivation + page budget)
      -> Final Structured Resume (a StructurePlan)

Never modifies `parsed` itself, never re-derives skill/experience/summary
CONTENT (that's Phase 1-3's job, explicitly out of scope here per the
mission's own "Do NOT modify Resume/Skill/Experience Intelligence") -
this module only ever decides section PRESENCE, ORDER, LABEL TEXT, and a
page-count budget, plus a small amount of derived section content that is
always a strict SUBSET or a straight relabeling of data Phase 1-3 already
produced (see generate_executive_sections) - never a new fact, metric, or
claim.
"""
import re

from app.services.experience_intelligence import has_impact_signal

# --- Seniority levels (STEP 3) ------------------------------------------------
#
# In increasing order of seniority - used only for directional comparisons
# in tests/logging, not as a numeric score itself (see _score_seniority).
SENIORITY_LEVELS: tuple[str, ...] = (
    "Intern", "Fresher", "Junior Engineer", "Mid-Level Engineer", "Senior Engineer",
    "Lead Engineer", "Staff Engineer", "Principal Engineer", "Architect",
    "Solution Architect", "Enterprise Architect", "Engineering Manager",
    "Director", "VP Engineering", "CTO",
)

# Explicit role-title keyword overrides - checked FIRST and, if matched, win
# outright over the weighted-signal score below. A title is the single
# strongest, most direct seniority signal a resume states about itself;
# checked most-senior-first so e.g. "Senior Solution Architect" resolves to
# "Solution Architect" (a more senior/specific title) rather than stopping
# at the "Senior Engineer" keyword.
_TITLE_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("chief technology officer", "CTO"), ("cto", "CTO"),
    ("vp engineering", "VP Engineering"), ("vp of engineering", "VP Engineering"),
    ("vice president of engineering", "VP Engineering"), ("vice president, engineering", "VP Engineering"),
    ("director of engineering", "Director"), ("engineering director", "Director"), ("director", "Director"),
    ("engineering manager", "Engineering Manager"), ("eng manager", "Engineering Manager"),
    ("enterprise architect", "Enterprise Architect"),
    ("solution architect", "Solution Architect"), ("solutions architect", "Solution Architect"),
    ("principal engineer", "Principal Engineer"), ("principal consultant", "Principal Engineer"),
    ("staff engineer", "Staff Engineer"),
    ("architect", "Architect"),
    ("technical lead", "Lead Engineer"), ("tech lead", "Lead Engineer"), ("lead engineer", "Lead Engineer"),
    ("team lead", "Lead Engineer"),
    ("senior", "Senior Engineer"),
    ("mid-level", "Mid-Level Engineer"), ("mid level", "Mid-Level Engineer"),
    ("junior", "Junior Engineer"),
    ("intern", "Intern"), ("internship", "Intern"),
    ("fresher", "Fresher"), ("trainee", "Fresher"), ("graduate", "Fresher"), ("entry level", "Fresher"),
    ("entry-level", "Fresher"),
)

_LEADERSHIP_KEYWORDS = ("led team", "led a team", "managed a team", "managed the team", "directed the",
                        "mentored", "mentoring", "supervised", "oversaw")
_ARCHITECTURE_KEYWORDS = ("architecture", "system design", "solution design", "solution architecture",
                          "architected", "designed the platform")
_MENTORING_KEYWORDS = ("mentored", "mentoring", "trained junior", "coached")
_BUSINESS_KEYWORDS = ("stakeholder", "budget", "roadmap", "business strategy", "p&l", "revenue",
                      "enterprise-wide", "cross-functional")
_TEAM_SIZE_RE = re.compile(r"team of (\d+)", re.IGNORECASE)

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PRESENT_RE = re.compile(r"present|current|till date|ongoing", re.IGNORECASE)


def _years_of_experience(experience: list[dict] | None) -> float:
    """Same estimation approach as file_generator.py's own
    _years_of_experience (re-implemented locally rather than importing a
    private helper across modules, consistent with this pipeline's
    established pattern of small parallel implementations rather than
    cross-module private imports) - the span between the earliest and
    latest year mentioned across every job's `duration` string."""
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


def _latest_role(experience: list[dict] | None) -> str:
    for exp in experience or []:
        if not exp.get("is_career_break") and exp.get("role"):
            return str(exp["role"])
    return ""


def _all_bullets(experience: list[dict] | None) -> list[str]:
    return [p for exp in (experience or []) for p in (exp.get("points") or [])]


def _count_keyword_hits(text_lower: str, keywords: tuple[str, ...]) -> int:
    return sum(text_lower.count(keyword) for keyword in keywords)


def _contains_keyword(text: str, keyword: str) -> bool:
    """Word-boundary-safe containment check - a bare `keyword in text`
    substring check would wrongly match short keywords like "cto" INSIDE
    an unrelated word ("director" literally contains "cto" - di-REC-T-O-r -
    so "Director of Engineering" would otherwise misclassify as CTO)."""
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _role_title_seniority_override(role_titles: list[str]) -> str | None:
    """Checks every job title (not just the latest) against _TITLE_OVERRIDES
    and returns the MOST SENIOR match found anywhere - an explicit title
    beats an inferred score (STEP 3: "never rely only on years")."""
    best_index = -1
    best_level = None
    combined = " | ".join(t.lower() for t in role_titles if t)
    for keyword, level in _TITLE_OVERRIDES:
        if _contains_keyword(combined, keyword):
            index = SENIORITY_LEVELS.index(level)
            if index > best_index:
                best_index, best_level = index, level
    return best_level


def _analyze_candidate(parsed: dict) -> dict:
    """STEP: Candidate Analysis - gathers every raw signal Seniority
    Detection/Role Classification need, all read-only from `parsed`."""
    experience = parsed.get("experience") or []
    bullets = _all_bullets(experience)
    combined_text = " ".join(bullets + [str(parsed.get("summary") or "")]).lower()
    role_titles = [str(exp.get("role") or "") for exp in experience]

    team_sizes = [int(m) for m in _TEAM_SIZE_RE.findall(combined_text)]
    return {
        "years": _years_of_experience(experience),
        "num_jobs": len([e for e in experience if not e.get("is_career_break")]),
        "num_skills": len(parsed.get("skills") or []),
        "leadership_hits": _count_keyword_hits(combined_text, _LEADERSHIP_KEYWORDS),
        "architecture_hits": _count_keyword_hits(combined_text, _ARCHITECTURE_KEYWORDS),
        "mentoring_hits": _count_keyword_hits(combined_text, _MENTORING_KEYWORDS),
        "business_hits": _count_keyword_hits(combined_text, _BUSINESS_KEYWORDS),
        "max_team_size": max(team_sizes) if team_sizes else 0,
        "role_titles": role_titles,
        "latest_role": _latest_role(experience),
    }


def _score_seniority(signals: dict) -> str:
    """Weighted-signal fallback used ONLY when no explicit role-title
    override matches (see detect_seniority) - combines years, leadership,
    architecture ownership, mentoring, business responsibility, and career
    breadth (num_jobs) into one score, then maps it onto SENIORITY_LEVELS.
    Deliberately NOT years-only (STEP 3's explicit requirement): a candidate
    with few years but heavy leadership/architecture signal scores higher
    than years alone would suggest, and vice versa.
    """
    score = (
        min(signals["years"], 20) * 1.0
        + signals["leadership_hits"] * 3
        + signals["architecture_hits"] * 3
        + signals["mentoring_hits"] * 2
        + signals["business_hits"] * 2
        + min(signals["max_team_size"], 20) * 0.5
        + min(signals["num_jobs"], 6) * 1.0
    )
    # Thresholds map the additive score onto SENIORITY_LEVELS - deliberately
    # coarse (this is a fallback for when there's no explicit title at all,
    # e.g. a resume with only bare bullet points and no job titles) rather
    # than pinpoint-precise; directional correctness (more signal -> higher
    # tier) matters far more than the exact boundary.
    thresholds = (
        (0, "Fresher"), (3, "Junior Engineer"), (7, "Mid-Level Engineer"),
        (13, "Senior Engineer"), (19, "Lead Engineer"), (25, "Staff Engineer"),
        (31, "Principal Engineer"),
    )
    level = "Fresher"
    for threshold, candidate_level in thresholds:
        if score >= threshold:
            level = candidate_level
    return level


def detect_seniority(parsed: dict) -> str:
    """STEP 3 - Seniority Detection. Returns one of SENIORITY_LEVELS.
    Prefers an explicit role-title match (_role_title_seniority_override) -
    the strongest, most direct signal a resume can state about itself -
    and only falls back to the weighted signal score (_score_seniority)
    when no job title in the resume maps to any known seniority keyword at
    all. Combines years of experience, role titles, leadership, project/
    career complexity (number of distinct roles), architecture ownership,
    mentoring, and business responsibility signals - never years alone.
    """
    signals = _analyze_candidate(parsed)
    title_override = _role_title_seniority_override(signals["role_titles"])
    if title_override:
        return title_override
    if signals["years"] == 0 and signals["num_jobs"] == 0:
        return "Fresher"
    return _score_seniority(signals)


# --- Role Classification (STEP 4) --------------------------------------------

ROLE_CATEGORIES: tuple[str, ...] = (
    "Backend Developer", "Frontend Developer", "Full Stack Developer", "Python Developer",
    "Java Developer", ".NET Developer", "Data Engineer", "Machine Learning Engineer",
    "AI Engineer", "Cloud Engineer", "Azure Engineer", "AWS Engineer", "DevOps Engineer",
    "Platform Engineer", "Site Reliability Engineer", "Cyber Security Engineer",
    "Business Analyst", "QA Engineer", "Automation Engineer", "Project Manager",
    "Product Manager", "Solution Architect", "Enterprise Architect", "Technical Lead",
    "Engineering Manager",
)

_ROLE_TITLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Site Reliability Engineer": ("site reliability", "sre"),
    "DevOps Engineer": ("devops",),
    "Platform Engineer": ("platform engineer",),
    "Cyber Security Engineer": ("security engineer", "cybersecurity", "cyber security"),
    "Machine Learning Engineer": ("machine learning engineer", "ml engineer"),
    "AI Engineer": ("ai engineer", "artificial intelligence engineer"),
    "Data Engineer": ("data engineer",),
    "Azure Engineer": ("azure engineer",),
    "AWS Engineer": ("aws engineer",),
    "Cloud Engineer": ("cloud engineer",),
    ".NET Developer": (".net developer", "dotnet developer"),
    "Java Developer": ("java developer",),
    "Python Developer": ("python developer",),
    "Frontend Developer": ("frontend developer", "front-end developer", "ui developer"),
    "Full Stack Developer": ("full stack developer", "full-stack developer"),
    "Backend Developer": ("backend developer", "back-end developer"),
    "Business Analyst": ("business analyst",),
    "QA Engineer": ("qa engineer", "quality assurance engineer", "test engineer"),
    "Automation Engineer": ("automation engineer",),
    "Project Manager": ("project manager",),
    "Product Manager": ("product manager",),
    "Enterprise Architect": ("enterprise architect",),
    "Solution Architect": ("solution architect", "solutions architect"),
    "Technical Lead": ("technical lead", "tech lead"),
    "Engineering Manager": ("engineering manager",),
}

# Fallback signal for when no job title matches directly - inferred from
# the candidate's own (already Skill-Intelligence-ranked) skills list.
_ROLE_SKILL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "DevOps Engineer": ("terraform", "jenkins", "ansible", "ci/cd"),
    "Cyber Security Engineer": ("oauth", "siem", "penetration testing", "cybersecurity"),
    "Machine Learning Engineer": ("tensorflow", "pytorch", "scikit-learn", "machine learning"),
    "Data Engineer": ("spark", "airflow", "etl", "data pipeline", "hadoop"),
    "Azure Engineer": ("azure",),
    "AWS Engineer": ("aws",),
    "Cloud Engineer": ("gcp", "cloud"),
    ".NET Developer": (".net", "asp.net", "c#"),
    "Java Developer": ("java", "spring boot", "spring"),
    "Python Developer": ("python", "django", "flask", "fastapi"),
    "Frontend Developer": ("react", "angular", "vue", "javascript", "typescript"),
    "Backend Developer": ("node.js", "express", "django", "spring boot"),
}

_DEFAULT_ROLE = "Backend Developer"


def _count_keyword_occurrences(text: str, keywords: tuple[str, ...]) -> int:
    """Word-boundary-safe occurrence count - a naive `text.count(keyword)`
    would let short keywords match INSIDE unrelated words (e.g. "java" is a
    literal substring of "javascript", so a frontend candidate whose only
    skill is "JavaScript" would otherwise also score a "Java Developer"
    hit)."""
    total = 0
    for keyword in keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        total += len(re.findall(pattern, text))
    return total


def classify_role(parsed: dict) -> str:
    """STEP 4 - Role Classification. Checks every job title first (most
    senior/most recent titles carry more weight only in that a title match
    at all is a strong signal); falls back to keyword-hit counting over the
    candidate's Skill-Intelligence-ranked skills list if no title matches.
    Falls back to _DEFAULT_ROLE only if nothing matches at all.
    """
    experience = parsed.get("experience") or []
    titles_text = " | ".join(str(exp.get("role") or "") for exp in experience).lower()
    for role, keywords in _ROLE_TITLE_KEYWORDS.items():
        if any(_contains_keyword(titles_text, keyword) for keyword in keywords):
            return role

    skills_text = " ".join(str(s) for s in (parsed.get("skills") or [])).lower()
    best_role, best_count = _DEFAULT_ROLE, 0
    for role, keywords in _ROLE_SKILL_KEYWORDS.items():
        count = _count_keyword_occurrences(skills_text, keywords)
        if count > best_count:
            best_role, best_count = role, count
    # Full Stack Developer is a combination signal, not a single keyword
    # set - a candidate whose skills hit BOTH a frontend and a backend
    # role's keywords roughly equally is better described as full-stack.
    frontend_count = _count_keyword_occurrences(skills_text, _ROLE_SKILL_KEYWORDS["Frontend Developer"])
    backend_count = _count_keyword_occurrences(skills_text, _ROLE_SKILL_KEYWORDS["Backend Developer"])
    if frontend_count >= 2 and backend_count >= 2:
        return "Full Stack Developer"
    return best_role


# --- Layout families + Section templates (STEP 5) ---------------------------

_LAYOUT_FAMILY_BY_SENIORITY: dict[str, str] = {
    "Intern": "fresher", "Fresher": "fresher",
    "Junior Engineer": "junior",
    "Mid-Level Engineer": "mid",
    "Senior Engineer": "senior", "Lead Engineer": "senior", "Staff Engineer": "senior",
    "Principal Engineer": "architect", "Architect": "architect",
    "Solution Architect": "architect", "Enterprise Architect": "architect",
    "Engineering Manager": "manager", "Director": "manager",
    "VP Engineering": "manager", "CTO": "manager",
}

# Each template is the exact (section_key, label) sequence from STEP 5's
# own worked examples. "Contact" is handled by file_generator.py's header
# (name/contact line), not a body section, so it isn't listed here. Tools/
# Achievements aren't named explicitly in every STEP 5 example, but are
# still appended (see module docstring: never silently drop real,
# non-empty candidate content) - placed adjacent to the most similar named
# section so they never contradict the example's own explicit ordering.
_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "fresher": [
        ("summary", "Professional Summary"),
        ("education", "Education"),
        ("skills", "Technical Skills"),
        ("tools", "Tools"),
        ("projects", "Projects"),
        ("internships", "Internships"),
        ("certifications", "Certifications"),
        ("achievements", "Achievements"),
    ],
    "junior": [
        ("summary", "Professional Summary"),
        ("skills", "Technical Skills"),
        ("tools", "Tools"),
        ("experience", "Experience"),
        ("projects", "Projects"),
        ("education", "Education"),
        ("certifications", "Certifications"),
        ("achievements", "Achievements"),
    ],
    "mid": [
        ("summary", "Professional Summary"),
        ("core_skills", "Core Skills"),
        ("tools", "Tools"),
        ("experience", "Professional Experience"),
        ("projects", "Projects"),
        ("certifications", "Certifications"),
        ("education", "Education"),
        ("achievements", "Achievements"),
    ],
    "senior": [
        ("summary", "Executive Summary"),
        ("core_expertise", "Core Expertise"),
        ("experience", "Professional Experience"),
        ("projects", "Major Projects"),
        ("skills", "Technical Skills"),
        ("tools", "Tools"),
        ("certifications", "Certifications"),
        ("education", "Education"),
        ("achievements", "Achievements"),
    ],
    "architect": [
        ("summary", "Executive Summary"),
        ("core_competencies", "Core Competencies"),
        ("enterprise_experience", "Enterprise Experience"),
        ("major_transformation_programs", "Major Transformation Programs"),
        ("architecture_skills", "Architecture Skills"),
        ("tools", "Tools"),
        ("certifications", "Certifications"),
        ("education", "Education"),
        ("achievements", "Achievements"),
    ],
    "manager": [
        ("summary", "Leadership Summary"),
        ("core_competencies", "Core Competencies"),
        ("experience", "Professional Experience"),
        ("leadership_achievements", "Leadership Achievements"),
        ("technology_portfolio", "Technology Portfolio"),
        ("certifications", "Certifications"),
        ("education", "Education"),
    ],
}


def layout_family_for_seniority(seniority: str) -> str:
    return _LAYOUT_FAMILY_BY_SENIORITY.get(seniority, "mid")


# --- STEP 7: Executive Section Generator (derived content, never invented) -

_LOW_PRIORITY_ITEM_CAPS = {"certifications": 5, "education": 3}


def generate_executive_sections(parsed: dict) -> dict[str, list]:
    """STEP 7 - derives content for the synthetic sections a template may
    reference that don't map 1:1 onto a `parsed` field
    ("leadership_achievements", "internships") - every item returned here
    is either a direct copy or a strict SUBSET of a bullet/achievement
    Phase 1-3 already produced, never a new sentence, metric, or claim.
    Also applies STEP 8's "compress lower-priority sections before
    reducing Experience" for candidates whose career is long enough to
    plausibly have accumulated more certifications/education entries than
    fit a scannable resume - Experience/Projects themselves are never
    touched here (Phase 1/3 already own their own bullet-count targets).
    A key is included only when genuinely non-empty (STEP 9: no
    unnecessary/empty sections).
    """
    extra: dict[str, list] = {}

    experience = parsed.get("experience") or []
    internships = [exp for exp in experience if "intern" in str(exp.get("role") or "").lower()]
    if internships:
        extra["internships"] = internships

    achievements = list(parsed.get("achievements") or [])
    leadership_bullets = [
        p for exp in experience for p in (exp.get("points") or [])
        if any(keyword in p.lower() for keyword in _LEADERSHIP_KEYWORDS) and has_impact_signal(p)
    ]
    leadership_achievements = achievements + [b for b in leadership_bullets if b not in achievements]
    if leadership_achievements:
        extra["leadership_achievements"] = leadership_achievements

    years = _years_of_experience(experience)
    if years >= 8:
        for field, cap in _LOW_PRIORITY_ITEM_CAPS.items():
            values = parsed.get(field) or []
            if len(values) > cap:
                extra[field] = values[:cap]

    return extra


# --- STEP 8: Page Optimization -----------------------------------------------

def determine_page_budget(years: float) -> int:
    """Under 5y -> 2 pages, 5-10y -> 3, 10-15y -> 4, 15+y -> 5 - matches
    file_generator.py's active `_max_pages_allowed`/experience_
    intelligence.py's `page_budget_for_years` bracket exactly (this module
    is disabled/unused in the render flow - see module docstring - but its
    own numbers are kept in sync rather than left stale, in case adaptive
    layouts are ever reinstated)."""
    if years < 5:
        return 2
    if years < 10:
        return 3
    if years < 15:
        return 4
    return 5


# --- Orchestration: the Final Structured Resume ------------------------------

class StructurePlan:
    """The Resume Structuring Engine's output - everything file_generator.py
    needs to render an adaptive layout, and nothing else (no content
    changes, no styling decisions)."""

    def __init__(self, seniority: str, role: str, years: float, sections: list[tuple[str, str]],
                 extra_content: dict[str, list], max_pages: int):
        self.seniority = seniority
        self.role = role
        self.years_of_experience = years
        self.sections = sections
        self.extra_content = extra_content
        self.max_pages = max_pages


def build_structure_plan(parsed: dict) -> StructurePlan:
    """Public entry point - runs the full pipeline (see module docstring)
    over an already-fully-optimized `parsed` dict and returns a
    StructurePlan ready to hand to file_generator.build_resume_file's
    `section_plan`/`extra_content`/`max_pages_override` parameters.
    """
    seniority = detect_seniority(parsed)
    role = classify_role(parsed)
    years = _years_of_experience(parsed.get("experience"))
    family = layout_family_for_seniority(seniority)
    sections = list(_TEMPLATES[family])
    extra_content = generate_executive_sections(parsed)
    max_pages = determine_page_budget(years)
    return StructurePlan(seniority, role, years, sections, extra_content, max_pages)
