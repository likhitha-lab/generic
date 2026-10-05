@description('Name of the Key Vault. Must be globally unique.')
param name string

@description('Azure region.')
param location string

@description('Azure AD tenant ID that owns the vault.')
param tenantId string = subscription().tenantId

@description('Principal ID to grant secret get/list access (e.g. the App Service system-assigned managed identity). Leave empty to skip.')
param accessPrincipalId string = ''

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
  }
}

// Grants the App Service's managed identity the built-in "Key Vault Secrets
// User" role so it can read secrets via DefaultAzureCredential without any
// credentials ever being checked into source control or app settings.
resource secretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(accessPrincipalId)) {
  name: guid(keyVault.id, accessPrincipalId, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: accessPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output id string = keyVault.id
output uri string = keyVault.properties.vaultUri
output name string = keyVault.name
