"""Unit tests for the deterministic, keyword-based tools-vs-technologies
classifier."""
from app.services.tool_classifier import (
    cap_tools,
    classify_tool_or_technology,
    classify_tools_and_technologies,
    cluster_tools_by_family,
    normalize_tool_name,
    normalize_tools,
)

# Exactly the two example lists from the request.
_SHOULD_BE_TOOLS = [
    "Git", "GitHub", "Azure DevOps", "VS Code", "Visual Studio",
    "Power BI", "JIRA", "Confluence", "Terraform", "Docker Desktop",
]
_SHOULD_BE_TECHNOLOGIES = ["Azure", "AWS", "AKS", "Kubernetes", "Azure Functions"]


def test_example_tools_classify_as_tool():
    for item in _SHOULD_BE_TOOLS:
        assert classify_tool_or_technology(item) == "tool", item


def test_example_technologies_classify_as_technology():
    for item in _SHOULD_BE_TECHNOLOGIES:
        assert classify_tool_or_technology(item) == "technology", item


def test_azure_devops_wins_over_bare_azure_keyword():
    # "Azure DevOps" contains "Azure" (a technology keyword) but must still
    # resolve to "tool" - the whole named product takes priority.
    assert classify_tool_or_technology("Azure DevOps") == "tool"
    assert classify_tool_or_technology("Azure Portal") == "tool"
    assert classify_tool_or_technology("Azure CLI") == "tool"
    assert classify_tool_or_technology("Azure") == "technology"
    assert classify_tool_or_technology("Azure Functions") == "technology"


def test_docker_desktop_is_tool_bare_docker_is_technology():
    assert classify_tool_or_technology("Docker Desktop") == "tool"
    assert classify_tool_or_technology("Docker") == "technology"


def test_unknown_term_defaults_to_technology():
    assert classify_tool_or_technology("Some Made Up Thing") == "technology"


def test_case_and_whitespace_insensitive():
    assert classify_tool_or_technology("  git  ") == "tool"
    assert classify_tool_or_technology("AZURE") == "technology"


def test_classify_tools_and_technologies_splits_mixed_list_with_no_duplication():
    mixed = _SHOULD_BE_TOOLS + _SHOULD_BE_TECHNOLOGIES
    tools, technologies = classify_tools_and_technologies(mixed)

    assert sorted(t.lower() for t in tools) == sorted(t.lower() for t in _SHOULD_BE_TOOLS)
    assert sorted(t.lower() for t in technologies) == sorted(t.lower() for t in _SHOULD_BE_TECHNOLOGIES)
    # every item appears in exactly one of the two output lists
    assert len(tools) + len(technologies) == len(mixed)
    assert not (set(t.lower() for t in tools) & set(t.lower() for t in technologies))


def test_classify_tools_and_technologies_handles_empty_and_none():
    assert classify_tools_and_technologies(None) == ([], [])
    assert classify_tools_and_technologies([]) == ([], [])


def test_classify_tools_and_technologies_drops_blank_entries():
    tools, technologies = classify_tools_and_technologies(["Git", "", "   ", "Azure"])
    assert tools == ["Git"]
    assert technologies == ["Azure"]


def test_newly_whitelisted_tools_classify_as_tool():
    for item in ["Graphviz", "IBM Optim", "SSMS", "Oracle SQL Developer", "Kubernetes Dashboard",
                 "Databricks", "Snowflake"]:
        assert classify_tool_or_technology(item) == "tool", item


# --- cap_tools ---------------------------------------------------------------

def test_cap_tools_preserves_order_and_caps_at_max_items():
    tools = [f"Tool{i}" for i in range(30)]
    result = cap_tools(tools, max_items=15)
    assert result == [f"Tool{i}" for i in range(15)]


def test_cap_tools_has_no_default_cap():
    # A candidate's genuine tool must never silently disappear for length
    # reasons - cap_tools no longer truncates by default.
    tools = [f"Tool{i}" for i in range(30)]
    assert len(cap_tools(tools)) == 30


def test_cap_tools_never_pads_below_max():
    assert cap_tools(["Git"]) == ["Git"]


def test_cap_tools_handles_empty_and_none():
    assert cap_tools(None) == []
    assert cap_tools([]) == []


# --- Tools Cleanup (normalize_tool_name / normalize_tools) -------------------

def test_normalize_tool_name_strips_trailing_version_year():
    assert normalize_tool_name("MS Visual Studio 2008") == "Visual Studio"
    assert normalize_tool_name("Visual Studio 2019") == "Visual Studio"


def test_normalize_tool_name_alias_map():
    assert normalize_tool_name("Microsoft Visual Studio") == "Visual Studio"
    assert normalize_tool_name("MS Excel") == "Excel"
    assert normalize_tool_name("SQL Server Management Studio") == "SSMS"


def test_normalize_tool_name_unrecognized_tool_unchanged():
    assert normalize_tool_name("SomeNicheTool") == "SomeNicheTool"


def test_normalize_tool_name_never_strips_a_meaningful_embedded_number():
    # Only a standalone TRAILING year is stripped - a version number
    # embedded elsewhere in the name must survive untouched.
    assert normalize_tool_name("Docker Desktop") == "Docker Desktop"


def test_normalize_tools_removes_duplicates_created_by_normalization():
    result = normalize_tools(["Visual Studio", "MS Visual Studio 2008", "Git"])
    assert result == ["Visual Studio", "Git"]


def test_normalize_tools_preserves_order_and_distinct_tools():
    result = normalize_tools(["Git", "Jenkins", "Docker Desktop"])
    assert result == ["Git", "Jenkins", "Docker Desktop"]


def test_normalize_tools_handles_empty_and_none():
    assert normalize_tools(None) == []
    assert normalize_tools([]) == []


def test_cap_tools_normalizes_before_capping():
    # A duplicate created by normalization must not waste one of the
    # max_items slots.
    tools = ["Visual Studio", "MS Visual Studio 2008"] + [f"Tool{i}" for i in range(25)]
    result = cap_tools(tools, max_items=3)
    assert result == ["Visual Studio", "Tool0", "Tool1"]


# --- Technology Grouping (this round's Priority 5 fix): cluster_tools_by_family

def test_cluster_tools_by_family_groups_azure_items_adjacent():
    tools = ["GIT", "Azure AD", "Excel", "Azure Functions", "Jenkins", "Azure DevOps"]
    result = cluster_tools_by_family(tools)
    azure_positions = [i for i, t in enumerate(result) if "azure" in t.lower()]
    assert azure_positions == list(range(azure_positions[0], azure_positions[0] + len(azure_positions)))


def test_cluster_tools_by_family_orders_azure_before_aws_before_gcp():
    tools = ["Google Cloud Platform", "AWS", "Azure", "Git"]
    result = cluster_tools_by_family(tools)
    assert result.index("Azure") < result.index("AWS") < result.index("Google Cloud Platform")


def test_cluster_tools_by_family_preserves_relative_order_of_unrecognized_tools():
    tools = ["Git", "Jenkins", "Postman"]
    assert cluster_tools_by_family(tools) == tools


def test_cluster_tools_by_family_handles_empty_list():
    assert cluster_tools_by_family([]) == []


def test_cap_tools_clusters_before_capping():
    tools = ["Azure AD", "Git", "Azure Functions", "Jenkins", "Azure DevOps", "Excel"]
    result = cap_tools(tools, max_items=3)
    assert len(result) == 3
    assert all("azure" in t.lower() for t in result)
