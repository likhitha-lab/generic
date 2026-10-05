"""Shared test fixtures.

Env vars MUST be set before `app.core.config.settings` is first imported
(it's an `@lru_cache`d module-level singleton) - hence this happens at
conftest module level, before any `from app...` import below.

Storage backend is left as the real `local` implementation (LocalStorageService),
just pointed at a throwaway temp directory - this exercises the actual
upload/download/delete/exists/list/signed-url code paths for real, rather
than mocking the interface away. GCSStorageService's own logic (retry,
GCS-specific error handling) is covered separately in test_storage_gcs.py by
mocking the google-cloud-storage SDK itself, since no real bucket exists in CI.

Gemini is mocked everywhere (`fake_gemini` fixture, autouse) - no test here
should ever make a real network call.
"""
import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="resumebuilder_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = f"{_TMP_DIR}/blobs"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-key"
# setdefault, not a forced overwrite: the opt-in `live_gemini`-marked
# regression suite (test_regression_sample_resumes.py) needs a REAL
# GEMINI_API_KEY from the actual environment to hit the real API - a
# forced overwrite here would silently replace a real key with this
# placeholder for every test, including those. Every other test's Gemini
# calls are mocked regardless (see the `fake_gemini` fixture below), so
# this placeholder is still exactly what's used whenever no real key is
# exported (the normal/CI case).
os.environ.setdefault("GEMINI_API_KEY", "unused-in-tests")
os.environ["DEFAULT_ADMIN_EMAIL"] = "admin@example.com"
os.environ["DEFAULT_ADMIN_PASSWORD"] = "TestAdmin123!"
os.environ["AUTH_RATE_LIMIT"] = "1000/minute"  # tests hit auth endpoints far more than real usage
os.environ["RATE_LIMIT"] = "1000/minute"

import io  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402

import app.services.experience_refiner as experience_refiner_module  # noqa: E402
import app.services.extraction_pipeline as extraction_pipeline_module  # noqa: E402
import app.services.resume_optimizer as resume_optimizer_module  # noqa: E402
import app.services.resume_service as resume_service_module  # noqa: E402
import app.services.section_recovery as section_recovery_module  # noqa: E402
import app.services.summary_generator as summary_generator_module  # noqa: E402
from app.main import app  # noqa: E402

# Shape matches the standardized schema (see extraction_pipeline.py) -
# "linkedin", not "links". Used as a blanket stand-in for ALL 5 of the
# per-section Gemini calls the upload/convert pipeline now makes (contact/
# summary/skills-section/experience/projects) - each call site only reads
# the handful of keys relevant to it out of this one dict, so one fixture
# value works for all 5 without needing to inspect which prompt was sent.
FAKE_GEMINI_RESPONSE = {
    "name": "Fake Candidate",
    "email": "fake@example.com",
    "phone": "555-0100",
    "linkedin": "linkedin.com/in/fakecandidate",
    "summary": "Experienced backend engineer.",
    "skills": ["Python", "FastAPI"],
    "education": [],
    "certifications": [],
    "tools": ["Docker"],
    "experience": [
        {
            "company": "Acme Corp",
            "role": "Engineer",
            "duration": "2020-2024",
            "points": ["Did engineering things."],
            "reason_for_leaving": "",
            "notes": "",
            "is_career_break": False,
            "break_detail": "",
        }
    ],
    "projects": [],
}


@pytest.fixture(autouse=True)
def fake_gemini(request, monkeypatch):
    """No test should ever hit the real Gemini API - EXCEPT the opt-in,
    skipped-by-default `live_gemini`-marked regression suite
    (test_regression_sample_resumes.py), which exists specifically to
    validate against real Gemini responses; this fixture no-ops entirely
    for any test carrying that marker; every other test gets `call_gemini`
    mocked as before. `call_gemini` is imported into SIX module namespaces
    that each need patching separately (Python binds the name at import
    time, so patching one doesn't affect the others): resume_service.py
    (manual Resume Generator flow), extraction_pipeline.py (the 6-call
    upload/convert Stage 1 flow), resume_optimizer.py (the Resume
    Intelligence Engine's skills/tools, projects, and achievements calls),
    experience_refiner.py (per-job bullet refinement), summary_generator.py
    (recruiter-quality summary generation) - the latter two are delegated
    to from resume_optimizer.py - and section_recovery.py (the
    detect/retry/infer/deterministic Section Recovery Engine's retry tier,
    run right after Stage 1 extraction)."""
    if request.node.get_closest_marker("live_gemini"):
        return
    monkeypatch.setattr(resume_service_module, "call_gemini", lambda prompt: dict(FAKE_GEMINI_RESPONSE))
    monkeypatch.setattr(extraction_pipeline_module, "call_gemini", lambda prompt: dict(FAKE_GEMINI_RESPONSE))
    monkeypatch.setattr(resume_optimizer_module, "call_gemini", lambda prompt: dict(FAKE_GEMINI_RESPONSE))
    monkeypatch.setattr(experience_refiner_module, "call_gemini", lambda prompt: dict(FAKE_GEMINI_RESPONSE))
    monkeypatch.setattr(summary_generator_module, "call_gemini", lambda prompt: dict(FAKE_GEMINI_RESPONSE))
    monkeypatch.setattr(section_recovery_module, "call_gemini", lambda prompt: dict(FAKE_GEMINI_RESPONSE))


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client):
    """Registers a fresh, uniquely-emailed user per test and returns bearer
    auth headers for it - uniqueness matters because all tests share one
    SQLite file for the whole session (see DATABASE_URL above)."""
    email = f"pytest-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!23"
    register = client.post(
        "/api/auth/register", json={"email": email, "password": password, "full_name": "Pytest User"}
    )
    assert register.status_code == 201, register.text

    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """A real, minimal, parseable PDF - not just bytes starting with %PDF-.
    extract_pdf_text() (pdfplumber) needs actual extractable text, or the
    upload/convert flow legitimately 422s with "no text extractable", same
    as it would for a real corrupt/scanned-image-only PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    doc.build(
        [
            Paragraph("Jane Doe", styles["Title"]),
            Paragraph("Professional Summary", styles["Heading2"]),
            Paragraph("Experienced software engineer with 5 years in backend systems.", styles["Normal"]),
            Paragraph("Professional Experience", styles["Heading2"]),
            Paragraph("Acme Corp - Senior Engineer (2020-2024)", styles["Normal"]),
        ]
    )
    return buffer.getvalue()
