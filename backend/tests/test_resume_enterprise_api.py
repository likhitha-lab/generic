"""Integration tests for the Enterprise-level endpoints (Phase 9) -
scoring, optimization history, before/after comparison, job matching, and
version rollback - through the real FastAPI app (see conftest.py)."""


def _create_resume(client, auth_headers) -> tuple[int, int]:
    resp = client.post(
        "/api/resumes/generate",
        headers=auth_headers,
        json={
            "name": "Jane Doe", "email": "jane@example.com",
            "summary": ["Experienced backend engineer with a strong delivery record."],
            "skills": ["Python", "AWS"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["latest_version"]["id"]


def test_scoring_endpoint_returns_report(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    resp = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/scoring", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert 0 <= body["score"] <= 100
    assert body["persona"] == "general"
    assert isinstance(body["breakdown"], dict)


def test_scoring_endpoint_accepts_persona(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    resp = client.get(
        f"/api/resumes/{resume_id}/versions/{version_id}/scoring",
        headers=auth_headers, params={"persona": "technical_recruiter"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["persona"] == "technical_recruiter"


def test_scoring_endpoint_rejects_unknown_persona(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    resp = client.get(
        f"/api/resumes/{resume_id}/versions/{version_id}/scoring",
        headers=auth_headers, params={"persona": "not_a_real_persona"},
    )
    assert resp.status_code == 400


def test_optimization_history_endpoint(client, auth_headers):
    resume_id, _version_id = _create_resume(client, auth_headers)
    client.post(f"/api/resumes/{resume_id}/regenerate", headers=auth_headers, json={})

    resp = client.get(f"/api/resumes/{resume_id}/optimization-history", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    history = resp.json()
    assert len(history) == 2
    assert history[0]["version_number"] == 1
    assert history[1]["version_number"] == 2


def test_compare_versions_endpoint(client, auth_headers):
    resume_id, v1_id = _create_resume(client, auth_headers)
    regen = client.post(f"/api/resumes/{resume_id}/regenerate", headers=auth_headers, json={})
    v2_id = regen.json()["latest_version"]["id"]

    resp = client.get(
        f"/api/resumes/{resume_id}/versions/compare",
        headers=auth_headers, params={"before_version_id": v1_id, "after_version_id": v2_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "comparison" in body
    assert "explanations" in body


def test_match_job_endpoint(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    # The manual /generate flow's Gemini call is globally mocked (see
    # conftest.py's fake_gemini fixture) to always return FAKE_GEMINI_
    # RESPONSE regardless of the request body - so the actually-stored
    # skills are that fixture's ["Python", "FastAPI"], not whatever this
    # test's own request payload asked for.
    resp = client.post(
        f"/api/resumes/{resume_id}/versions/{version_id}/match-job",
        headers=auth_headers, json={"job_description": "Looking for Python and FastAPI experience."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["match_score"] == 100
    assert "Python" in body["suggested_skill_order"]


def test_match_job_endpoint_never_mutates_stored_resume(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    before = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/json", headers=auth_headers).json()
    client.post(
        f"/api/resumes/{resume_id}/versions/{version_id}/match-job",
        headers=auth_headers, json={"job_description": "Kubernetes and Terraform required."},
    )
    after = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/json", headers=auth_headers).json()
    assert before["content"]["skills"] == after["content"]["skills"]


def test_rollback_endpoint_creates_new_version_with_old_content(client, auth_headers):
    resume_id, v1_id = _create_resume(client, auth_headers)
    v1_content = client.get(f"/api/resumes/{resume_id}/versions/{v1_id}/json", headers=auth_headers).json()["content"]

    client.post(f"/api/resumes/{resume_id}/regenerate", headers=auth_headers, json={})

    rollback_resp = client.post(f"/api/resumes/{resume_id}/versions/{v1_id}/rollback", headers=auth_headers)
    assert rollback_resp.status_code == 201, rollback_resp.text
    body = rollback_resp.json()
    assert body["version_count"] == 3  # v1, v2 (regenerate), v3 (rollback) - nothing deleted

    v3_id = body["latest_version"]["id"]
    v3_content = client.get(f"/api/resumes/{resume_id}/versions/{v3_id}/json", headers=auth_headers).json()["content"]
    assert v3_content["skills"] == v1_content["skills"]
    assert v3_content["summary"] == v1_content["summary"]


def test_rollback_carries_forward_target_versions_visibility_settings(client, auth_headers):
    resume_id, v1_id = _create_resume(client, auth_headers)
    client.put(
        f"/api/resumes/{resume_id}/versions/{v1_id}/visibility",
        headers=auth_headers,
        json={"show_email": False, "show_phone": True, "show_linkedin": True, "show_address": True, "show_employment_dates": True},
    )

    client.post(f"/api/resumes/{resume_id}/regenerate", headers=auth_headers, json={})
    rollback_resp = client.post(f"/api/resumes/{resume_id}/versions/{v1_id}/rollback", headers=auth_headers)
    assert rollback_resp.status_code == 201, rollback_resp.text

    v3 = rollback_resp.json()["latest_version"]
    assert v3["visibility"]["show_email"] is False


def test_export_report_json_endpoint(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    resp = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/export-report", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "scoring" in body
    assert "generated_at" in body


def test_export_report_text_format(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)
    resp = client.get(
        f"/api/resumes/{resume_id}/versions/{version_id}/export-report",
        headers=auth_headers, params={"format": "text"},
    )
    assert resp.status_code == 200, resp.text
    assert "OVERALL SCORE" in resp.text


def test_export_report_with_comparison(client, auth_headers):
    resume_id, v1_id = _create_resume(client, auth_headers)
    regen = client.post(f"/api/resumes/{resume_id}/regenerate", headers=auth_headers, json={})
    v2_id = regen.json()["latest_version"]["id"]

    resp = client.get(
        f"/api/resumes/{resume_id}/versions/{v2_id}/export-report",
        headers=auth_headers, params={"compare_to_version_id": v1_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "comparison" in body
    assert "explanations" in body


def test_enterprise_endpoints_respect_ownership(client, auth_headers):
    resume_id, version_id = _create_resume(client, auth_headers)

    other_email = "other-enterprise@example.com"
    client.post("/api/auth/register", json={"email": other_email, "password": "Passw0rd!23", "full_name": "Other"})
    other_login = client.post("/api/auth/login", json={"email": other_email, "password": "Passw0rd!23"})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    resp = client.get(f"/api/resumes/{resume_id}/versions/{version_id}/scoring", headers=other_headers)
    assert resp.status_code == 404

    resp = client.get(f"/api/resumes/{resume_id}/optimization-history", headers=other_headers)
    assert resp.status_code == 404

    resp = client.post(f"/api/resumes/{resume_id}/versions/{version_id}/rollback", headers=other_headers)
    assert resp.status_code == 404
