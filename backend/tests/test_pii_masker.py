"""Unit tests for app/services/pii_masker.py - the PII Masking Engine
(Phase 2). Consumes pii_detector.py's report shape directly. Not wired
into the pipeline - every test here calls the module's functions
directly, never through resume_service.py or any Gemini call site."""
from app.services.pii_detector import detect_pii_in_structured, detect_pii_in_text
from app.services.pii_masker import (
    build_mapping,
    mask_structured,
    mask_text,
    unmask_structured,
    unmask_text,
)

_RAW_TEXT = """Jane Doe
jane.doe@example.com | +1-415-555-0199
linkedin.com/in/janedoe

Professional Experience
Acme Corp
Note: Client: Globex Corporation
"""


def test_build_mapping_is_deterministic_across_repeated_calls():
    report = detect_pii_in_text(_RAW_TEXT)
    assert build_mapping(report) == build_mapping(report)


def test_build_mapping_assigns_expected_placeholders():
    report = detect_pii_in_text(_RAW_TEXT)
    mapping = build_mapping(report)
    assert mapping["Jane Doe"] == "<CANDIDATE_NAME_1>"
    assert mapping["jane.doe@example.com"] == "<EMAIL_1>"
    assert mapping["+1-415-555-0199"] == "<PHONE_1>"
    assert mapping["linkedin.com/in/janedoe"] == "<LINKEDIN_1>"
    assert mapping["Globex Corporation"] == "<CLIENT_1>"


def test_build_mapping_same_value_always_same_placeholder():
    report = {**detect_pii_in_text(""), "companies": ["Acme Corp"], "clients": ["Acme Corp"]}
    mapping = build_mapping(report)
    # "Acme Corp" appears in BOTH companies and clients - REPORT_KEYS order
    # puts "companies" before "clients", so it keeps the COMPANY placeholder
    # rather than getting a second, different one under "clients".
    assert mapping["Acme Corp"] == "<COMPANY_1>"
    assert len(mapping) == 1


def test_build_mapping_different_values_get_different_placeholders():
    report = {**detect_pii_in_text(""), "companies": ["Acme Corp", "Globex Inc"]}
    mapping = build_mapping(report)
    assert mapping["Acme Corp"] == "<COMPANY_1>"
    assert mapping["Globex Inc"] == "<COMPANY_2>"


def test_mask_text_replaces_every_detected_value():
    report = detect_pii_in_text(_RAW_TEXT)
    mapping = build_mapping(report)
    masked = mask_text(_RAW_TEXT, mapping)
    assert "Jane Doe" not in masked
    assert "jane.doe@example.com" not in masked
    assert "+1-415-555-0199" not in masked
    assert "Globex Corporation" not in masked
    assert "<CANDIDATE_NAME_1>" in masked
    assert "<EMAIL_1>" in masked
    assert "<CLIENT_1>" in masked


def test_mask_text_never_touches_text_outside_detected_entities():
    masked = mask_text(_RAW_TEXT, {"Jane Doe": "<CANDIDATE_NAME_1>"})
    assert "Professional Experience" in masked
    assert "Acme Corp" in masked  # not in this mapping, must survive untouched


def test_mask_text_does_not_replace_substring_inside_a_larger_word():
    # "Corp" must not match inside "Corporation".
    masked = mask_text("Globex Corporation hired Corp Inc.", {"Corp": "<COMPANY_1>"})
    assert "Globex Corporation" in masked
    assert masked.count("<COMPANY_1>") == 1
    assert "<COMPANY_1> Inc." in masked


def test_mask_text_handles_value_starting_with_non_word_character():
    # A leading "+" defeats a naive \b...\b pattern - confirms the
    # (?<!\w)/(?!\w) lookaround approach handles it correctly.
    masked = mask_text("Call +1-415-555-0199 now.", {"+1-415-555-0199": "<PHONE_1>"})
    assert masked == "Call <PHONE_1> now."


def test_mask_text_longer_value_masked_before_shorter_overlapping_one():
    mapping = {"Jane": "<X_1>", "Jane Doe": "<CANDIDATE_NAME_1>"}
    masked = mask_text("Jane Doe presented the report.", mapping)
    assert masked == "<CANDIDATE_NAME_1> presented the report."


def test_mask_text_is_never_partial():
    masked = mask_text("Contact Jane Doe for details.", {"Jane Doe": "<CANDIDATE_NAME_1>"})
    assert "Jane" not in masked
    assert "Doe" not in masked


def test_mask_text_handles_empty_text_and_empty_mapping():
    assert mask_text("", {"Jane Doe": "<CANDIDATE_NAME_1>"}) == ""
    assert mask_text("Jane Doe", {}) == "Jane Doe"


# --- Reversibility ------------------------------------------------------------

def test_mask_then_unmask_is_fully_reversible():
    report = detect_pii_in_text(_RAW_TEXT)
    mapping = build_mapping(report)
    masked = mask_text(_RAW_TEXT, mapping)
    assert unmask_text(masked, mapping) == _RAW_TEXT


def test_unmask_text_handles_empty_and_no_mapping():
    assert unmask_text("", {"Jane Doe": "<CANDIDATE_NAME_1>"}) == ""
    assert unmask_text("<CANDIDATE_NAME_1>", {}) == "<CANDIDATE_NAME_1>"


def test_unmask_handles_ten_plus_placeholders_without_prefix_collision():
    # "<COMPANY_1>" is not a substring of "<COMPANY_10>" - confirms no
    # placeholder-numbering collision as the counter passes 9.
    mapping = {f"Company {i}": f"<COMPANY_{i}>" for i in range(1, 12)}
    text = " ".join(mapping.values())
    assert unmask_text(text, mapping) == " ".join(mapping.keys())


# --- Structured dict masking ---------------------------------------------

_STRUCTURED = {
    "name": "Jane Doe",
    "email": "jane.doe@example.com",
    "experience": [
        {"company": "Acme Corp", "points": ["Led the Acme Corp migration to AWS."],
         "notes": "Client: Globex Corporation"},
    ],
    "projects": [{"title": "Customer Portal Revamp", "client": "Initech"}],
}


def test_mask_structured_masks_dedicated_fields_and_inline_mentions():
    report = detect_pii_in_structured(_STRUCTURED)
    mapping = build_mapping(report)
    masked = mask_structured(_STRUCTURED, mapping)
    assert masked["name"] == "<CANDIDATE_NAME_1>"
    assert masked["experience"][0]["company"] == "<COMPANY_1>"
    # inline mention inside a bullet, not just the dedicated "company" field
    assert "Acme Corp" not in masked["experience"][0]["points"][0]
    assert "<COMPANY_1>" in masked["experience"][0]["points"][0]
    # "Globex Corporation" (from the experience note) is detected before
    # "Initech" (from the project's own client field) - REPORT_KEYS/field
    # iteration order, so Initech correctly gets the SECOND client slot.
    assert masked["experience"][0]["notes"] == "Client: <CLIENT_1>"
    assert masked["projects"][0]["client"] == "<CLIENT_2>"


def test_mask_structured_never_mutates_the_input():
    report = detect_pii_in_structured(_STRUCTURED)
    mapping = build_mapping(report)
    mask_structured(_STRUCTURED, mapping)
    assert _STRUCTURED["name"] == "Jane Doe"  # untouched


def test_mask_then_unmask_structured_is_fully_reversible():
    report = detect_pii_in_structured(_STRUCTURED)
    mapping = build_mapping(report)
    masked = mask_structured(_STRUCTURED, mapping)
    assert unmask_structured(masked, mapping) == _STRUCTURED
