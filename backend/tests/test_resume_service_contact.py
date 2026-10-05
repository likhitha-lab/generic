"""Unit tests for resume_service.py's Contact Intelligence helpers -
_apply_contact_fallback's new GitHub regex fallback and
_contact_dict_for_render's github/portfolio/open_to_relocate/open_to_remote
passthrough. Both are pure/near-pure functions, tested directly rather than
through the full upload pipeline."""
from app.services.resume_service import _apply_contact_fallback, _contact_dict_for_render


def test_apply_contact_fallback_fills_github_from_raw_text_when_gemini_missed_it():
    structured = {"email": "jane@example.com", "phone": "", "linkedin": "", "github": ""}
    raw_text = "Jane Doe\nEmail: jane@example.com\nGitHub: github.com/janedoe"
    _apply_contact_fallback(structured, raw_text)
    assert structured["github"] == "github.com/janedoe"


def test_apply_contact_fallback_never_overwrites_geminis_github():
    structured = {"github": "github.com/already-correct"}
    _apply_contact_fallback(structured, "GitHub: github.com/some-other-user")
    assert structured["github"] == "github.com/already-correct"


def test_contact_dict_for_render_carries_through_new_fields():
    structured = {
        "email": "jane@example.com", "linkedin": "linkedin.com/in/janedoe",
        "github": "github.com/janedoe", "portfolio": "janedoe.dev",
        "open_to_relocate": True, "open_to_remote": False,
    }
    contact = _contact_dict_for_render(structured)
    assert contact["github"] == "github.com/janedoe"
    assert contact["portfolio"] == "janedoe.dev"
    assert contact["open_to_relocate"] is True
    assert contact["open_to_remote"] is False
    assert contact["links"] == "linkedin.com/in/janedoe"  # unchanged existing behavior


def test_contact_dict_for_render_handles_missing_new_fields():
    # Older Stage 1 output / manual flow without these fields must not crash.
    contact = _contact_dict_for_render({"email": "jane@example.com"})
    assert contact["github"] is None
    assert contact["portfolio"] is None
    assert contact["open_to_relocate"] is None
    assert contact["open_to_remote"] is None
