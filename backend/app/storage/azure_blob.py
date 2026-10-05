"""Real Azure Blob Storage backend.

Only imported/instantiated when STORAGE_BACKEND=azure - the azure-storage-blob
SDK is a listed dependency but this module is never touched during local
development, so nothing here needs Azure credentials to run this project
locally (see storage/factory.py).

Auth: uses a connection string (AZURE_STORAGE_CONNECTION_STRING) by default,
which is the simplest option and is what you'd pull from Key Vault in
production (see core/keyvault.py). If you prefer passwordless auth, swap the
`from_connection_string` call for
`BlobServiceClient(account_url=..., credential=DefaultAzureCredential())` -
both are standard, documented Azure SDK patterns.
"""
from datetime import datetime, timedelta, timezone

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, ContentSettings, generate_blob_sas

from app.storage.base import StorageError, StorageService


class AzureBlobStorage(StorageService):
    def __init__(self, connection_string: str, container_name: str):
        self._connection_string = connection_string
        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._container_name = container_name
        try:
            container_client = self._client.get_container_client(container_name)
            if not container_client.exists():
                container_client.create_container()
        except HttpResponseError as exc:
            raise StorageError(f"Could not access/create Azure container {container_name!r}: {exc}") from exc

    def _blob_client(self, path: str):
        return self._client.get_blob_client(container=self._container_name, blob=path)

    def upload_file(self, path: str, data: bytes, content_type: str) -> str:
        try:
            self._blob_client(path).upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        except HttpResponseError as exc:
            raise StorageError(f"Upload of {path!r} failed: {exc}") from exc
        return path

    def download_file(self, path: str) -> bytes:
        try:
            return self._blob_client(path).download_blob().readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(path) from exc
        except HttpResponseError as exc:
            raise StorageError(f"Download of {path!r} failed: {exc}") from exc

    def delete_file(self, path: str) -> None:
        try:
            self._blob_client(path).delete_blob()
        except ResourceNotFoundError:
            pass
        except HttpResponseError as exc:
            raise StorageError(f"Delete of {path!r} failed: {exc}") from exc

    def file_exists(self, path: str) -> bool:
        try:
            return self._blob_client(path).exists()
        except HttpResponseError as exc:
            raise StorageError(f"Exists check of {path!r} failed: {exc}") from exc

    def list_files(self, prefix: str) -> list[str]:
        try:
            container_client = self._client.get_container_client(self._container_name)
            return [b.name for b in container_client.list_blobs(name_starts_with=prefix)]
        except HttpResponseError as exc:
            raise StorageError(f"List of {prefix!r} failed: {exc}") from exc

    def generate_signed_url(self, path: str, expires_in: timedelta) -> str:
        if not self.file_exists(path):
            raise FileNotFoundError(path)
        blob_client = self._blob_client(path)
        try:
            account_key = dict(
                item.split("=", 1) for item in self._connection_string.split(";") if "=" in item
            ).get("AccountKey")
            sas = generate_blob_sas(
                account_name=blob_client.account_name,
                container_name=self._container_name,
                blob_name=path,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + expires_in,
            )
            return f"{blob_client.url}?{sas}"
        except HttpResponseError as exc:
            raise StorageError(f"Could not generate signed URL for {path!r}: {exc}") from exc
