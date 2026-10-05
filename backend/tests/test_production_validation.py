"""Final production validation - live Gemini, driven through the ACTUAL
FastAPI API (real router, real auth, real DB session, real local storage -
via TestClient, the same infra tests/test_resumes_api.py already uses; the
only thing NOT "real" compared to a deployed instance is that requests are
in-process rather than over a real HTTP socket, which does not change
which application code runs).

Opt-in only (`@pytest.mark.live_gemini`, excluded by default - see
pytest.ini). Requires a REAL `GEMINI_API_KEY` exported in the shell before
running (conftest.py's `fake_gemini` fixture no-ops entirely for any test
carrying this marker - see that fixture's own docstring).

Run:
    GEMINI_API_KEY=<your real key> pytest -m live_gemini tests/test_production_validation.py -v -s

For every real sample resume in reference_docs/ and reference_docs/
regression_samples/, this:
  1. Uploads the resume through POST /api/resumes/upload (the actual API).
  2. Downloads the generated DOCX and PDF through the actual download
     endpoint.
  3. Reads the generated structured content through the actual
     GET .../json endpoint.
  4. Captures the persisted Evaluation Dashboard report (evaluation_report.
     json - a storage-only blob, no API endpoint, no DB column - see
     evaluation_dashboard.py/resume_service._persist_version) by spying on
     the storage layer's own upload_file call, the same technique already
     proven in test_resumes_api.py.
  5. Verifies every item on the production validation checklist and prints
     a full PASS/FAIL report for that resume - pytest's own per-test
     pass/fail IS the "final validation report" requested; run with `-v -s`
     and share the full output.
"""
import glob
import io
import json as json_module
import os

import pytest
from docx import Document

import app.storage.local as local_storage_module

_TESTS_DIR = os.path.dirname(__file__)
_REFERENCE_DOCS_DIR = os.path.join(_TESTS_DIR, "..", "reference_docs")
_REGRESSION_SAMPLES_DIR = os.path.join(_REFERENCE_DOCS_DIR, "regression_samples")

# Sections a preservation-percentage check applies to, and the minimum
# acceptable percentage - 95% (not 100%) to allow for the occasional
# genuine Gemini near-duplicate-wording edge case without treating every
# imperfection as a hard production failure; anything below this is a real
# finding worth investigating, not noise.
_PRESERVATION_CHECKS = (
    ("skills_preservation_pct", "Skills preservation"),
    ("experience_preservation_pct", "Experience preservation"),
    ("projects_preservation_pct", "Projects preservation"),
    ("education_preservation_pct", "Education preservation"),
    ("certifications_preservation_pct", "Certifications preservation"),
)
_PRESERVATION_THRESHOLD = 95.0


def _discover_sample_files() -> list[str]:
    patterns = [
        os.path.join(_REFERENCE_DOCS_DIR, "*.docx"),
        os.path.join(_REFERENCE_DOCS_DIR, "*.pdf"),
        os.path.join(_REGRESSION_SAMPLES_DIR, "**", "*.docx"),
        os.path.join(_REGRESSION_SAMPLES_DIR, "**", "*.pdf"),
    ]
    files: set[str] = set()
    for pattern in patterns:
        files.update(glob.glob(pattern, recursive=True))
    return sorted(files)


_SAMPLE_FILES = _discover_sample_files()


