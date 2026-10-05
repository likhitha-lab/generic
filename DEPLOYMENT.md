# Deployment Guide

**Nothing in this project has been deployed.** This document is the runbook for when real Azure credentials and a subscription are available. Everything up through "validate locally" can and should be done first, with zero Azure access.

## Part 1 — Run locally (no Azure required)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set GEMINI_API_KEY at minimum. Everything else has a working
# local default (SQLite, local filesystem storage, a dev-only JWT secret).
uvicorn app.main:app --reload --port 8000
```

First startup creates `backend/local_data/app.db` (SQLite) and `backend/local_data/blobs/` (file storage), seeds the `admin`/`user` roles, and seeds one admin account from `DEFAULT_ADMIN_EMAIL`/`DEFAULT_ADMIN_PASSWORD` (defaults: `admin@example.com` / `ChangeMe123!` — change these in `.env` before doing anything real). API docs: `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
# edit .env: VITE_API_URL must point at the backend above, e.g. http://127.0.0.1:8000
npm run dev
```

App: `http://localhost:5173`. Register a new account or log in with the seeded admin.

### Or both together with Docker Compose

```bash
cp backend/.env.example backend/.env   # edit as above
docker compose up --build
```

## Part 2 — One-time Azure setup

Everything below assumes the Azure CLI (`az`) is installed and you're logged in (`az login`) with a subscription that can create the resources in `infra/main.bicep`.

```bash
export AZ_SUBSCRIPTION_ID="<your-subscription-id>"
export AZ_RESOURCE_GROUP="rg-resumemgr-dev"
export AZ_LOCATION="eastus"

az account set --subscription "$AZ_SUBSCRIPTION_ID"
az group create --name "$AZ_RESOURCE_GROUP" --location "$AZ_LOCATION"
```

### Generate the secrets the deployment needs

```bash
export SQL_ADMIN_PASSWORD=$(openssl rand -base64 24)
export JWT_SECRET_KEY=$(openssl rand -hex 32)
export DEFAULT_ADMIN_PASSWORD=$(openssl rand -base64 18)
export GEMINI_API_KEY="<your Gemini API key from https://aistudio.google.com/apikey>"

echo "Save these somewhere safe (e.g. a password manager) - they are not printed again:"
echo "SQL_ADMIN_PASSWORD=$SQL_ADMIN_PASSWORD"
echo "DEFAULT_ADMIN_PASSWORD=$DEFAULT_ADMIN_PASSWORD"
```

### Validate the Bicep template

```bash
cd infra
az bicep build --file main.bicep
az deployment group validate \
  --resource-group "$AZ_RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters main.parameters.json \
  --parameters sqlAdministratorPassword="$SQL_ADMIN_PASSWORD" \
  --parameters geminiApiKey="$GEMINI_API_KEY" \
  --parameters jwtSecretKey="$JWT_SECRET_KEY" \
  --parameters defaultAdminPassword="$DEFAULT_ADMIN_PASSWORD"
```

### Deploy the infrastructure

```bash
az deployment group create \
  --resource-group "$AZ_RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters main.parameters.json \
  --parameters sqlAdministratorPassword="$SQL_ADMIN_PASSWORD" \
  --parameters geminiApiKey="$GEMINI_API_KEY" \
  --parameters jwtSecretKey="$JWT_SECRET_KEY" \
  --parameters defaultAdminPassword="$DEFAULT_ADMIN_PASSWORD" \
  --name resumemgr-deploy
```

This creates: App Service + Plan, Static Web App, Storage Account + `resumes` container, Azure SQL Server + Database, Key Vault (pre-populated with all five secrets), Application Insights + Log Analytics, and grants the App Service's managed identity the "Key Vault Secrets User" role on the vault. Capture the outputs:

```bash
az deployment group show --resource-group "$AZ_RESOURCE_GROUP" --name resumemgr-deploy --query properties.outputs
```

You'll need `backendUrl`, `frontendUrl`, `appServiceName`, and `staticWebAppName` for the steps below.

### Install the ODBC driver dependency (App Service only)

