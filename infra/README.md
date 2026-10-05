# Infrastructure as Code (Bicep)

This folder defines every Azure resource in the architecture as code. **Nothing here has been deployed** - these templates are provided so deployment can be executed once real Azure credentials are available. See `../DEPLOYMENT.md` for the full step-by-step deployment guide, including how to generate the secret parameter values below.

## Layout

- `main.bicep` - root template, wires up all modules and Key Vault secrets.
- `main.parameters.json` - parameter file with placeholders for secrets (never commit real values here - pass them via `--parameters key=value` or a local, gitignored parameters file instead).
- `modules/` - one file per resource type: `appServicePlan.bicep`, `appService.bicep`, `staticWebApp.bicep`, `storageAccount.bicep`, `sqlServer.bicep`, `keyVault.bicep`, `appInsights.bicep`, `logAnalytics.bicep`.

## What gets created

| Resource | Purpose |
|---|---|
| App Service (Linux, Python) | Hosts the FastAPI backend |
| Static Web App | Hosts the built React frontend |
| Storage Account + Blob container | Original uploads, generated JSON/PDF/DOCX |
| Azure SQL Server + Database (serverless) | Users, Roles, Resumes, ResumeVersions, AuditLogs |
| Key Vault | JWT secret, Gemini API key, DB connection string, storage connection string, admin bootstrap password |
| Application Insights + Log Analytics | Monitoring/telemetry |

The App Service is given a system-assigned managed identity with the "Key Vault Secrets User" role on the vault, so the backend reads secrets via `DefaultAzureCredential` at startup (`backend/app/core/keyvault.py`) - no secret ever needs to be pasted into an App Service application setting.

## Validating without deploying

```bash
az bicep build --file main.bicep
az deployment group validate \
  --resource-group <your-rg> \
  --template-file main.bicep \
  --parameters main.parameters.json \
  --parameters sqlAdministratorPassword=$SQL_ADMIN_PASSWORD \
  --parameters geminiApiKey=$GEMINI_API_KEY \
  --parameters jwtSecretKey=$JWT_SECRET_KEY \
  --parameters defaultAdminPassword=$DEFAULT_ADMIN_PASSWORD
```

## Deploying (only once Azure access is available)

See `../DEPLOYMENT.md`.