@pytest.mark.skipif(not _SAMPLE_FILES, reason="No sample resumes found to validate against.")
@pytest.mark.parametrize("file_path", _SAMPLE_FILES, ids=[os.path.basename(f) for f in _SAMPLE_FILES])
@pytest.mark.live_gemini
def test_production_validation_end_to_end(file_path, client, auth_headers, monkeypatch):
    filename = os.path.basename(file_path)
    suffix = ".pdf" if file_path.lower().endswith(".pdf") else ".docx"
    content_type = "application/pdf" if suffix == ".pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    # Spy on the storage layer to capture the evaluation_report.json blob -
    # it has no API endpoint or DB column by design (see
    # resume_service._persist_version's own comment on this).
    uploaded_blobs: list[tuple[str, bytes]] = []
    original_upload_file = local_storage_module.LocalStorageService.upload_file

    def _spy_upload_file(self, path, data, ct):
        uploaded_blobs.append((path, data))
        return original_upload_file(self, path, data, ct)

    monkeypatch.setattr(local_storage_module.LocalStorageService, "upload_file", _spy_upload_file)

    # --- Step 1: upload through the actual API ---
    resp = client.post(
        "/api/resumes/upload", headers=auth_headers,
        files={"file": (filename, file_bytes, content_type)},
    )
    assert resp.status_code == 201, f"{filename}: upload failed - {resp.text}"
    body = resp.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    # --- Step 2: generate/download the final DOCX and PDF through the actual API ---
    docx_resp = client.get(
        f"/api/resumes/{resume_id}/versions/{version_id}/download", headers=auth_headers,
        params={"format": "docx"},
    )
    pdf_resp = client.get(
        f"/api/resumes/{resume_id}/versions/{version_id}/download", headers=auth_headers,
        params={"format": "pdf"},
    )
    assert docx_resp.status_code == 200, f"{filename}: DOCX download failed - {docx_resp.text}"
    assert pdf_resp.status_code == 200, f"{filename}: PDF download failed - {pdf_resp.text}"
    docx_bytes, pdf_bytes = docx_resp.content, pdf_resp.content
    assert docx_bytes, f"{filename}: generated DOCX is empty"
    assert pdf_bytes, f"{filename}: generated PDF is empty"

    # --- Step 3: generated structured content, through the actual API ---
    json_resp = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/json", headers=auth_headers)
    assert json_resp.status_code == 200, f"{filename}: content JSON fetch failed - {json_resp.text}"
    generated = json_resp.json()["content"]

    # --- Step 4: the persisted Evaluation Dashboard report ---
    eval_matches = [data for path, data in uploaded_blobs if path.endswith("evaluation_report.json")]
    assert eval_matches, f"{filename}: no evaluation_report.json blob was persisted"
    dashboard = json_module.loads(eval_matches[-1])

    # --- Step 5: verify every item on the checklist ---
    failures: list[str] = []

    if dashboard["name_extraction"] != "PASS":
        failures.append("Name extraction: FAIL")
    if dashboard["contact_preservation"] != "PASS":
        failures.append("Contact preservation: FAIL")
    for field, label in _PRESERVATION_CHECKS:
        pct = dashboard[field]
        if pct < _PRESERVATION_THRESHOLD:
            failures.append(f"{label}: FAIL ({pct}% < {_PRESERVATION_THRESHOLD}% threshold)")
    if dashboard["duplicate_entity_count"] > 0:
        failures.append(f"Duplicate entities: FAIL ({dashboard['duplicate_entity_count']} remaining)")
    if dashboard["missing_section_count"] > 0:
        failures.append(f"Missing sections: FAIL ({dashboard['missing_section_count']} section(s))")

    # Formatting: DOCX must actually parse and contain the candidate's name.
    formatting_ok = True
    try:
        doc = Document(io.BytesIO(docx_bytes))
        docx_text = "\n".join(p.text for p in doc.paragraphs)
        name = generated.get("name") or ""
        if not name or name not in docx_text:
            formatting_ok = False
            failures.append(f"Formatting: FAIL (candidate name {name!r} not found in generated DOCX)")
    except Exception as exc:  # noqa: BLE001 - a parse failure IS the finding
        formatting_ok = False
        failures.append(f"Formatting: FAIL (generated DOCX did not parse: {exc})")

    # --- Final per-resume report ---
    print(f"\n{'=' * 90}\nPRODUCTION VALIDATION: {filename}\n{'=' * 90}")
    print(f"  Name extraction:          {dashboard['name_extraction']}")
    print(f"  Contact preservation:     {dashboard['contact_preservation']}")
    for field, label in _PRESERVATION_CHECKS:
        print(f"  {label + ':':<26}{dashboard[field]}%")
    print(f"  Duplicate entity count:   {dashboard['duplicate_entity_count']}")
    print(f"  Missing section count:    {dashboard['missing_section_count']}")
    print(f"  ATS score:                {dashboard['ats_score']}")
    print(f"  Information preservation: {dashboard['information_preservation_pct']}%")
    print(f"  Overall quality score:    {dashboard['overall_resume_quality_score']}")
    print(f"  Formatting:               {'PASS' if formatting_ok else 'FAIL'}")
    print(f"  OVERALL: {'PASS' if not failures else 'FAIL'}")
    if failures:
        print("  Failures:")
        for failure in failures:
            print(f"    - {failure}")
    print("=" * 90)

    assert not failures, f"{filename} FAILED production validation:\n" + "\n".join(failures)
