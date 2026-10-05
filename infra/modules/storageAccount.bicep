@description('Name of the storage account. Must be globally unique, lowercase, 3-24 chars.')
param name string

@description('Azure region.')
param location string

@description('Blob container used to store original uploads, generated JSON, PDF, and DOCX files.')
param containerName string = 'resumes'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: name
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
    }
  }

  resource blobService 'blobServices' = {
    name: 'default'

    resource container 'containers' = {
      name: containerName
      properties: {
        publicAccess: 'None'
      }
    }
  }
}

output id string = storageAccount.id
output name string = storageAccount.name
// Consumers use this with the account key (fetched via listKeys, kept out of
// source control) to build AZURE_STORAGE_CONNECTION_STRING - see
// DEPLOYMENT.md for the exact az cli command.
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