Azure App Service's built-in Python runtime already includes `msodbcsql18`, so no action is needed there. If you ever run the backend in a *custom* container instead of the built-in runtime, install `msodbcsql18` in that image (see `pyodbc`'s documentation) — the provided `backend/Dockerfile` is intended for local `docker compose` use only, not for the App Service deployment path described here.

## Part 3 — CI/CD via GitHub Actions

Two workflows already exist in `.github/workflows/`: `backend-cicd.yml` and `frontend-cicd.yml`. Both run their test/build jobs on every push with no Azure access required; the `deploy` jobs additionally need these repository secrets configured (Settings → Secrets and variables → Actions):

| Secret | Used by | How to get it |
|---|---|---|
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | backend deploy (OIDC login) | Create an Azure AD app registration federated with this GitHub repo — see below |
| `AZURE_APP_SERVICE_NAME` | backend deploy | The `appServiceName` output from Part 2 |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | frontend deploy | `az staticwebapp secrets list --name <staticWebAppName> --query properties.apiKey -o tsv` |
| `VITE_API_URL` | frontend build | The `backendUrl` output from Part 2 |

### Set up OIDC login (no client secret stored in GitHub)

```bash
az ad app create --display-name "resumemgr-github-deploy"
export APP_ID=$(az ad app list --display-name "resumemgr-github-deploy" --query "[0].appId" -o tsv)
az ad sp create --id "$APP_ID"

az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "resumemgr-main-branch",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<your-org>/<your-repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

az role assignment create --assignee "$APP_ID" --role "Contributor" \
  --scope "/subscriptions/$AZ_SUBSCRIPTION_ID/resourceGroups/$AZ_RESOURCE_GROUP"
```

Set `AZURE_CLIENT_ID` to `$APP_ID`, `AZURE_TENANT_ID` to `$(az account show --query tenantId -o tsv)`, `AZURE_SUBSCRIPTION_ID` to `$AZ_SUBSCRIPTION_ID`.

Once all secrets are set, pushing to `main` runs tests/build, then deploys the backend to App Service and the frontend to Static Web Apps automatically.

## Part 4 — Post-deploy checklist

- [ ] Log in as the seeded admin at `<frontendUrl>/login`, immediately change the password (no self-service password-change endpoint exists yet — update `DEFAULT_ADMIN_PASSWORD` and redeploy, or update the `hashed_password` column directly, until one is added).
- [ ] Confirm `<backendUrl>/health` returns `{"status": "ok"}`.
- [ ] Confirm CORS: `ALLOWED_ORIGINS` on the App Service should equal the real Static Web App URL (the Bicep template sets this automatically from the `staticWebApp` module output).
- [ ] Confirm the App Service's managed identity can read Key Vault: check `Log stream` in the Azure Portal for the app for any `keyvault` errors on startup.
- [ ] Rotate `SQL_ADMIN_PASSWORD`, `JWT_SECRET_KEY`, and `DEFAULT_ADMIN_PASSWORD` if they were ever typed into a shell history or CI log in plaintext during setup.

## Future work (documented, not implemented)

- **Custom domain + managed TLS certificate** for both the Static Web App and App Service.
- **Azure Entra ID** as an alternative/additional login method (see ARCHITECTURE.md's security model section) — would use `msal` on the frontend and validate Entra-issued tokens on the backend instead of (or alongside) the current JWT issuer.
- **Application Insights auto-instrumentation**: add `azure-monitor-opentelemetry` to `requirements.txt` and two lines in `main.py` (`configure_azure_monitor(connection_string=...)`) to get automatic request/dependency tracing — the resource is already provisioned by Bicep, just not wired into the app yet.
- **Alembic migrations** in place of `create_all()`, needed before the schema changes again after this is deployed with real data in it.
- **A background job** (Azure Function on a timer, or a WebJob) to hard-delete blobs for resumes soft-deleted past a retention window.
- **VNet integration + private endpoints** for SQL and Storage, if the data sensitivity of stored resumes warrants it beyond the current "public endpoint + firewall rule + TLS" posture.
