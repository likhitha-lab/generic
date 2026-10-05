"""Picks the active storage backend from settings.STORAGE_BACKEND.

`local` (default) needs nothing. `gcs` needs GCS_BUCKET_NAME to be set and
(on Cloud Run) needs no explicit credentials at all - Application Default
Credentials via the service's own service account. `azure` needs
AZURE_STORAGE_CONNECTION_STRING and AZURE_STORAGE_CONTAINER to be set
(typically resolved via Key Vault - see core/keyvault.py). Neither cloud
backend requires network access to be importable, which is why `local` is
the default in this sandboxed/local-dev context.
"""
from functools import lru_cache

from app.core.config import settings
from app.storage.base import StorageService
from app.storage.local import LocalStorageService


@lru_cache
def get_storage() -> StorageService:
    if settings.STORAGE_BACKEND == "gcs":
        from app.storage.gcs import GCSStorageService  # local import: avoid requiring google-cloud-storage

        if not settings.GCS_BUCKET_NAME:
            raise RuntimeError("STORAGE_BACKEND=gcs requires GCS_BUCKET_NAME to be set")
        return GCSStorageService(
            bucket_name=settings.GCS_BUCKET_NAME,
            project=settings.GOOGLE_CLOUD_PROJECT,
            credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS,
        )

    if settings.STORAGE_BACKEND == "azure":
        from app.storage.azure_blob import AzureBlobStorage  # local import: avoid requiring

        if not settings.AZURE_STORAGE_CONNECTION_STRING:
            raise RuntimeError("STORAGE_BACKEND=azure requires AZURE_STORAGE_CONNECTION_STRING to be set")
        return AzureBlobStorage(
            connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
            container_name=settings.AZURE_STORAGE_CONTAINER,
        )

    return LocalStorageService(settings.LOCAL_STORAGE_DIR)
