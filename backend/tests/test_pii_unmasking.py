"""Unit tests for the final PII Unmasking layer (Phase 3) in
app/services/pii_masker.py - find_unknown_placeholders,
validate_mapping_integrity, unmask_text_verified, unmask_structured_verified.
Restoration itself (unmask_text/unmask_structured) is unchanged and
already covered by tests/test_pii_masker.py - these tests are specifically
about the new detection/reporting layer on top of it."""
from app.services.pii_masker import (
    find_unknown_placeholders,
    unmask_structured_verified,
    unmask_text_verified,
    validate_mapping_integrity,
)

_MAPPING = {"Jane Doe": "<CANDIDATE_NAME_1>", "jane@example.com": "<EMAIL_1>", "Acme Corp": "<COMPANY_1>"}


# --- Requirement 1 & 2: valid restoration, repeated/nested/structured -------

def test_every_valid_placeholder_is_replaced_with_its_original_value():
    result = unmask_text_verified("Contact <CANDIDATE_NAME_1> at <EMAIL_1>.", _MAPPING)
    assert result["text"] == "Contact Jane Doe at jane@example.com."
    assert result["unknown_placeholders"] == []
    assert result["mapping_issues"] == []


def test_no_placeholder_remains_in_final_output_when_all_are_known():
    result = unmask_text_verified("<CANDIDATE_NAME_1> works at <COMPANY_1>.", _MAPPING)
    assert "<" not in result["text"]


def test_repeated_placeholders_restore_correctly():
    text = "<CANDIDATE_NAME_1> led the project. <CANDIDATE_NAME_1> also reviewed it."
    result = unmask_text_verified(text, _MAPPING)
    assert result["text"] == "Jane Doe led the project. Jane Doe also reviewed it."


def test_nested_structures_restore_correctly():
    nested = {
        "person": {"name": "<CANDIDATE_NAME_1>", "contacts": ["<EMAIL_1>", "<EMAIL_1>"]},
        "history": [{"employers": [{"name": "<COMPANY_1>"}]}],
    }
    result = unmask_structured_verified(nested, _MAPPING)
    assert result["structured"]["person"]["name"] == "Jane Doe"
    assert result["structured"]["person"]["contacts"] == ["jane@example.com", "jane@example.com"]
    assert result["structured"]["history"][0]["employers"][0]["name"] == "Acme Corp"
    assert result["unknown_placeholders"] == []


def test_lists_restore_correctly():
    result = unmask_structured_verified(["<CANDIDATE_NAME_1>", "<COMPANY_1>", "plain text"], _MAPPING)
    assert result["structured"] == ["Jane Doe", "Acme Corp", "plain text"]


def test_dictionaries_restore_correctly():
    result = unmask_structured_verified({"a": "<EMAIL_1>", "b": {"c": "<COMPANY_1>"}}, _MAPPING)
    assert result["structured"] == {"a": "jane@example.com", "b": {"c": "Acme Corp"}}


# --- Requirement 3: malformed placeholders - detect, record, leave, warn ---

def test_malformed_placeholder_missing_number_is_left_unchanged():
    result = unmask_text_verified("Contact <CANDIDATE_NAME> now.", _MAPPING)
    assert result["text"] == "Contact <CANDIDATE_NAME> now."
    assert result["unknown_placeholders"] == ["<CANDIDATE_NAME>"]


def test_malformed_placeholder_leading_zero_is_left_unchanged():
    result = unmask_text_verified("Contact <CANDIDATE_NAME_01> now.", _MAPPING)
    assert result["text"] == "Contact <CANDIDATE_NAME_01> now."
    assert result["unknown_placeholders"] == ["<CANDIDATE_NAME_01>"]


def test_malformed_placeholder_missing_underscore_is_left_unchanged():
    result = unmask_text_verified("Contact <CANDIDATE_NAME1> now.", _MAPPING)
    assert result["text"] == "Contact <CANDIDATE_NAME1> now."
    assert result["unknown_placeholders"] == ["<CANDIDATE_NAME1>"]


def test_malformed_placeholder_wrong_case_is_left_unchanged():
    result = unmask_text_verified("Contact <CANDIDATE_name_1> now.", _MAPPING)
    assert result["text"] == "Contact <CANDIDATE_name_1> now."
    assert result["unknown_placeholders"] == ["<CANDIDATE_name_1>"]


