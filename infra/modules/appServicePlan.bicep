@description('Name of the App Service Plan.')
param name string

@description('Azure region.')
param location string

@description('SKU for the Linux App Service Plan hosting the FastAPI backend.')
param skuName string = 'B1'

resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: name
  location: location
  sku: {
    name: skuName
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

output id string = plan.id
