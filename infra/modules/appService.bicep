@description('Name of the App Service (backend API). Must be globally unique.')
param name string

@description('Azure region.')
param location string

@description('Resource ID of the App Service Plan.')
param appServicePlanId string

@description('Python runtime version.')
param linuxFxVersion string = 'PYTHON|3.12'

@description('Non-secret app settings. Secrets (JWT key, DB connection string, storage connection string) are pulled from Key Vault at startup by app/core/keyvault.py - only the Key Vault URL is set here.')
param appSettings array = []

resource appService 'Microsoft.Web/sites@2023-01-01' = {
  name: name
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlanId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      appCommandLine: 'gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000'
      appSettings: appSettings
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
  }
}

output id string = appService.id
output name string = appService.name
output defaultHostName string = appService.properties.defaultHostName
output principalId string = appService.identity.principalId
