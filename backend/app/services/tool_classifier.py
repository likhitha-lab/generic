"""Deterministic, keyword-based Tools vs. Technologies classifier.

Extraction/Gemini frequently mix these two together in a single "tools"
(or "skills") list - "Azure", "AWS", "Kubernetes" are technologies/
platforms, not tools a person opens and runs, while "Git", "Azure DevOps",
"VS Code", "Docker Desktop" genuinely are. This module tells the two apart
so a "Tools" resume section only ever contains actual software/products,
with everything else (platforms, cloud services, protocols, concepts)
moved to where it belongs - Technical Skills.

Rule-based only, like skill_categorizer.py: keyword dictionaries, no LLM
call. A reusable ALTERNATIVE/complement to resume_optimizer.py's Gemini-
driven skills/tools split, not a replacement for it - useful wherever a
fast, free, fully deterministic, offline classification is what's needed
instead (e.g. as a fallback when the Gemini call fails, or as its own
standalone re-triage pass over an already-mixed list).

Does not touch extraction - this is a pure function over an already-
extracted item list.
"""
import re

# Specific, named software/products/utilities a person actually opens and
# runs. Checked FIRST and takes priority over TECHNOLOGY_KEYWORDS, which is
# what correctly separates e.g. "Azure DevOps"/"Azure Portal"/"Azure CLI"
# (genuine tools, despite containing "Azure") from bare "Azure" (a cloud
# platform, not a tool) - see classify_tool_or_technology.
TOOL_KEYWORDS: tuple[str, ...] = (
    "git", "github", "gitlab", "bitbucket",
    "azure devops", "azure portal", "azure cli", "azure log analytics",
    "azure service health",
    "vs code", "visual studio code", "visual studio",
    "power bi", "jira", "confluence", "trello", "asana", "slack",
    "terraform", "docker desktop", "jenkins", "circleci", "travis ci",
    "postman", "servicenow", "bmc remedy",
    "eclipse", "intellij", "intellij idea", "pycharm", "notepad++",
    "sublime text", "vim", "emacs", "xcode", "android studio",
    "figma", "adobe photoshop", "photoshop", "excel", "microsoft excel",
    "tableau", "sap", "salesforce",
    "putty", "winscp", "wireshark", "splunk", "datadog", "new relic",
    "grafana", "prometheus", "kubectl", "helm",
    "unity", "unreal engine",
    "graphviz", "ibm optim", "ssms", "sql server management studio",
    "oracle sql developer", "kubernetes dashboard", "databricks", "snowflake",
)

# Recruiter guidance: a scannable Tools section lists roughly 15-20 actual
# tools - see cap_tools().
MAX_TOOLS = 20

# Platforms, cloud services, protocols, and general technology concepts -
# these belong in Technical Skills, never in a Tools section, even though
# some of them (e.g. "Docker") are also things you "run" in a loose sense.
TECHNOLOGY_KEYWORDS: tuple[str, ...] = (
    "azure", "microsoft azure", "aws", "amazon web services",
    "gcp", "google cloud platform", "google cloud",
    "aks", "azure kubernetes service", "kubernetes", "k8s",
    "azure functions", "aws lambda", "lambda", "ec2", "s3",
    "azure blob storage", "storage account", "azure active directory",
    "azure ad", "active directory", "iam",
    "vnet", "virtual network", "expressroute", "vpn", "dns", "nsg",
    "load balancer", "traffic manager", "rest api", "api gateway",
    "ci/cd", "devops", "networking", "cloud", "docker", "containers",
    "microservices", "serverless", "sql", "nosql",
    "machine learning", "artificial intelligence", "deep learning",
)


def _normalize_for_match(text: str) -> str:
    """Lowercase + collapsed whitespace - matching always happens against
    this, never against the original casing."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _matches_any_keyword(text_lower: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword genuinely applies to `text_lower` - exact match,
    or the keyword appearing as a whole word/phrase (word-boundary
    checked), never a bare substring match. Word-boundary checking is what
    stops "aws" from matching inside some longer unrelated token, the same
    protection skill_categorizer.py uses for "java" vs "javascript"."""
    for keyword in keywords:
        if text_lower == keyword:
            return True
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        if re.search(pattern, text_lower):
            return True
    return False


def classify_tool_or_technology(item: str) -> str:
    """Returns "tool" or "technology" for a single item. TOOL_KEYWORDS is
    checked first and wins if matched - this is what lets "Azure DevOps"
    resolve to "tool" despite containing "Azure" (a technology keyword):
    once something matches as a specific named tool, it's never
    reconsidered against the technology list. Anything matching neither
    list defaults to "technology" - the safer bucket when a term's status
    is genuinely ambiguous, since incorrectly inflating a generic term
    into "Tools" is a worse-looking mistake on a resume than the reverse.
    """
    normalized = _normalize_for_match(item)
    if _matches_any_keyword(normalized, TOOL_KEYWORDS):
        return "tool"
    if _matches_any_keyword(normalized, TECHNOLOGY_KEYWORDS):
        return "technology"
    return "technology"


def classify_tools_and_technologies(items: list[str] | None) -> tuple[list[str], list[str]]:
    """Splits a mixed list into (tools, technologies) - every input item
    appears in EXACTLY one of the two output lists, never both and never
    dropped. Order is preserved within each output list (first-seen order
    from the input), duplicates are not removed here (see
    normalization.py's normalize_list for that - this function only
    separates, it doesn't dedupe).
    """
    if not items:
        return [], []

    tools: list[str] = []
    technologies: list[str] = []
    for item in items:
        cleaned = item.strip() if item else ""
        if not cleaned:
            continue
        if classify_tool_or_technology(cleaned) == "tool":
            tools.append(cleaned)
        else:
            technologies.append(cleaned)
    return tools, technologies


