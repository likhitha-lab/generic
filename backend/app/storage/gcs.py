"""Real Google Cloud Storage backend.

Only imported/instantiated when STORAGE_BACKEND=gcs - the google-cloud-storage
SDK is a listed dependency but this module is never touched during local
development, so nothing here needs GCP credentials to run this project
locally (see storage/factory.py).

Auth: on Cloud Run, this needs no configuration at all - the client picks up
Application Default Credentials from the service's own service account via
the metadata server automatically. Locally, either run
`gcloud auth application-default login` once, or point
GOOGLE_APPLICATION_CREDENTIALS at a downloaded service-account key file - the
latter is read explicitly below because a key path set only in a local .env
file (parsed by pydantic-settings, not exported to the process environment)
would otherwise be invisible to the SDK's own env-var lookup.

Signed URLs (`generate_signed_url`) on Cloud Run specifically need the
runtime service account to additionally hold `roles/iam.serviceAccountTokenCreator`
on ITSELF - Compute-metadata-based credentials (what Cloud Run uses) have no
private key to sign with locally, so google-auth transparently falls back to
calling the IAM SignBlob API instead, which requires that self-impersonation
grant. See DEPLOYMENT_GCP.md.
"""
import logging
import time
from datetime import timedelta
from typing import Callable, TypeVar

from google.api_core.exceptions import Forbidden, GoogleAPICallError, NotFound, ServiceUnavailable, TooManyRequests
from google.cloud import storage
from google.oauth2 import service_account

from app.storage.base import StorageError, StorageService

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Rate-limiting and brief unavailability are the only failure modes worth
# retrying - a 403 (Forbidden) or 404 (NotFound) will never succeed on retry,
# so those are handled separately at each call site instead of looping on them.
_RETRYABLE_EXCEPTIONS = (ServiceUnavailable, TooManyRequests)
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5


def _with_retry(operation: Callable[[], T], action: str) -> T:
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return operation()
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            logger.warning(
                "GCS %s: transient failure on attempt %d/%d (%s) - %s",
                action, attempt + 1, _MAX_ATTEMPTS, type(exc).__name__,
                "retrying" if attempt < _MAX_ATTEMPTS - 1 else "giving up",
            )
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
    logger.error("GCS %s failed permanently after %d attempts", action, _MAX_ATTEMPTS)
    raise StorageError(f"GCS {action} failed after {_MAX_ATTEMPTS} attempts (transient/network error)") from last_exc