def test_never_substitutes_the_wrong_value_for_a_malformed_placeholder():
    # Every one of these LOOKS related to CANDIDATE_NAME_1 but must never
    # be resolved to "Jane Doe" - only an exact match is ever restored.
    for malformed in ("<CANDIDATE_NAME>", "<CANDIDATE_NAME_01>", "<CANDIDATE_NAME1>", "<CANDIDATE_name_1>"):
        result = unmask_text_verified(malformed, _MAPPING)
        assert "Jane Doe" not in result["text"]
        assert result["text"] == malformed


def test_mixed_valid_and_invalid_placeholders_in_one_text():
    text = "<CANDIDATE_NAME_1> (also seen as <CANDIDATE_NAME>) works at <COMPANY_1>."
    result = unmask_text_verified(text, _MAPPING)
    assert result["text"] == "Jane Doe (also seen as <CANDIDATE_NAME>) works at Acme Corp."
    assert result["unknown_placeholders"] == ["<CANDIDATE_NAME>"]


def test_unknown_placeholder_not_shaped_like_any_category_is_ignored_not_flagged():
    # Not every "<...>" is a placeholder attempt - a genuine, unrelated
    # bracketed string (e.g. a template artifact) shaped nothing like
    # PLACEHOLDER_1 shouldn't even be flagged if it doesn't match the broad
    # placeholder shape at all (no trailing digits/underscore requirement,
    # but still must be a single bracketed alnum/underscore word).
    result = unmask_text_verified("See <this is not a placeholder> here.", _MAPPING)
    assert result["unknown_placeholders"] == []  # contains spaces, not placeholder-shaped


def test_find_unknown_placeholders_deduplicates_repeated_unknowns():
    text = "<CANDIDATE_NAME> and <CANDIDATE_NAME> again."
    assert find_unknown_placeholders(text, _MAPPING) == ["<CANDIDATE_NAME>"]


def test_unknown_placeholders_in_nested_structure_are_aggregated_once():
    nested = {"a": "<CANDIDATE_NAME>", "b": ["<CANDIDATE_NAME>", "<COMPANY_1>"]}
    result = unmask_structured_verified(nested, _MAPPING)
    assert result["unknown_placeholders"] == ["<CANDIDATE_NAME>"]
    assert result["structured"]["a"] == "<CANDIDATE_NAME>"  # left unchanged
    assert result["structured"]["b"][1] == "Acme Corp"  # known one still restored


# --- Requirement 4: mapping integrity ---------------------------------------

def test_validate_mapping_integrity_reports_no_issues_for_a_healthy_mapping():
    assert validate_mapping_integrity(_MAPPING) == []


def test_validate_mapping_integrity_detects_corrupted_entry():
    bad_mapping = {"Jane Doe": "not-a-placeholder-at-all"}
    issues = validate_mapping_integrity(bad_mapping)
    assert len(issues) == 1
    assert "Corrupted" in issues[0]


def test_validate_mapping_integrity_detects_duplicate_placeholder():
    bad_mapping = {"Jane Doe": "<CANDIDATE_NAME_1>", "John Smith": "<CANDIDATE_NAME_1>"}
    issues = validate_mapping_integrity(bad_mapping)
    assert len(issues) == 1
    assert "Duplicate" in issues[0]


def test_validate_mapping_integrity_handles_empty_mapping():
    assert validate_mapping_integrity({}) == []


# --- Requirement 5: never restore placeholders not generated by this request

def test_never_restores_a_placeholder_from_a_different_requests_mapping():
    # <COMPANY_1> is a perfectly VALID-shaped placeholder, but it isn't a
    # value in THIS mapping (only CANDIDATE_NAME_1/EMAIL_1 are) - must be
    # left alone, not resolved against some other request's idea of what
    # COMPANY_1 might mean.
    other_request_mapping = {"Jane Doe": "<CANDIDATE_NAME_1>", "jane@example.com": "<EMAIL_1>"}
    result = unmask_text_verified("Works at <COMPANY_1>.", other_request_mapping)
    assert result["text"] == "Works at <COMPANY_1>."
    assert result["unknown_placeholders"] == ["<COMPANY_1>"]
