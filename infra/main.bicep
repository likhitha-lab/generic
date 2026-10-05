/**
 * Root deployment template for the AI Resume Management System.
 *
 * This provisions every Azure resource described in ARCHITECTURE.md:
 * App Service (backend), Static Web App (frontend), Blob Storage, Azure SQL,
 * Key Vault, Application Insights (+ its Log Analytics workspace). Secrets
 * (SQL password, Gemini key, JWT signing key, admin bootstrap password) are
 * written into Key Vault as part of this deployment and read by the app at
 * startup via DefaultAzureCredential (see backend/app/core/keyvault.py) -
 * they are never stored in App Service application settings in plain text.
 *
 * This template is NOT deployed as part of building this project. It is
 * provided so deployment can be executed later once real Azure credentials
 * and a subscription are available - see DEPLOYMENT.md for the exact
 * `az deployment group create` command and required parameters.
 */
targetScope = 'resourceGroup'

@description('Short project name used as a prefix for every resource name, e.g. "resumemgr".')
@minLength(3)
@maxLength(12)
param projectName string = 'resumemgr'

@description('Environment suffix, e.g. dev, staging, prod.')
param environmentName string = 'dev'

@description('Azure region for all resources (Static Web Apps has its own more limited region list - see staticWebAppLocation).')
param location string = resourceGroup().location

@description('Azure region for the Static Web App. Must be one of the supported SWA regions (e.g. eastus2, westus2, centralus, westeurope, eastasia).')
param staticWebAppLocation string = 'eastus2'

@description('SQL Server administrator login name.')
param sqlAdministratorLogin string = 'resumeadmin'

@secure()
@description('SQL Server administrator password. Supply at deploy time - never commit this.')
param sqlAdministratorPassword string

@secure()
@description('Google Gemini API key, written into Key Vault as "gemini-api-key".')
param geminiApiKey string

@secure()
@description('JWT signing secret, written into Key Vault as "jwt-secret-key". Generate with e.g. `openssl rand -hex 32`.')
param jwtSecretKey string

@description('Email address for the seeded admin account.')
param defaultAdminEmail string = 'admin@example.com'

@secure()
@description('Password for the seeded admin account, written into Key Vault as "default-admin-password".')
param defaultAdminPassword string

@description('App Service Plan SKU for the backend.')
param appServicePlanSku string = 'B1'

@description('Static Web App SKU.')
param staticWebAppSku string = 'Free'

var resourceToken = '${projectName}-${environmentName}'
var storageAccountName = replace('${projectName}${environmentName}sa', '-', '')
var keyVaultName = take('${projectName}-${environmentName}-kv', 24)
var sqlServerName = '${resourceToken}-sql'
var sqlDatabaseName = '${projectName}db'
var appServicePlanName = '${resourceToken}-plan'
var appServiceName = '${resourceToken}-api'
var staticWebAppName = '${resourceToken}-web'
var logAnalyticsName = '${resourceToken}-logs'
var appInsightsName = '${resourceToken}-ai'

module logAnalytics 'modules/logAnalytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: logAnalyticsName
    location: location
  }
}

module appInsights 'modules/appInsights.bicep' = {
  name: 'appInsights'
  params: {
    name: appInsightsName
    location: location
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

module storageAccount 'modules/storageAccount.bicep' = {
  name: 'storageAccount'
  params: {
    name: storageAccountName
    location: location
  }
}

module sqlServer 'modules/sqlServer.bicep' = {
  name: 'sqlServer'
  params: {
    serverName: sqlServerName
    databaseName: sqlDatabaseName
    location: location
    administratorLogin: sqlAdministratorLogin
    administratorPassword: sqlAdministratorPassword
  }
}

module appServicePlan 'modules/appServicePlan.bicep' = {
  name: 'appServicePlan'
  params: {
    name: appServicePlanName
    location: location
    skuName: appServicePlanSku
  }
}

module keyVault 'modules/keyVault.bicep' = {
  name: 'keyVault'
  params: {
    name: keyVaultName
    location: location
  }
}

// Existing-resource references so we can attach secrets / read keys without
// re-declaring the resources (the modules above already created them).
resource storageAccountRef 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
  dependsOn: [storageAccount]
}

resource keyVaultRef 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
  dependsOn: [keyVault]
}

var sqlConnectionString = 'mssql+pyodbc://${sqlAdministratorLogin}:${sqlAdministratorPassword}@${sqlServer.outputs.serverFqdn}:1433/${sqlDatabaseName}?driver=ODBC+Driver+18+for+SQL+Server'
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};AccountKey=${storageAccountRef.listKeys().keys[0].value};EndpointSuffix=core.windows.net'

resource secretJwt 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVaultRef
  name: 'jwt-secret-key'
  properties: {
    value: jwtSecretKey
  }
}

resource secretGemini 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVaultRef
  name: 'gemini-api-key'
  properties: {
    value: geminiApiKey
  }
}

resource secretDatabaseUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVaultRef
  name: 'database-url'
  properties: {
    value: sqlConnectionString
  }
}

resource secretStorageConnectionString 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVaultRef
  name: 'azure-storage-connection-string'
  properties: {
    value: storageConnectionString
  }
}

resource secretAdminPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVaultRef
  name: 'default-admin-password'
  properties: {
    value: defaultAdminPassword
  }
}

module staticWebApp 'modules/staticWebApp.bicep' = {
  name: 'staticWebApp'
  params: {
    name: staticWebAppName
    location: staticWebAppLocation
    skuName: staticWebAppSku
  }
}

// Non-secret settings only. Secrets are resolved at container startup from
// Key Vault via DefaultAzureCredential (the App Service's own managed
// identity, granted access below) - see backend/app/core/keyvault.py.
module appService 'modules/appService.bicep' = {
  name: 'appService'
  params: {
    name: appServiceName
    location: location
    appServicePlanId: appServicePlan.outputs.id
    appSettings: [
      { name: 'AZURE_KEY_VAULT_URL', value: keyVault.outputs.uri }
      { name: 'STORAGE_BACKEND', value: 'azure' }
      { name: 'AZURE_STORAGE_CONTAINER', value: 'resumes' }
      { name: 'GEMINI_MODEL', value: 'gemini-2.5-flash' }
      { name: 'DEFAULT_ADMIN_EMAIL', value: defaultAdminEmail }
      { name: 'ALLOWED_ORIGINS', value: 'https://${staticWebApp.outputs.defaultHostname}' }
      { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.outputs.connectionString }
      { name: 'WEBSITES_PORT', value: '8000' }
      { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
    ]
  }
}

// Grants the backend's managed identity read access to Key Vault secrets.
module keyVaultAccess 'modules/keyVault.bicep' = {
  name: 'keyVaultAccess'
  params: {
    name: keyVaultName
    location: location
    accessPrincipalId: appService.outputs.principalId
  }
  dependsOn: [
    keyVault
    appService
  ]
}

output backendUrl string = 'https://${appService.outputs.defaultHostName}'
output frontendUrl string = 'https://${staticWebApp.outputs.defaultHostname}'
output keyVaultUri string = keyVault.outputs.uri
output sqlServerFqdn string = sqlServer.outputs.serverFqdn
output storageAccountName string = storageAccountName
output appServiceName string = appServiceName
output staticWebAppName string = staticWebAppName
