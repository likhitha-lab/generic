"""Deterministic, keyword-based skill categorization.

Input: a flat, already-normalized skill list (see normalization.py's
normalize_skills() - this module assumes spelling/formatting variants are
already merged, e.g. "ReactJS"/"React.js" already collapsed to one form).

Output: a category -> sorted skill list mapping, e.g.:

    {"Cloud Platforms": ["AWS", "Azure"], "Programming Languages": ["Python"]}

This is a rule-based ALTERNATIVE to resume_optimizer.py's Gemini-driven
skills categorization, not a replacement for it - that module asks an LLM
to derive categories fitting the candidate's actual (possibly non-technical)
domain, which this module cannot do: everything here is keyword lookups
against a fixed, TECHNICAL-skill-only dictionary, with no model call and no
domain adaptation.

Categories cover genuine technical competencies a recruiter scanning a
resume expects in a "Technical Skills" section - Programming Languages,
Frameworks, Libraries, Cloud Platforms, Databases, Messaging Technologies,
DevOps Technologies, Architecture Patterns, Core Engineering Skills, AI/ML
Technologies, Analytics Technologies, Visualization Technologies, Security
Technologies, Infrastructure Technologies - plus an "Other Skills"
catch-all. A skill that matches none of the named categories (a
responsibility, a soft skill, a business/methodology term, or a genuine
skill this fixed keyword dictionary just doesn't happen to name) is bucketed
into "Other Skills" rather than excluded - a candidate's real skill must
never silently disappear just because a keyword list didn't anticipate it.
(This module previously had no catch-all and excluded anything unmatched
entirely - a confirmed defect: an unusual technology or a skill phrased
differently than the keyword lists expect vanished from the resume with no
trace. See categorize_skills()'s docstring.)

Does not touch extraction - this is a pure function over an already-
extracted-and-normalized skill list, called after that list exists.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Categories, in the exact order new output dicts should follow (Python
# dicts preserve insertion order) - matches the priority order categories
# are checked in too, so a skill matching keywords in two different
# categories' lists (rare, but e.g. "TensorFlow" could plausibly read as
# either a framework or a machine-learning tool) resolves to whichever
# category comes first here, keeping every skill in EXACTLY one category.
# This order is also what finalize_technical_skills() flattens by, so it
# doubles as the display order a recruiter sees: languages/frameworks/
# platforms/databases first (the highest-scanned items), more specialized
# categories after.
CATEGORY_ORDER: tuple[str, ...] = (
    "Programming Languages",
    "Frameworks",
    "Libraries",
    "Cloud Platforms",
    "Databases",
    "Messaging Technologies",
    "DevOps Technologies",
    "Architecture Patterns",
    "Core Engineering Skills",
    "AI/ML Technologies",
    "Analytics Technologies",
    "Visualization Technologies",
    "Security Technologies",
    "Infrastructure Technologies",
    "Other Skills",
)

# Reusable mapping dictionaries - one keyword set per category, matched
# case-insensitively. Deliberately not exhaustive (a fixed keyword list
# never is), but covers common, high-frequency technical terms for each
# category; anything that matches nothing is EXCLUDED from Technical
# Skills entirely (see categorize_skill) rather than guessed at or kept in
# a vague catch-all - a resume's Technical Skills section should read as a
# curated, scannable list of real competencies, not a dump of everything
# extraction happened to find.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Programming Languages": (
        "python", "java", "javascript", "typescript", "c++", "c#", "golang", "go",
        "rust", "ruby", "php", "swift", "kotlin", "scala", "perl", "bash",
        "shell scripting", "powershell", "sql", "html5", "css3", "html", "css",
        "objective-c", "dart", "r",
    ),
    "Frameworks": (
        "react.js", "react", "angular", "angularjs", "vue.js", "vue", "django",
        "flask", "spring", "spring boot", "express.js", "express", "next.js",
        "node.js", "nodejs", ".net", "asp.net", "laravel", "ruby on rails",
        "rails", "fastapi", "bootstrap", "jquery", "svelte",
    ),
    "Libraries": (
        "pandas", "numpy", "scipy", "requests", "axios", "redux", "junit",
        "mockito", "lombok", "guava", "lodash", "jest", "mocha", "chai", "rxjs",
    ),
    "Cloud Platforms": (
        "aws", "amazon web services", "azure", "microsoft azure", "gcp",
        "google cloud platform", "google cloud", "ibm cloud", "oracle cloud",
        "alibaba cloud", "digitalocean", "heroku", "cloudflare", "azure portal",
        "aks", "azure kubernetes service", "ec2", "s3", "azure functions",
        "aws lambda", "lambda",
    ),
    "Databases": (
        "mysql", "postgresql", "postgres", "mongodb", "sql server",
        "oracle database", "oracle db", "redis", "cassandra", "dynamodb",
        "sqlite", "mariadb", "elasticsearch", "neo4j", "couchbase", "cosmos db",
        "nosql",
    ),
    "Messaging Technologies": (
        "kafka", "apache kafka", "rabbitmq", "activemq", "amazon sqs", "sqs",
        "amazon sns", "sns", "azure service bus", "service bus", "mqtt",
        "zeromq", "pub/sub", "google pub/sub",
    ),
    "DevOps Technologies": (
        "ci/cd", "jenkins", "github actions", "gitlab ci", "azure devops",
        "ansible", "puppet", "chef", "terraform", "git", "github", "gitlab",
        "bitbucket", "devops", "circleci", "travis ci", "docker", "kubernetes",
        "docker desktop", "containerd", "helm", "openshift", "docker swarm",
        "podman", "k8s",
    ),
    "Architecture Patterns": (
        "microservices", "monolith", "monolithic architecture", "serverless",
        "event-driven architecture", "event driven", "domain-driven design",
        "ddd", "cqrs", "soa", "service-oriented architecture", "mvc", "mvvm",
        "event sourcing", "distributed systems", "high availability",
        "system design", "system architecture", "api gateway pattern",
        "hexagonal architecture", "clean architecture",
    ),
    "Core Engineering Skills": (
        "rest apis", "rest api", "graphql", "grpc", "soap", "oop",
        "object-oriented programming", "data structures", "algorithms",
        "design patterns", "unit testing", "tdd", "test-driven development",
        "integration testing", "code review", "version control", "debugging",
        "performance tuning", "performance optimization", "caching",
        "multithreading", "concurrency", "api design",
    ),
    "AI/ML Technologies": (
        "machine learning", "deep learning", "tensorflow", "pytorch",
        "scikit-learn", "keras", "nlp", "natural language processing",
        "computer vision", "data science", "artificial intelligence",
        "neural networks", "mlops", "llm", "generative ai", "langchain",
        "hugging face", "openai api",
    ),
    "Analytics Technologies": (
        "etl", "data pipeline", "spark", "apache spark", "hadoop", "airflow",
        "apache airflow", "data warehousing", "snowflake", "databricks",
        "bigquery", "redshift", "aws glue", "glue", "data modeling",
    ),
    "Visualization Technologies": (
        "power bi", "tableau", "looker", "qlik", "qlikview", "matplotlib",
        "seaborn", "d3.js", "grafana", "kibana", "data visualization",
    ),
    "Security Technologies": (
        "azure key vault", "key vault", "defender", "mfa", "oauth", "ssl",
        "tls", "iam", "penetration testing", "encryption", "siem", "firewall",
        "cybersecurity", "vulnerability assessment", "zero trust",
    ),
    "Infrastructure Technologies": (
        "vnet", "virtual network", "nsg", "vpn", "expressroute", "dns",
        "load balancer", "traffic manager", "tcp/ip", "http", "https", "cdn",
        "subnetting", "bgp", "vlan", "windows server", "windows", "linux",
        "ubuntu", "centos", "red hat", "redhat", "macos", "unix", "debian",
        "fedora",
    ),
}

# Recruiter guidance: a scannable Technical Skills section lists roughly
# 20-30 items - see finalize_technical_skills().
MAX_TECHNICAL_SKILLS = 30

# A genuine technical skill is a short name (a language, framework,
# platform, pattern) - never a full certification title, a degree/
# qualification line, or a project name. Stage 1 extraction is supposed to
# keep these in their own "certifications"/"education" fields, but
# occasionally one leaks into the raw "skills" list; a leaked string like
# "AWS Certified Solutions Architect - Professional" would otherwise slip
# past the keyword filter below (it genuinely contains "aws") and land in
# Technical Skills as if it were a skill. This guard rejects anything that
# reads like a certification/degree/description BEFORE the keyword check
# ever runs, regardless of what keyword substrings it happens to contain.
_NON_SKILL_INDICATORS = (
    "certified", "certification", "certificate", "bachelor", "master of",
    "b.tech", "m.tech", "b.sc", "m.sc", "university", "college", "diploma",
)


def _looks_like_non_skill(cleaned: str) -> bool:
    lowered = cleaned.lower()
    if any(indicator in lowered for indicator in _NON_SKILL_INDICATORS):
        return True
    # A real skill name is a short phrase (a language/framework/platform),
    # never a full sentence, certification title, or project description -
    # more than 5 words is a strong signal it's one of those, not a skill.
    return len(cleaned.split()) > 5


def _normalize_for_match(text: str) -> str:
    """Lowercase + collapsed whitespace - matching happens against this,
    never against the original casing (categorization shouldn't depend on
    how a skill happened to be capitalized)."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _skill_matches_keyword(skill_lower: str, keyword: str) -> bool:
    """True if `keyword` genuinely applies to `skill_lower` - an exact
    match, or `keyword` appearing in `skill_lower` as a whole word/phrase
    (word-boundary-checked), never a bare substring match. Word-boundary
    checking is what stops "java" from matching inside "javascript" - a
    naive `keyword in skill_lower` check would wrongly categorize
    JavaScript as a Java skill."""
    if skill_lower == keyword:
        return True
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, skill_lower) is not None


