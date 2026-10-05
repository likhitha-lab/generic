@description('Name of the Static Web App (frontend).')
param name string

@description('Azure region. Static Web Apps are only available in a subset of regions.')
param location string

@description('SKU: Free or Standard. Standard is required for custom auth/roles integration and staging environments.')
param skuName string = 'Free'

resource staticWebApp 'Microsoft.Web/staticSites@2023-01-01' = {
  name: name
  location: location
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    // Deployment is driven by GitHub Actions (see .github/workflows/frontend-cicd.yml),
    // not by a repository connection on the resource itself, so provider-managed
    // fields are intentionally left empty.
    buildProperties: {
      appLocation: 'frontend'
      outputLocation: 'dist'
    }
  }
}

output id string = staticWebApp.id
output name string = staticWebApp.name
output defaultHostname string = staticWebApp.properties.defaultHostname
