"""Integration tests for the resume upload/generate/convert/download/delete
flow, through the real FastAPI app + real LocalStorageService (see
conftest.py) with only Gemini mocked. These are the tests that actually
prove the GCS migration didn't break anything observable at the API level -
StorageService is backend-agnostic by construction, so the same assertions
hold regardless of which backend is configured.
"""
from app.database.session import SessionLocal
from app.models.resume_version import ResumeVersion
from app.storage.factory import get_storage


def _latest_version_blob_paths(resume_id: int) -> ResumeVersion:
    db = SessionLocal()
    try:
        return (
            db.query(ResumeVersion)
            .filter(ResumeVersion.resume_id == resume_id)
            .order_by(ResumeVersion.version_number.desc())
            .first()
        )
    finally:
        db.close()


def test_generate_resume_creates_output_prefixed_blobs(client, auth_headers):
    resp = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={"name": "Jane Doe", "email": "jane@example.com", "summary": ["Engineer."], "skills": ["Python"]},
    )
    assert resp.status_code == 201, resp.text
    resume_id = resp.json()["id"]

    version = _latest_version_blob_paths(resume_id)
    assert version.pdf_blob_path.startswith("Resume_output/")
    assert version.docx_blob_path.startswith("Resume_output/")
    assert version.json_blob_path.startswith("Resume_output/")
    assert version.original_blob_path is None  # manual generation never has an "original" upload

    storage = get_storage()
    assert storage.file_exists(version.pdf_blob_path)
    assert storage.file_exists(version.docx_blob_path)


def test_upload_resume_creates_output_and_uploads_prefixed_blobs(client, auth_headers, sample_pdf_bytes):
    resp = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    resume_id = resp.json()["id"]

    version = _latest_version_blob_paths(resume_id)
    assert version.pdf_blob_path.startswith("Resume_output/")
    assert version.docx_blob_path.startswith("Resume_output/")
    assert version.original_blob_path.startswith("Resume_uploads/")

    storage = get_storage()
    assert storage.file_exists(version.pdf_blob_path)
    assert storage.file_exists(version.original_blob_path)


def test_upload_resume_persists_evaluation_report_alongside_output(client, auth_headers, sample_pdf_bytes, monkeypatch):
    """Evaluation Dashboard (Phase G - see evaluation_dashboard.py): every
    uploaded/converted resume must get an evaluation_report.json blob
    alongside content.json/PDF/DOCX - not tracked in a ResumeVersion
    column (same "blob-storage-only, no migration" pattern already used
    for raw_content.json). Verified by spying on StorageService.
    upload_file's actual calls during the request, rather than listing the
    blob prefix afterward (LocalStorageService.list_files hits an
    unrelated, pre-existing Windows short-path-name resolution quirk in
    some dev environments when the OS temp dir path itself uses an 8.3
    short name - a local.py issue, out of scope here, not a regression in
    this feature)."""
    import json as json_module

    import app.storage.local as local_storage_module

    uploaded: list[tuple[str, bytes]] = []
    original_upload_file = local_storage_module.LocalStorageService.upload_file

    def _spy_upload_file(self, path, data, content_type):
        uploaded.append((path, data))
        return original_upload_file(self, path, data, content_type)

    monkeypatch.setattr(local_storage_module.LocalStorageService, "upload_file", _spy_upload_file)

    resp = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text

    matches = [(path, data) for path, data in uploaded if path.endswith("evaluation_report.json")]
    assert matches, "no evaluation_report.json blob was uploaded"

    report = json_module.loads(matches[0][1])
    expected_keys = {
        "name_extraction", "contact_preservation", "summary_completeness_pct",
        "skills_preservation_pct", "experience_preservation_pct", "projects_preservation_pct",
        "certifications_preservation_pct", "education_preservation_pct", "duplicate_entity_count",
        "missing_section_count", "ats_score", "information_preservation_pct",
        "overall_resume_quality_score", "section_detail",
    }
    assert expected_keys.issubset(report.keys())
    assert report["name_extraction"] == "PASS"
    assert report["contact_preservation"] == "PASS"


