"""Optional Azure Key Vault secret loading.

Called once at startup, from main.py, before anything else reads `settings`.
If AZURE_KEY_VAULT_URL isn't set (the default for local dev and for this
sandbox), this is a no-op - no Azure SDK call is ever made and no Azure
credentials are required to run the app.

In Azure, this uses DefaultAzureCredential, which transparently picks up the
App Service's managed identity - no client secret is ever stored anywhere.
Locally against a real vault (optional, for testing this integration before
deployment) it falls back to your `az login` session.

Secret naming convention in the vault (hyphens, since Key Vault secret names
can't contain underscores):
  jwt-secret-key                     -> JWT_SECRET_KEY
  gemini-api-key                     -> GEMINI_API_KEY
  database-url                       -> DATABASE_URL
  azure-storage-connection-string    -> AZURE_STORAGE_CONNECTION_STRING
  default-admin-password             -> DEFAULT_ADMIN_PASSWORD
"""
import logging

from app.core.config import Settings

logger = logging.getLogger(__name__)

_SECRET_NAME_TO_FIELD = {
    "jwt-secret-key": "JWT_SECRET_KEY",
    "gemini-api-key": "GEMINI_API_KEY",
    "database-url": "DATABASE_URL",
    "azure-storage-connection-string": "AZURE_STORAGE_CONNECTION_STRING",
    "default-admin-password": "DEFAULT_ADMIN_PASSWORD",
}


def load_secrets_from_keyvault(settings: Settings) -> None:
    if not settings.AZURE_KEY_VAULT_URL:
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        logger.warning(
            "AZURE_KEY_VAULT_URL is set but azure-identity/azure-keyvault-secrets "
            "aren't installed; skipping Key Vault secret loading."
        )
        return

    client = SecretClient(vault_url=settings.AZURE_KEY_VAULT_URL, credential=DefaultAzureCredential())

    for secret_name, field_name in _SECRET_NAME_TO_FIELD.items():
        try:
            secret = client.get_secret(secret_name)
        except Exception:  # noqa: BLE001 - a missing/inaccessible secret shouldn't crash startup
            logger.warning("Could not load Key Vault secret '%s'; keeping env/.env value.", secret_name)
            continue
        setattr(settings, field_name, secret.value)
        logger.info("Loaded '%s' from Key Vault.", secret_name)
