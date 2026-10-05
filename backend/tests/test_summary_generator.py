"""Unit tests for app/services/summary_generator.py."""
import app.services.summary_generator as summary_generator
from app.services.gemini_client import GeminiInvalidResponseError


def _structured(**overrides) -> dict:
    base = {
        "summary": "Original extracted summary sentence.",
        "experience": [
            {"company": "Acme", "role": "Senior Cloud Engineer", "duration": "2019-2024", "is_career_break": False},
            {"company": "Globex", "role": "Cloud Engineer", "duration": "2016-2019", "is_career_break": False},
        ],
    }
    base.update(overrides)
    return base


def test_generate_summary_uses_gemini_response(monkeypatch):
    monkeypatch.setattr(
        summary_generator, "call_gemini",
        lambda prompt: {"summary": "Cloud engineer with 8 years of experience in Azure and AWS."},
    )
    result = summary_generator.generate_summary(_structured(), {"Cloud Platforms": ["Azure", "AWS"]})
    assert result == "Cloud engineer with 8 years of experience in Azure and AWS."


def test_generate_summary_degrades_to_deterministic_fallback_on_gemini_failure(monkeypatch):
    # Priority 3 fix - on a Gemini failure this no longer just returns
    # Stage 1's original one-line summary verbatim; it synthesizes a
    # richer, still entirely-factual summary from the candidate's own
    # extracted data (see _build_deterministic_summary).
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(summary_generator, "call_gemini", boom)
    result = summary_generator.generate_summary(_structured(), {"Cloud Platforms": ["Azure"]})
    assert result != "Original extracted summary sentence."
    assert "Senior Cloud Engineer" in result
    assert "Acme" in result
    assert "8+ year" in result  # span across both listed jobs: 2016-2024
    assert "Azure" in result


def test_generate_summary_falls_back_to_original_when_deterministic_builder_has_nothing(monkeypatch):
    # True empty-data edge case: no experience, no skills, no
    # certifications at all - the deterministic builder has nothing
    # factual to build even one sentence from, so the ORIGINAL (here also
    # empty) summary is the correct final fallback.
    def boom(prompt):
        raise GeminiInvalidResponseError("simulated malformed JSON")

    monkeypatch.setattr(summary_generator, "call_gemini", boom)
    result = summary_generator.generate_summary(_structured(summary="", experience=[]), {})
    assert result == ""


def test_generate_summary_treats_empty_gemini_response_as_failure(monkeypatch):
    monkeypatch.setattr(summary_generator, "call_gemini", lambda prompt: {"summary": ""})
    result = summary_generator.generate_summary(_structured(), {"Cloud Platforms": ["Azure"]})
    assert result != "Original extracted summary sentence."
    assert "Acme" in result


def test_job_context_lines_excludes_career_breaks():
    experience = [
        {"company": "Acme", "role": "Engineer", "duration": "2020-2024", "is_career_break": False},
        {"company": "Career Break", "role": "", "duration": "2019-2020", "is_career_break": True},
    ]
    lines = summary_generator._job_context_lines(experience)
    assert len(lines) == 1
    assert "Acme" in lines[0]


def test_prompt_receives_original_summary_and_categorized_skills(monkeypatch):
    captured_prompts = []

    def capture(prompt):
        captured_prompts.append(prompt)
        return {"summary": "New synthesized summary."}

    monkeypatch.setattr(summary_generator, "call_gemini", capture)
    summary_generator.generate_summary(
        _structured(summary="Do not copy this exact sentence."),
        {"Cloud Platforms": ["Azure", "AWS"], "Programming Languages": ["Python"]},
    )
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "Do not copy this exact sentence." in prompt
    assert "Azure" in prompt and "AWS" in prompt and "Python" in prompt
    assert "Do NOT copy" in prompt
