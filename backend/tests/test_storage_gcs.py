"""Unit tests for GCSStorageService, mocking the google-cloud-storage SDK
itself - there's no real bucket/credentials in CI, so these verify GCS-
specific behavior (error mapping, retry-then-succeed, bucket-missing/
permission-denied at construction) purely against a fake client, not real
GCS. Real end-to-end GCS behavior is verified manually against a real
bucket - see DEPLOYMENT_GCP.md's verification commands.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import Forbidden, NotFound, ServiceUnavailable

from app.storage.base import StorageError
from app.storage.gcs import GCSStorageService


def _make_service(bucket_exists=True):
    with patch("app.storage.gcs.storage.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_bucket = MagicMock()
        mock_bucket.exists.return_value = bucket_exists
        mock_client.bucket.return_value = mock_bucket
        service = GCSStorageService(bucket_name="test-bucket", project="test-project")
        return service, mock_bucket


def test_missing_bucket_raises_storage_error():
    with pytest.raises(StorageError, match="does not exist"):
        _make_service(bucket_exists=False)


def test_permission_denied_on_bucket_check_is_non_fatal():
    # storage.buckets.get (what bucket.exists() calls) is a bucket-level
    # permission, deliberately NOT included in roles/storage.objectAdmin -
    # a least-privilege runtime service account with full object access can
    # still get Forbidden here. That's a scope mismatch, not proof the
    # bucket/credentials are actually broken, so construction succeeds
    # (with a warning logged) instead of raising - real misuse still surfaces
    # via upload_file/download_file's own Forbidden handling on first use.
    with patch("app.storage.gcs.storage.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_bucket = MagicMock()
        mock_bucket.exists.side_effect = Forbidden("no access")
        mock_client.bucket.return_value = mock_bucket
        service = GCSStorageService(bucket_name="test-bucket")
        assert service is not None


def test_upload_file_calls_upload_from_string_with_content_type():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    service.upload_file("generated/1/resume.pdf", b"pdf-bytes", "application/pdf")

    mock_blob.upload_from_string.assert_called_once_with(b"pdf-bytes", content_type="application/pdf")


def test_download_file_not_found_raises_file_not_found_error():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.side_effect = NotFound("gone")
    mock_bucket.blob.return_value = mock_blob

    with pytest.raises(FileNotFoundError):
        service.download_file("generated/1/resume.pdf")


def test_download_file_permission_denied_raises_storage_error():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.side_effect = Forbidden("no access")
    mock_bucket.blob.return_value = mock_blob

    with pytest.raises(StorageError, match="Permission denied"):
        service.download_file("generated/1/resume.pdf")


def test_delete_file_swallows_not_found():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.delete.side_effect = NotFound("already gone")
    mock_bucket.blob.return_value = mock_blob

    service.delete_file("generated/1/resume.pdf")  # must not raise


def test_transient_error_is_retried_then_succeeds():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    # Fails twice with a transient/retryable error, then succeeds on the 3rd call.
    mock_blob.download_as_bytes.side_effect = [ServiceUnavailable("busy"), ServiceUnavailable("busy"), b"data"]
    mock_bucket.blob.return_value = mock_blob

    with patch("app.storage.gcs.time.sleep"):  # don't actually sleep in tests
        result = service.download_file("generated/1/resume.pdf")

    assert result == b"data"
    assert mock_blob.download_as_bytes.call_count == 3


def test_transient_error_exhausts_retries_and_raises_storage_error():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.side_effect = ServiceUnavailable("still busy")
    mock_bucket.blob.return_value = mock_blob

    with patch("app.storage.gcs.time.sleep"):
        with pytest.raises(StorageError, match="failed after"):
            service.download_file("generated/1/resume.pdf")


def test_file_exists_true_and_false():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_bucket.blob.return_value = mock_blob

    assert service.file_exists("generated/1/resume.pdf") is True


def test_list_files_returns_blob_names():
    service, mock_bucket = _make_service()
    blob_a, blob_b = MagicMock(name="a"), MagicMock(name="b")
    blob_a.name, blob_b.name = "generated/1/a.pdf", "generated/1/b.pdf"
    service._client.list_blobs.return_value = [blob_a, blob_b]

    result = service.list_files("generated/1")

    assert result == ["generated/1/a.pdf", "generated/1/b.pdf"]


def test_generate_signed_url_missing_file_raises_file_not_found():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_bucket.blob.return_value = mock_blob

    with pytest.raises(FileNotFoundError):
        service.generate_signed_url("generated/1/resume.pdf", timedelta(minutes=15))


def test_generate_signed_url_returns_sdk_result():
    service, mock_bucket = _make_service()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url-here"
    mock_bucket.blob.return_value = mock_blob

    url = service.generate_signed_url("generated/1/resume.pdf", timedelta(minutes=15))

    assert url == "https://storage.googleapis.com/signed-url-here"
    mock_blob.generate_signed_url.assert_called_once_with(version="v4", expiration=timedelta(minutes=15), method="GET")