class GCSStorageService(StorageService):
    def __init__(self, bucket_name: str, project: str = "", credentials_path: str = ""):
        if credentials_path:
            credentials = service_account.Credentials.from_service_account_file(credentials_path)
            self._client = storage.Client(project=project or None, credentials=credentials)
        else:
            self._client = storage.Client(project=project or None)
        self._bucket = self._client.bucket(bucket_name)

        # bucket.exists() calls storage.buckets.get, a BUCKET-level permission -
        # deliberately NOT included in roles/storage.objectAdmin (or
        # objectViewer/objectCreator), which are scoped to storage.objects.*
        # only, by design, so a service can be granted object access without
        # being able to read/enumerate the bucket itself. A least-privilege
        # runtime service account (exactly what this project's own
        # scripts/setup-gcp-cicd.sh grants) can therefore have full read/
        # write/delete access to every object in the bucket and still get a
        # Forbidden on this specific check. That's a scope mismatch, not
        # evidence of a real misconfiguration - so it's a warning, not fatal:
        # object uploads/downloads (which HAVE the permissions they need)
        # keep working either way, and if the bucket genuinely doesn't exist
        # or the object-level grant is also missing, upload_file/
        # download_file below already raise a clear StorageError on first
        # real use instead of failing silently.
        try:
            exists = _with_retry(self._bucket.exists, f"bucket check for {bucket_name!r}")
        except Forbidden as exc:
            logger.warning(
                "GCS bucket %r: skipping startup existence check - runtime service account "
                "lacks storage.buckets.get (%s). This is expected under a least-privilege "
                "object-only IAM grant (roles/storage.objectAdmin does not include bucket-level "
                "permissions) and does not by itself mean uploads will fail.",
                bucket_name, exc,
            )
        except GoogleAPICallError as exc:
            logger.error("GCS bucket %r: unreachable on startup check - %s", bucket_name, exc)
            raise StorageError(f"Could not reach GCS to verify bucket {bucket_name!r}: {exc}") from exc
        else:
            if not exists:
                logger.error("GCS bucket %r does not exist", bucket_name)
                raise StorageError(
                    f"GCS bucket {bucket_name!r} does not exist. Check GCS_BUCKET_NAME, or create "
                    f"it - see DEPLOYMENT_GCP.md."
                )
            logger.info("GCS storage backend ready: bucket=%s project=%s", bucket_name, project or "(default)")

    def _blob(self, path: str):
        return self._bucket.blob(path)

    def upload_file(self, path: str, data: bytes, content_type: str) -> str:
        blob = self._blob(path)
        try:
            _with_retry(lambda: blob.upload_from_string(data, content_type=content_type), f"upload of {path!r}")
        except Forbidden as exc:
            logger.error("GCS upload of %r: permission denied - %s", path, exc)
            raise StorageError(f"Permission denied uploading {path!r}") from exc
        except GoogleAPICallError as exc:
            logger.error("GCS upload of %r failed: %s", path, exc)
            raise StorageError(f"Upload of {path!r} failed: {exc}") from exc
        logger.info("GCS upload OK: path=%s bytes=%d content_type=%s", path, len(data), content_type)
        return path

    def download_file(self, path: str) -> bytes:
        blob = self._blob(path)
        try:
            data = _with_retry(blob.download_as_bytes, f"download of {path!r}")
        except NotFound as exc:
            logger.warning("GCS download of %r: object not found", path)
            raise FileNotFoundError(path) from exc
        except Forbidden as exc:
            logger.error("GCS download of %r: permission denied - %s", path, exc)
            raise StorageError(f"Permission denied downloading {path!r}") from exc
        except GoogleAPICallError as exc:
            logger.error("GCS download of %r failed: %s", path, exc)
            raise StorageError(f"Download of {path!r} failed: {exc}") from exc
        logger.info("GCS download OK: path=%s bytes=%d", path, len(data))
        return data

    def delete_file(self, path: str) -> None:
        blob = self._blob(path)
        try:
            _with_retry(blob.delete, f"delete of {path!r}")
        except NotFound:
            logger.info("GCS delete of %r: already absent (idempotent no-op)", path)
            return
        except Forbidden as exc:
            logger.error("GCS delete of %r: permission denied - %s", path, exc)
            raise StorageError(f"Permission denied deleting {path!r}") from exc
        except GoogleAPICallError as exc:
            logger.error("GCS delete of %r failed: %s", path, exc)
            raise StorageError(f"Delete of {path!r} failed: {exc}") from exc
        logger.info("GCS delete OK: path=%s", path)

    def file_exists(self, path: str) -> bool:
        try:
            return _with_retry(self._blob(path).exists, f"exists check of {path!r}")
        except Forbidden as exc:
            logger.error("GCS exists-check of %r: permission denied - %s", path, exc)
            raise StorageError(f"Permission denied checking {path!r}") from exc
        except GoogleAPICallError as exc:
            logger.error("GCS exists-check of %r failed: %s", path, exc)
            raise StorageError(f"Exists check of {path!r} failed: {exc}") from exc

    def list_files(self, prefix: str) -> list[str]:
        try:
            blobs = _with_retry(lambda: list(self._client.list_blobs(self._bucket, prefix=prefix)), f"list of {prefix!r}")
        except Forbidden as exc:
            logger.error("GCS list of %r: permission denied - %s", prefix, exc)
            raise StorageError(f"Permission denied listing {prefix!r}") from exc
        except GoogleAPICallError as exc:
            logger.error("GCS list of %r failed: %s", prefix, exc)
            raise StorageError(f"List of {prefix!r} failed: {exc}") from exc
        return [blob.name for blob in blobs]

    def generate_signed_url(self, path: str, expires_in: timedelta) -> str:
        if not self.file_exists(path):
            logger.warning("GCS signed-url request for %r: object not found", path)
            raise FileNotFoundError(path)
        try:
            url = self._blob(path).generate_signed_url(version="v4", expiration=expires_in, method="GET")
        except GoogleAPICallError as exc:
            logger.error(
                "GCS signed-url generation for %r failed: %s (on Cloud Run, check the runtime "
                "service account has roles/iam.serviceAccountTokenCreator on itself)", path, exc,
            )
            raise StorageError(f"Could not generate signed URL for {path!r}: {exc}") from exc
        logger.info("GCS signed URL generated: path=%s expires_in=%s", path, expires_in)
        return url
