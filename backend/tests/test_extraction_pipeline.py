"""Unit tests for app/services/extraction_pipeline.py - the Stage 1 Resume
Converter extraction pipeline (6 concurrent, focused Gemini calls merged
into one standardized schema). Focuses on the 6th call added for
Achievements/Languages/Publications/Volunteer Experience/Leadership (the
direct fix for Stage 1 previously being told to ignore an Awards/
Achievements section even when the source document had one) and on the
existing degrade-gracefully-on-failure behavior, which the new call must
not weaken for the other 5."""
from app.services.extraction_pipeline import extract_resume
from app.services.gemini_client import GeminiError


def _fake_gemini_by_prompt(prompt: str) -> dict:
    """Routes a fake response by inspecting which single-purpose prompt was
    sent - the same "one call per concern" shape the real pipeline uses, so
    this test exercises the actual merge logic rather than a single
    blanket fixture value."""
    if "candidate's contact details" in prompt:
        return {
            "name": "Jane Doe", "email": "jane@example.com", "phone": "555-0100", "linkedin": "",
            "github": "github.com/janedoe", "portfolio": "janedoe.dev",
            "open_to_relocate": True, "open_to_remote": None,
        }
    if "Professional Summary" in prompt and "Extract/write" in prompt:
        return {"summary": "Experienced engineer."}
    if "Extract ONLY these four sections" in prompt:
        return {"skills": ["Python"], "education": ["B.Tech"], "certifications": ["AWS Certified"], "tools": ["Git"]}
    if "Extract ONLY the Professional Experience section" in prompt:
        return {"experience": [{"company": "Acme", "role": "Engineer", "duration": "2020-2024", "points": ["Did X."],
                                 "reason_for_leaving": "", "notes": "", "is_career_break": False, "break_detail": ""}]}
    if "Extract ONLY the Projects section" in prompt:
        return {"projects": []}
    if "Extract ONLY these five sections" in prompt:
        return {
            "achievements": ["Employee of the Month, March 2023"],
            "languages": ["English (Fluent)"],
            "publications": ["\"Scaling Microservices\", Tech Journal, 2022"],
            "volunteer_experience": ["Weekend coding mentor, Code for Good"],
            "leadership": ["Led a 5-person feature team"],
        }
    raise AssertionError(f"Unexpected prompt routed to fake Gemini: {prompt[:200]!r}")


def test_extract_resume_merges_extras_into_standard_schema(monkeypatch):
    import app.services.extraction_pipeline as extraction_pipeline_module

    monkeypatch.setattr(extraction_pipeline_module, "call_gemini", _fake_gemini_by_prompt)
    result = extract_resume("some raw resume text")

    assert result["name"] == "Jane Doe"
    assert result["skills"] == ["Python"]
    assert result["experience"][0]["company"] == "Acme"
    assert result["achievements"] == ["Employee of the Month, March 2023"]
    assert result["languages"] == ["English (Fluent)"]
    assert result["publications"] == ["\"Scaling Microservices\", Tech Journal, 2022"]
    assert result["volunteer_experience"] == ["Weekend coding mentor, Code for Good"]
    assert result["leadership"] == ["Led a 5-person feature team"]
    assert result["github"] == "github.com/janedoe"
    assert result["portfolio"] == "janedoe.dev"
    assert result["open_to_relocate"] is True
    assert result["open_to_remote"] is None


def test_extract_resume_extras_failure_degrades_gracefully_not_the_whole_resume(monkeypatch):
    import app.services.extraction_pipeline as extraction_pipeline_module

    def flaky(prompt: str) -> dict:
        if "Extract ONLY these five sections" in prompt:
            raise GeminiError("Gemini unavailable")
        return _fake_gemini_by_prompt(prompt)

    monkeypatch.setattr(extraction_pipeline_module, "call_gemini", flaky)
    result = extract_resume("some raw resume text")

    # The rest of the resume must generate normally even though the extras
    # call failed - achievements/languages/publications/volunteer/leadership
    # come back empty, never an aborted/errored whole-resume conversion.
    assert result["name"] == "Jane Doe"
    assert result["skills"] == ["Python"]
    assert result["experience"][0]["company"] == "Acme"
    assert result["achievements"] == []
    assert result["languages"] == []
    assert result["publications"] == []
    assert result["volunteer_experience"] == []
    assert result["leadership"] == []