def categorize_skill(skill: str) -> str | None:
    """Returns the single technical category name a skill belongs to,
    checking CATEGORY_ORDER in order and returning the first match - or
    None if the skill matches no technical-skill keyword at all (a
    responsibility, soft skill, business/methodology term, or anything too
    vague to be a real technical competency - see module docstring), or if
    it looks like a certification/degree/description rather than a skill
    name at all (see _looks_like_non_skill)."""
    if _looks_like_non_skill(skill):
        return None
    skill_lower = _normalize_for_match(skill)
    for category in CATEGORY_ORDER:
        keywords = CATEGORY_KEYWORDS.get(category, ())
        if any(_skill_matches_keyword(skill_lower, keyword) for keyword in keywords):
            return category
    return None


def categorize_skills(skills: list[str] | None) -> dict[str, list[str]]:
    """Groups a flat, normalized skill list into the fixed technical
    category structure (see module docstring) - keyword-based only, no
    AI/model call. A skill that looks like a certification/degree/
    description that leaked into the raw skills list (see
    _looks_like_non_skill) is still excluded here - that content belongs in
    "certifications"/"education", not Technical Skills. Everything else
    that resolves to no NAMED category is bucketed into "Other Skills"
    rather than excluded - never silently dropped. Every category's items
    are sorted alphabetically; a category with no matching skills is
    omitted from the result entirely rather than returned as an empty list.
    """
    if not skills:
        return {}

    buckets: dict[str, list[str]] = {category: [] for category in CATEGORY_ORDER}
    filtered_non_skill = 0
    for skill in skills:
        cleaned = skill.strip() if skill else ""
        if not cleaned:
            continue
        if _looks_like_non_skill(cleaned):
            filtered_non_skill += 1
            continue
        category = categorize_skill(cleaned) or "Other Skills"
        buckets[category].append(cleaned)

    if filtered_non_skill:
        logger.info(
            "Filtered %d certification-/degree-like string(s) out of the raw skills list "
            "(keyword-based fallback categorization) - those belong in certifications/education, "
            "not Technical Skills.", filtered_non_skill,
        )

    return {
        category: sorted(items, key=str.lower)
        for category in CATEGORY_ORDER
        if (items := buckets[category])
    }


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Removes exact (post-normalization) duplicates, keeping the first
    occurrence's original text and original order - same conservative rule
    used throughout this pipeline (never fuzzy-merges two genuinely
    different items)."""
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = _normalize_for_match(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def finalize_technical_skills(
    categorized: dict[str, list[str]] | None, max_items: int | None = None
) -> list[str]:
    """Flattens a category -> items mapping - from EITHER Gemini's own
    domain-adaptive categorization (resume_optimizer.py's primary path) or
    this module's categorize_skills() fallback - into the single,
    un-subsectioned Technical Skills list a recruiter-style resume actually
    shows. Real resumes list skills as one clean, scannable block, not as
    nested category sub-headings - see file_generator.py's add_skills_section,
    which already renders a flat list (no category sub-headings) whenever
    "skills" is a list rather than a dict, so returning a flat list here is
    what makes the rendered resume show ONE "Technical Skills" section with
    no subsections, with no rendering-code change needed.

    Preserves the mapping's own category order (so Gemini's domain-
    appropriate ordering, or CATEGORY_ORDER's for the fallback, is
    respected) and dedupes exact (post-normalization) repeats across
    categories. `max_items` is None (no cap) by default - a candidate's
    genuine skill must never silently disappear for length reasons; a
    caller may still pass an explicit cap if it wants one. Never pads to
    reach a minimum - fewer than `max_items` genuine skills is fine.
    """
    if not categorized:
        return []
    flat = [item for items in categorized.values() for item in items]
    deduped = _dedupe_preserve_order(flat)
    return deduped if max_items is None else deduped[:max_items]