# --- Tools Cleanup (alias/version normalization) -----------------------------
#
# Root cause this addresses: "MS Visual Studio 2008" and "Visual Studio"
# name the SAME tool, but as literal strings they're neither an exact
# match nor caught by classify_tool_or_technology (which only tells tools
# apart from technologies, never normalizes a tool's own spelling) - so
# both would render as two separate, duplicate-looking entries.
_TOOL_ALIAS_MAP: dict[str, str] = {
    "ms visual studio": "Visual Studio", "microsoft visual studio": "Visual Studio",
    "ms excel": "Excel", "microsoft excel": "Excel",
    "ms sql server management studio": "SSMS", "sql server management studio": "SSMS",
    "vs code": "VS Code", "visual studio code": "VS Code",
}

# A specific old version YEAR rarely "adds value" on a resume and mostly
# signals staleness ("Visual Studio 2008" reads worse than plain "Visual
# Studio") - deliberately narrow: only a standalone trailing 4-digit year,
# never a version qualifier embedded elsewhere in a name.
_TRAILING_VERSION_YEAR_RE = re.compile(r"\s+(?:19|20)\d{2}\s*$")


def _strip_trailing_version_year(tool: str) -> str:
    return _TRAILING_VERSION_YEAR_RE.sub("", tool).strip()


def normalize_tool_name(tool: str) -> str:
    """STEP: Tools Cleanup - a single tool name's canonical form: strips a
    trailing bare version year (if any), then looks up the result against
    _TOOL_ALIAS_MAP for a known "MS "/"Microsoft "-prefixed variant.
    Returns the (year-stripped) name unchanged if no alias applies - never
    invents a canonical form for a tool this map doesn't recognize."""
    stripped = _strip_trailing_version_year(tool)
    return _TOOL_ALIAS_MAP.get(_normalize_for_match(stripped), stripped)


def normalize_tools(tools: list[str] | None) -> list[str]:
    """STEP: Tools Cleanup. Normalizes every tool name (alias + trailing-
    version-year stripping - see normalize_tool_name) and removes exact-
    match duplicates that result (e.g. "MS Visual Studio 2008" and
    "Visual Studio" both normalize to "Visual Studio" and collapse to one
    entry) - preserves first-seen order, never invents or drops a
    genuinely distinct tool."""
    if not tools:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for tool in tools:
        cleaned = str(tool).strip() if tool is not None else ""
        if not cleaned:
            continue
        canonical = normalize_tool_name(cleaned)
        key = _normalize_for_match(canonical)
        if key and key not in seen:
            seen.add(key)
            result.append(canonical)
    return result


# --- Technology Grouping (family clustering - this round's Priority 5 fix) -
#
# Root cause: unlike Technical Skills (skill_intelligence.py already
# clusters same-vendor Cloud items adjacent to each other), the Tools list
# had no such treatment - a real resume's raw Tools list mixed ".Net
# core", "API Management", "AKS", "AngularJS", "ASP.NET", "AWS Elastic
# search", "Azure AD", ... in whatever order extraction happened to
# produce them, with Azure/AWS/GCP items scattered rather than grouped.
# Same discipline as skill_intelligence.py's Cloud category: a pure
# REORDERING of the still-flat Tools list (same items, same ATS-readable
# strings) - never a nested category->list structure, which would change
# the shape file_generator.py's rendering (and every Tools-consuming
# check) expects.
_AZURE_FAMILY_KEYWORDS = ("azure",)
_AWS_FAMILY_KEYWORDS = ("aws", "amazon web services")
_GCP_FAMILY_KEYWORDS = ("gcp", "google cloud", "google cloud platform")
_TOOL_FAMILY_RANK = {"azure": 0, "aws": 1, "gcp": 2}


def _tool_family(tool: str) -> str | None:
    lowered = _normalize_for_match(tool)
    if _matches_any_keyword(lowered, _AZURE_FAMILY_KEYWORDS):
        return "azure"
    if _matches_any_keyword(lowered, _AWS_FAMILY_KEYWORDS):
        return "aws"
    if _matches_any_keyword(lowered, _GCP_FAMILY_KEYWORDS):
        return "gcp"
    return None


def cluster_tools_by_family(tools: list[str]) -> list[str]:
    """Clusters same-vendor (Azure/AWS/GCP-family) tools adjacent to each
    other within the still-flat Tools list, rather than leaving them
    scattered in whatever order extraction produced. A tool with no
    recognized family keeps its original relative position among other
    unrecognized tools (stable sort - never reordered relative to each
    other, only relative to the clustered families)."""
    indexed = list(enumerate(tools))
    indexed.sort(key=lambda pair: (_TOOL_FAMILY_RANK.get(_tool_family(pair[1]), 3), pair[0]))
    return [tool for _index, tool in indexed]


def cap_tools(tools: list[str] | None, max_items: int | None = None) -> list[str]:
    """Normalizes (alias + version-year cleanup, dedup - see
    normalize_tools) and clusters same-vendor tools adjacent to each other
    (see cluster_tools_by_family). `max_items` is None (no cap) by default -
    a candidate's genuine tool must never silently disappear for length
    reasons; a caller may still pass an explicit cap (e.g. a short preview)
    if it wants one. Normalizing before capping means a duplicate/alias
    variant never wastes one of the `max_items` slots. Never pads to reach
    a minimum."""
    clustered = cluster_tools_by_family(normalize_tools(tools))
    return clustered if max_items is None else clustered[:max_items]
