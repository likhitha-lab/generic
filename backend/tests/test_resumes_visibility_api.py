"""Integration tests for recruiter-controlled visibility (show/hide email,
phone, LinkedIn, address, employment dates) through the real FastAPI app -
verifies the PUT endpoint, that GET responses expose the current settings,
that downloaded PDF/DOCX bytes actually reflect them, and that
content_json (the source of truth) is never touched."""
import json

import pdfplumber

from app.database.session import SessionLocal
from app.models.resume_version import ResumeVersion
from app.storage.factory import get_storage
from docx import Document
import io


def _get_version_row(resume_id: int) -> ResumeVersion:
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


def _download_text(client, auth_headers, resume_id, version_id, file_format):
    resp = client.get(
        f"/api/resumes/{resume_id}/versions/{version_id}/download",
        headers=auth_headers, params={"format": file_format},
    )
    assert resp.status_code == 200
    if file_format == "pdf":
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    document = Document(io.BytesIO(resp.content))
    # Name/contact now live inside the header's name/logo table cells (see
    # _build_docx_with_tier) - document.paragraphs is body-only and never
    # includes header/footer content, and header.paragraphs alone misses
    # anything inside a header table, so all three have to be read here.
    header = document.sections[0].header
    header_text = "\n".join(p.text for p in header.paragraphs if p.text)
    header_table_text = "\n".join(
        p.text for table in header.tables for row in table.rows for cell in row.cells for p in cell.paragraphs if p.text
    )
    body_text = "\n".join(p.text for p in document.paragraphs if p.text)
    return header_text + "\n" + header_table_text + "\n" + body_text


def _create_resume_with_contact(client, auth_headers):
    resp = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={
            "name": "Jane Doe", "email": "jane@example.com", "phone": "+1-555-0100",
            "location": "Austin, TX", "links": "linkedin.com/in/janedoe",
            "summary": ["Engineer."], "skills": ["Python"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["versions"][0]["id"]


def test_new_version_defaults_to_showing_everything(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    resp = client.get(f"/api/resumes/{resume_id}", headers=auth_headers)
    version = resp.json()["versions"][0]
    assert version["visibility"] == {
        "show_email": True, "show_phone": True, "show_linkedin": True,
        "show_address": True, "show_employment_dates": True,
    }


def test_update_visibility_returns_merged_settings(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    resp = client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["visibility"]["show_email"] is False
    assert resp.json()["visibility"]["show_phone"] is True


def test_hiding_email_removes_it_from_downloaded_pdf_and_docx(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )
    pdf_text = _download_text(client, auth_headers, resume_id, version_id, "pdf")
    docx_text = _download_text(client, auth_headers, resume_id, version_id, "docx")
    assert "jane@example.com" not in pdf_text
    assert "jane@example.com" not in docx_text
    # Everything else still shows.
    assert "+1-555-0100" in pdf_text
    assert "+1-555-0100" in docx_text


def test_hiding_employment_dates_removes_them_from_downloaded_files(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": True, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": False},
    )
    pdf_text = _download_text(client, auth_headers, resume_id, version_id, "pdf")
    docx_text = _download_text(client, auth_headers, resume_id, version_id, "docx")
    assert "2020-2024" not in pdf_text
    assert "2020-2024" not in docx_text
    assert "Acme Corp" in pdf_text
    assert "Acme Corp" in docx_text


def test_visibility_update_never_touches_content_json(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    before = json.loads(_get_version_row(resume_id).content_json)

    client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": False, "show_linkedin": False, "show_address": False, "show_employment_dates": False},
    )

    after = json.loads(_get_version_row(resume_id).content_json)
    # The underlying data is completely unchanged - "hide only during
    # rendering, never remove data from storage".
    assert before == after
    assert after["email"] == "jane@example.com"
    assert after["experience"][0]["duration"] == "2020-2024"


def test_visibility_update_reuses_same_blob_paths_and_version_number(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    before = _get_version_row(resume_id)
    before_pdf_path, before_docx_path, before_version_number = (
        before.pdf_blob_path, before.docx_blob_path, before.version_number,
    )

    client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )

    after = _get_version_row(resume_id)
    # Same version, same paths - visibility changes overwrite the existing
    # rendered files in place rather than creating a new version.
    assert after.version_number == before_version_number
    assert after.pdf_blob_path == before_pdf_path
    assert after.docx_blob_path == before_docx_path


def test_partial_visibility_update_preserves_other_settings(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)
    client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )
    # A second update only changes phone - email must stay hidden, not
    # silently reset to shown.
    resp = client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": False, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )
    assert resp.json()["visibility"]["show_email"] is False
    assert resp.json()["visibility"]["show_phone"] is False


def test_cannot_update_visibility_on_another_users_resume(client, auth_headers):
    resume_id, version_id = _create_resume_with_contact(client, auth_headers)

    other_email = "visibility-other-user@example.com"
    client.post("/api/auth/register", json={"email": other_email, "password": "Passw0rd!23", "full_name": "Other User"})
    other_login = client.post("/api/auth/login", json={"email": other_email, "password": "Passw0rd!23"})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    resp = client.put(
        f"/api/resumes/{resume_id}/versions/{version_id}/visibility",
        headers=other_headers,
        json={"show_email": False, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )
    assert resp.status_code == 404
