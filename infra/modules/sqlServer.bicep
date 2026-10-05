@description('Name of the logical Azure SQL server. Must be globally unique.')
param serverName string

@description('Name of the SQL database.')
param databaseName string

@description('Azure region.')
param location string

@description('SQL admin login. The password is supplied separately as a secret and never stored in this template.')
param administratorLogin string

@secure()
@description('SQL admin password. Pass via --parameters administratorPassword=$SQL_ADMIN_PASSWORD at deploy time - never hardcode it.')
param administratorPassword string

@description('Database SKU tier. Defaults to the cheapest general-purpose serverless tier suitable for a small app.')
param skuName string = 'GP_S_Gen5_1'

resource sqlServer 'Microsoft.Sql/servers@2023-05-01-preview' = {
  name: serverName
  location: location
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  sku: {
    name: skuName
    tier: 'GeneralPurpose'
  }
  properties: {
    autoPauseDelay: 60
    minCapacity: json('0.5')
  }
}

// Allows Azure services (including App Service) to reach the server.
// Scope this down to specific outbound IPs in production - see DEPLOYMENT.md.
resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output serverFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = sqlDatabase.name
