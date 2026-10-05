"""Filesystem-backed storage - the default for local development and for
this sandbox, since it requires no cloud storage account or credentials.

Files are written under `backend/local_data/blobs/`, which is git-ignored.
This implements the exact same `StorageService` interface as the real GCS/
Azure backends, so switching to a real backend in production is a one-line
environment variable change (`STORAGE_BACKEND=gcs`), not a code change.
"""
import os
from datetime import timedelta
from pathlib import Path

from app.storage.base import StorageService


class LocalStorageService(StorageService):
    def __init__(self, root_dir: str):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        # Prevent path traversal outside the storage root.
        full = (self.root / path).resolve()
        if self.root.resolve() not in full.parents and full != self.root.resolve():
            raise ValueError(f"Invalid storage path: {path!r}")
        return full

    def upload_file(self, path: str, data: bytes, content_type: str) -> str:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return path

    def download_file(self, path: str) -> bytes:
        full = self._resolve(path)
        if not full.exists():
            raise FileNotFoundError(path)
        return full.read_bytes()

    def delete_file(self, path: str) -> None:
        full = self._resolve(path)
        if full.exists():
            os.remove(full)

    def file_exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def list_files(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        return [
            str(p.relative_to(self.root)).replace(os.sep, "/")
            for p in base.rglob("*")
            if p.is_file()
        ]

    def generate_signed_url(self, path: str, expires_in: timedelta) -> str:
        # There's no real access-control concept for the local filesystem to
        # "sign" a URL against - this exists so local dev/tests can exercise
        # the same interface as production, not as a real security boundary.
        full = self._resolve(path)
        if not full.exists():
            raise FileNotFoundError(path)
        return full.resolve().as_uri()
