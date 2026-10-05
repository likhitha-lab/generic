"""Unit tests for LocalStorageService - exercised for real against a
throwaway temp directory (not mocked), since there's no cloud SDK to fake
here."""
from datetime import timedelta

import pytest

from app.storage.local import LocalStorageService


@pytest.fixture
def storage(tmp_path):
    return LocalStorageService(str(tmp_path))


def test_upload_then_download_roundtrips(storage):
    storage.upload_file("uploads/1/resume.pdf", b"pdf-bytes", "application/pdf")
    assert storage.download_file("uploads/1/resume.pdf") == b"pdf-bytes"


def test_download_missing_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.download_file("uploads/1/does-not-exist.pdf")


def test_file_exists(storage):
    assert storage.file_exists("uploads/1/resume.pdf") is False
    storage.upload_file("uploads/1/resume.pdf", b"data", "application/pdf")
    assert storage.file_exists("uploads/1/resume.pdf") is True


def test_delete_is_idempotent(storage):
    storage.upload_file("uploads/1/resume.pdf", b"data", "application/pdf")
    storage.delete_file("uploads/1/resume.pdf")
    assert storage.file_exists("uploads/1/resume.pdf") is False
    storage.delete_file("uploads/1/resume.pdf")  # must not raise the second time


def test_list_files_returns_everything_under_prefix(storage):
    storage.upload_file("generated/7/a.pdf", b"a", "application/pdf")
    storage.upload_file("generated/7/b.docx", b"b", "application/octet-stream")
    storage.upload_file("generated/9/c.pdf", b"c", "application/pdf")

    files = storage.list_files("generated/7")
    assert sorted(files) == ["generated/7/a.pdf", "generated/7/b.docx"]


def test_list_files_empty_prefix_returns_empty_list(storage):
    assert storage.list_files("generated/999") == []


def test_generate_signed_url_missing_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.generate_signed_url("uploads/1/nope.pdf", timedelta(minutes=15))


def test_generate_signed_url_returns_a_uri(storage):
    storage.upload_file("uploads/1/resume.pdf", b"data", "application/pdf")
    url = storage.generate_signed_url("uploads/1/resume.pdf", timedelta(minutes=15))
    assert url.startswith("file://")


def test_path_traversal_is_rejected(storage):
    with pytest.raises(ValueError):
        storage.upload_file("../../etc/passwd", b"data", "text/plain")
