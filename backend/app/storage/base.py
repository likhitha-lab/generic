"""Storage backend interface.

Every router/service talks to this interface, never to a cloud SDK or the
filesystem directly - that's what makes it possible to run the whole app
locally with zero cloud credentials (see local.py) while shipping real
Google Cloud Storage (see gcs.py) or Azure Blob Storage (see azure_blob.py)
integration for production, selected by a single environment variable
(see factory.py).
"""
from abc import ABC, abstractmethod
from datetime import timedelta


class StorageError(RuntimeError):
    """Raised for storage failures that aren't simply "object not found" -
    e.g. the bucket/container itself is missing, the caller's credentials
    don't have permission, or a network/transient error survived retries.
    Deliberately a plain RuntimeError subclass, not a taxonomy of one
    exception per failure mode - callers (routers) only ever need to
    distinguish "not found" (FileNotFoundError, a 404) from "everything
    else" (this, a 502/500); they don't act differently on permission-denied
    vs. network-down."""


class StorageService(ABC):
    @abstractmethod
    def upload_file(self, path: str, data: bytes, content_type: str) -> str:
        """Store `data` at `path` and return the path it was stored under
        (implementations may namespace/prefix this, e.g. with a container name).
        Raises StorageError on any failure other than the object not existing
        (there's nothing to "not find" on an upload)."""

    @abstractmethod
    def download_file(self, path: str) -> bytes:
        """Raise FileNotFoundError if `path` doesn't exist. Raises
        StorageError for any other failure (permission denied, network, etc.)."""

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Must not raise if `path` doesn't exist (delete is idempotent).
        Raises StorageError for any other failure."""

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Never raises FileNotFoundError - that's what this replaces the
        need for. Raises StorageError on failures other than "not found"."""

    @abstractmethod
    def list_files(self, prefix: str) -> list[str]:
        """Return every path under `prefix` (e.g. "uploads/42/"). Empty list
        if the prefix has nothing under it - not an error."""

    @abstractmethod
    def generate_signed_url(self, path: str, expires_in: timedelta) -> str:
        """Return a time-limited URL a client can use to fetch `path`
        directly, without going through the app or ever seeing the bucket
        path/credentials. Raises FileNotFoundError if `path` doesn't exist."""
