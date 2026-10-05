"""Integration tests for the PII masking wrapper inside
app/services/gemini_client.call_gemini() (see reference_docs/
PII_PIPELINE_INTEGRATION_REPORT.md).

CRITICAL note on why this file exists at all: the repo-wide `fake_gemini`
autouse fixture (tests/conftest.py) monkeypatches `call_gemini` at EACH
CALLER's own module namespace (resume_service, extraction_pipeline,
resume_optimizer, experience_refiner, summary_generator, section_recovery)
- meaning every other test in this entire suite bypasses gemini_client.
call_gemini's real body completely and never exercises the masking logic
inside it. These tests are the only ones in the suite that call the REAL
`call_gemini` function, mocking only `_send_and_parse` (the one call that
actually reaches the network) - so the masking wrapper itself is genuinely
exercised, not just assumed to work because the rest of the suite is green.
"""
import app.services.gemini_client as gemini_client_module
from app.services.gemini_client import GeminiInvalidResponseError, call_gemini

_REAL_PROMPT = """Extract structured data from this resume:

Jane Doe
jane.doe@example.com | +1-415-555-0199
linkedin.com/in/janedoe

Professional Experience
Acme Corp
Note: Client: Globex Corporation
"""


def test_call_gemini_never_sends_original_pii_to_send_and_parse(monkeypatch):
    captured_prompts = []

    def fake_send_and_parse(prompt):
        captured_prompts.append(prompt)
        return {"summary": "A resume."}

    monkeypatch.setattr(gemini_client_module, "_send_and_parse", fake_send_and_parse)
    call_gemini(_REAL_PROMPT)

    assert len(captured_prompts) == 1
    sent_prompt = captured_prompts[0]
    assert "Jane Doe" not in sent_prompt
    assert "jane.doe@example.com" not in sent_prompt
    assert "+1-415-555-0199" not in sent_prompt
    assert "linkedin.com/in/janedoe" not in sent_prompt
    assert "Globex Corporation" not in sent_prompt
    # The instructional wording itself must survive untouched.
    assert "Extract structured data from this resume:" in sent_prompt
    assert "Professional Experience" in sent_prompt
    assert "Acme Corp" in sent_prompt  # not detected by raw-text mode - see known limitation


def test_call_gemini_masked_prompt_contains_placeholders(monkeypatch):
    captured_prompts = []
    monkeypatch.setattr(
        gemini_client_module, "_send_and_parse",
        lambda prompt: captured_prompts.append(prompt) or {"summary": "ok"},
    )
    call_gemini(_REAL_PROMPT)
    sent_prompt = captured_prompts[0]
    assert "<CANDIDATE_NAME_1>" in sent_prompt
    assert "<EMAIL_1>" in sent_prompt
    assert "<PHONE_1>" in sent_prompt
    assert "<LINKEDIN_1>" in sent_prompt
    assert "<CLIENT_1>" in sent_prompt


def test_call_gemini_unmasks_the_response_before_returning(monkeypatch):
    # Simulates Gemini faithfully echoing the placeholders it was given -
    # the caller must get the REAL values back, never the placeholders.
    def fake_send_and_parse(prompt):
        assert "Jane Doe" not in prompt  # sanity: really was masked
        return {
            "name": "<CANDIDATE_NAME_1>",
            "email": "<EMAIL_1>",
            "experience": [
                {"company": "Acme Corp", "notes": "Client: <CLIENT_1>",
                 "points": ["Led the migration for <CLIENT_1>."]},
            ],
        }

    monkeypatch.setattr(gemini_client_module, "_send_and_parse", fake_send_and_parse)
    result = call_gemini(_REAL_PROMPT)

    assert result["name"] == "Jane Doe"
    assert result["email"] == "jane.doe@example.com"
    assert result["experience"][0]["notes"] == "Client: Globex Corporation"
    assert result["experience"][0]["points"][0] == "Led the migration for Globex Corporation."
    # No placeholder token survives anywhere in the final result.
    assert "<CANDIDATE_NAME_1>" not in str(result)
    assert "<CLIENT_1>" not in str(result)


def test_call_gemini_no_op_when_prompt_has_no_detectable_pii(monkeypatch):
    captured_prompts = []
    monkeypatch.setattr(
        gemini_client_module, "_send_and_parse",
        lambda prompt: captured_prompts.append(prompt) or {"summary": "ok"},
    )
    plain_prompt = "Rewrite this bullet in a professional tone: Improved deployment speed."
    call_gemini(plain_prompt)
    assert captured_prompts[0] == plain_prompt  # completely unchanged, nothing to mask


def test_call_gemini_retry_path_reuses_the_same_masking(monkeypatch):
    # First attempt raises GeminiInvalidResponseError (malformed JSON);
    # the retry must still be masked, using the SAME mapping, and the
    # eventually-returned result must still be correctly unmasked.
    calls = []

    def fake_send_and_parse(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise GeminiInvalidResponseError("malformed JSON")
        assert "Jane Doe" not in prompt
        return {"name": "<CANDIDATE_NAME_1>"}

    monkeypatch.setattr(gemini_client_module, "_send_and_parse", fake_send_and_parse)
    result = call_gemini(_REAL_PROMPT)

    assert len(calls) == 2
    assert "Jane Doe" not in calls[0]
    assert "Jane Doe" not in calls[1]
    assert result["name"] == "Jane Doe"


def test_call_gemini_handles_empty_prompt(monkeypatch):
    monkeypatch.setattr(gemini_client_module, "_send_and_parse", lambda prompt: {"summary": ""})
    result = call_gemini("")
    assert result == {"summary": ""}


# --- Verified unmasking wired in (see PII_UNMASKING_REPORT.md) -------------

def test_call_gemini_still_returns_a_plain_dict_on_the_happy_path(monkeypatch):
    # Swapping to unmask_structured_verified must not change call_gemini's
    # return contract - still a plain dict, same content as before.
    monkeypatch.setattr(
        gemini_client_module, "_send_and_parse",
        lambda prompt: {"name": "<CANDIDATE_NAME_1>", "email": "<EMAIL_1>"},
    )
    result = call_gemini(_REAL_PROMPT)
    assert result == {"name": "Jane Doe", "email": "jane.doe@example.com"}
    assert isinstance(result, dict)
    assert "structured" not in result  # the verified wrapper's own envelope must not leak through


def test_call_gemini_warns_and_leaves_malformed_placeholder_unresolved(monkeypatch, caplog):
    # A malformed placeholder in Gemini's own response must survive
    # unresolved (never guessed at) AND now actually get logged, instead of
    # silently vanishing into the final resume unnoticed.
    monkeypatch.setattr(
        gemini_client_module, "_send_and_parse",
        lambda prompt: {"name": "<CANDIDATE_NAME_1>", "notes": "aka <CANDIDATE_NAME>"},
    )
    with caplog.at_level("WARNING"):
        result = call_gemini(_REAL_PROMPT)
    assert result["name"] == "Jane Doe"
    assert result["notes"] == "aka <CANDIDATE_NAME>"
    assert any("<CANDIDATE_NAME>" in record.message for record in caplog.records)