def test_upload_rejects_wrong_extension(client, auth_headers):
    resp = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.txt", b"not a resume", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_rejects_content_not_matching_extension(client, auth_headers):
    """A file renamed to .pdf but that isn't actually PDF-formatted must be
    rejected by the magic-byte check, not just the extension check."""
    resp = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.pdf", b"this is not really a pdf", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "doesn't match" in resp.json()["detail"]


def test_upload_rejects_oversized_file(client, auth_headers):
    oversized = b"%PDF-1.4\n" + b"0" * (6 * 1024 * 1024)
    resp = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.pdf", oversized, "application/pdf")},
    )
    assert resp.status_code == 413


def test_download_version_returns_file_bytes(client, auth_headers):
    create = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={"name": "Jane Doe", "summary": ["Engineer."], "skills": ["Python"]},
    )
    resume_id = create.json()["id"]
    version_id = create.json()["versions"][0]["id"]

    resp = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/download", headers=auth_headers, params={"format": "pdf"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_signed_url_endpoint_returns_expiring_url(client, auth_headers):
    create = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={"name": "Jane Doe", "summary": ["Engineer."], "skills": ["Python"]},
    )
    resume_id = create.json()["id"]
    version_id = create.json()["versions"][0]["id"]

    resp = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/signed-url", headers=auth_headers, params={"format": "pdf"})
    assert resp.status_code == 200
    body = resp.json()
    assert "url" in body and body["url"]
    assert "expires_at" in body


def test_delete_resume_removes_underlying_blobs(client, auth_headers, sample_pdf_bytes):
    upload = client.post(
        "/api/resumes/upload",
        headers=auth_headers,
        files={"file": ("resume.pdf", sample_pdf_bytes, "application/pdf")},
    )
    resume_id = upload.json()["id"]
    version = _latest_version_blob_paths(resume_id)
    storage = get_storage()

    # Confirm the blobs genuinely exist before deleting - otherwise this
    # test would trivially "pass" even if delete did nothing at all.
    assert storage.file_exists(version.pdf_blob_path)
    assert storage.file_exists(version.docx_blob_path)
    assert storage.file_exists(version.json_blob_path)
    assert storage.file_exists(version.original_blob_path)

    delete_resp = client.delete(f"/api/resumes/{resume_id}", headers=auth_headers)
    assert delete_resp.status_code == 204

    assert not storage.file_exists(version.pdf_blob_path)
    assert not storage.file_exists(version.docx_blob_path)
    assert not storage.file_exists(version.json_blob_path)
    assert not storage.file_exists(version.original_blob_path)

    # And the resume itself is gone from the caller's point of view.
    get_resp = client.get(f"/api/resumes/{resume_id}", headers=auth_headers)
    assert get_resp.status_code == 404


def test_regenerate_creates_new_version_without_deleting_previous_blobs(client, auth_headers):
    create = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={"name": "Jane Doe", "summary": ["Engineer."], "skills": ["Python"]},
    )
    resume_id = create.json()["id"]
    v1 = _latest_version_blob_paths(resume_id)

    regen = client.post(f"/api/resumes/{resume_id}/regenerate", headers=auth_headers, json={})
    assert regen.status_code == 201, regen.text

    v2 = _latest_version_blob_paths(resume_id)
    assert v2.version_number == v1.version_number + 1
    assert v2.pdf_blob_path != v1.pdf_blob_path  # unique path per version, never overwritten

    storage = get_storage()
    assert storage.file_exists(v1.pdf_blob_path)  # regenerate doesn't touch older versions' blobs
    assert storage.file_exists(v2.pdf_blob_path)


def test_cannot_access_another_users_resume(client, auth_headers):
    create = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={"name": "Jane Doe", "summary": ["Engineer."], "skills": ["Python"]},
    )
    resume_id = create.json()["id"]

    other_email = "other-user@example.com"
    client.post("/api/auth/register", json={"email": other_email, "password": "Passw0rd!23", "full_name": "Other User"})
    other_login = client.post("/api/auth/login", json={"email": other_email, "password": "Passw0rd!23"})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    resp = client.get(f"/api/resumes/{resume_id}", headers=other_headers)
    assert resp.status_code == 404
