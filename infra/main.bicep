// Azure platform for the Emory STEM Advisor.
//
// Two-phase deployment, driven by deploy/azure/deploy.sh:
//   1. deployApps=false creates the registry, database, storage, vault,
//      monitoring, identity and Container Apps environment.
//   2. The script builds/pushes images and sets Key Vault secrets.
//   3. deployApps=true creates the API, web, worker and migrate job.
//
// This base deliberately uses public ingress and a PostgreSQL firewall rule for
// Azure services. Private networking, Blob-backed artifact storage and the
// school's custom identity adapter are follow-up work; see infra/README.md.

targetScope = 'resourceGroup'

@description('Short environment name used in every resource name (dev, staging, prod).')
@minLength(1)
@maxLength(16)
param environmentName string = 'dev'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Lowercase prefix for resource names.')
@minLength(3)
@maxLength(12)
param namePrefix string = 'advisor'

@description('Tags applied to every resource.')
param tags object = {
  workload: 'emory-stem-advisor'
  environment: environmentName
}

@description('Create the Container Apps, web app and migrate job. The deploy script sets this true after images exist.')
param deployApps bool = false

@description('Container image for the API and migrate job (for example myregistry.azurecr.io/advisor-api:20261018).')
param apiImage string = ''

@description('Container image for the worker. Defaults to apiImage when empty.')
param workerImage string = ''

@description('Container image for the nginx web container.')
param webImage string = ''

@description('Public base URL used for OIDC redirects. Empty derives the Container Apps web FQDN.')
param publicBaseUrl string = ''

@description('PostgreSQL administrator login.')
@minLength(1)
@maxLength(63)
param postgresAdminLogin string = 'advisoradmin'

@description('PostgreSQL administrator password. Use a URL-safe value or let the deploy script encode it.')
@secure()
@minLength(8)
param postgresAdminPassword string

@description('PostgreSQL compute SKU.')
param postgresSkuName string = 'Standard_D2ds_v5'

@description('PostgreSQL tier: Burstable, GeneralPurpose or MemoryOptimized.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param postgresTier string = 'GeneralPurpose'

@description('PostgreSQL storage in GiB.')
@minValue(32)
@maxValue(16384)
param postgresStorageGb int = 128

@description('Days of PostgreSQL point-in-time restore retained.')
@minValue(7)
@maxValue(35)
param postgresBackupRetentionDays int = 14

@description('PostgreSQL high availability. ZoneRedundant requires an AZ-enabled region.')
@allowed([
  'Disabled'
  'ZoneRedundant'
])
param postgresHighAvailability string = 'Disabled'

@description('Model provider: openai, openai-compatible, anthropic or google.')
param llmProvider string = 'openai-compatible'

@description('Deployment or model name for the generation endpoint.')
param llmModelId string

@description('Optional provider base URL (Azure OpenAI endpoint / gateway).')
param llmBaseUrl string = ''

@description('Model context window used for conversation compaction accounting.')
@minValue(1000)
@maxValue(2000000)
param llmContextWindowTokens int = 128000

@description('Automatically compact conversation history at the threshold below.')
param conversationCompactionEnabled bool = true

@description('Fraction of the model context window that triggers compaction (0.05-0.95).')
param conversationCompactionThreshold string = '0.5'

@description('Raw recent turns kept verbatim after a checkpoint.')
@minValue(1)
@maxValue(50)
param conversationCompactionKeepTurns int = 4

@description('Maximum tokens for one compaction handoff.')
@minValue(128)
@maxValue(8192)
param conversationCompactionMaxOutputTokens int = 2048

@description('UTF-8 bytes-per-token estimate used for context accounting.')
@minValue(1)
@maxValue(16)
param conversationTokenEstimateBytes int = 4

@description('Embedding model id. The production image preloads the default Granite model.')
param embeddingModelId string = 'ibm-granite/granite-embedding-125m-english'

@description('Authentication mode: oidc for real deployments. development is unsafe on public ingress.')
@allowed([
  'oidc'
  'development'
])
param authMode string = 'oidc'

@description('OIDC issuer URL.')
param oidcIssuer string = ''

@description('OIDC client id.')
param oidcClientId string = ''

@description('Comma-separated emails promoted to admin on first login only.')
param adminBootstrapEmails string = ''

@description('Comma-separated OIDC subjects promoted to admin on first login only.')
param adminBootstrapSubjects string = ''

@description('Serve the institutional Admin bundle and API. Admin role still required.')
param adminPanelEnabled bool = true

@description('Serve the Debug bundle and accept scoped overrides. Admin role still required.')
param debugPanelEnabled bool = true

@description('Lifetime of one admin debug override session.')
@minValue(300)
@maxValue(86400)
param debugSessionTtlSeconds int = 28800

@description('Minimum API replicas.')
@minValue(1)
@maxValue(30)
param apiMinReplicas int = 1

@description('Maximum API replicas.')
@minValue(1)
@maxValue(30)
param apiMaxReplicas int = 10

@description('Worker replicas. Keep at one until per-job throughput is measured.')
@minValue(1)
@maxValue(20)
param workerReplicas int = 1

@description('Concurrent jobs executed inside one worker process.')
@minValue(1)
@maxValue(32)
param workerConcurrency int = 4

@description('Per-process PostgreSQL pool size used by the API and worker.')
@minValue(1)
@maxValue(200)
param dbPoolMaxSize int = 20

@description('Per-process AnyIO thread tokens for sync dependencies and handlers.')
@minValue(1)
@maxValue(1000)
param httpThreadpoolTokens int = 80

@description('Outstanding conversation turns allowed per workspace (0 disables).')
@minValue(0)
@maxValue(100000)
param admissionMaxPendingWorkspace int = 4

@description('Outstanding conversation turns allowed across the instance (0 disables).')
@minValue(0)
@maxValue(1000000)
param admissionMaxPendingGlobal int = 200

@description('Optional Azure Files share size in GiB for DATA_DIR.')
@minValue(1)
@maxValue(5120)
param dataShareQuotaGb int = 100

var suffix = uniqueString(resourceGroup().id, environmentName)
var compactPrefix = replace(namePrefix, '-', '')
var acrName = toLower(take('${compactPrefix}${suffix}', 50))
var logAnalyticsName = take('${namePrefix}-logs-${suffix}', 63)
var appInsightsName = take('${namePrefix}-appi-${suffix}', 63)
var storageName = toLower(take('${compactPrefix}${suffix}', 24))
var postgresName = toLower(take('${namePrefix}-pg-${suffix}', 63))
var keyVaultName = take('${namePrefix}-kv-${suffix}', 24)
var identityName = take('${namePrefix}-id-${suffix}', 128)
var environmentNameResource = take('${namePrefix}-env-${suffix}', 32)
var apiName = take('${namePrefix}-api-${suffix}', 32)
var webName = take('${namePrefix}-web-${suffix}', 32)
var workerName = take('${namePrefix}-worker-${suffix}', 32)
var migrateJobName = take('${namePrefix}-migrate-${suffix}', 32)
var dataShareName = 'advisor-data'

var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var keyVaultSecretsUserRole = '4633458b-17de-408a-b874-0445c86b69e6'
var storageBlobDataContributorRole = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'artifacts'
  properties: {
    publicAccess: 'None'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource dataShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: dataShareName
  properties: {
    shareQuota: dataShareQuotaGb
    accessTier: 'TransactionOptimized'
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' = {
  name: postgresName
  location: location
  tags: tags
  sku: {
    name: postgresSkuName
    tier: postgresTier
  }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: postgresStorageGb
    }
    backup: {
      backupRetentionDays: postgresBackupRetentionDays
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: postgresHighAvailability
    }
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
  }
}

resource advisorDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-12-01' = {
  parent: postgres
  name: 'advisor'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// The schema runs CREATE EXTENSION vector; Flexible Server requires the
// extension to be allowlisted first.
resource vectorExtension 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2022-12-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    value: 'VECTOR'
    source: 'user-override'
  }
}

// Base scaffold only: this permits Azure service traffic. Replace with a
// private endpoint/VNet before student data is loaded.
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: postgres
  name: 'allow-azure-services'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: environmentNameResource
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Azure Files is the interim shared DATA_DIR for more than one replica.
// Replace it with the Blob adapter before horizontal API scaling is required
// for correctness (not just convenience).
resource environmentDataStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: containerEnvironment
  name: dataShareName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: dataShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRole)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', acrPullRole)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource keyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, keyVaultSecretsUserRole)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRole)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storageBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, storageBlobDataContributorRole)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRole)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

module apps 'modules/apps.bicep' = if (deployApps) {
  name: 'advisor-apps'
  params: {
    location: location
    tags: tags
    environmentId: containerEnvironment.id
    defaultDomain: containerEnvironment.properties.defaultDomain
    acrLoginServer: registry.properties.loginServer
    identityId: identity.id
    keyVaultUri: keyVault.properties.vaultUri
    storageName: environmentDataStorage.name
    apiName: apiName
    webName: webName
    workerName: workerName
    migrateJobName: migrateJobName
    apiImage: apiImage
    webImage: webImage
    workerImage: empty(workerImage) ? apiImage : workerImage
    publicBaseUrl: publicBaseUrl
    llmProvider: llmProvider
    llmModelId: llmModelId
    llmBaseUrl: llmBaseUrl
    llmContextWindowTokens: llmContextWindowTokens
    conversationCompactionEnabled: conversationCompactionEnabled
    conversationCompactionThreshold: conversationCompactionThreshold
    conversationCompactionKeepTurns: conversationCompactionKeepTurns
    conversationCompactionMaxOutputTokens: conversationCompactionMaxOutputTokens
    conversationTokenEstimateBytes: conversationTokenEstimateBytes
    embeddingModelId: embeddingModelId
    authMode: authMode
    adminBootstrapEmails: adminBootstrapEmails
    adminBootstrapSubjects: adminBootstrapSubjects
    adminPanelEnabled: adminPanelEnabled
    debugPanelEnabled: debugPanelEnabled
    debugSessionTtlSeconds: debugSessionTtlSeconds
    oidcIssuer: oidcIssuer
    oidcClientId: oidcClientId
    apiMinReplicas: apiMinReplicas
    apiMaxReplicas: apiMaxReplicas
    workerReplicas: workerReplicas
    workerConcurrency: workerConcurrency
    dbPoolMaxSize: dbPoolMaxSize
    httpThreadpoolTokens: httpThreadpoolTokens
    admissionMaxPendingWorkspace: admissionMaxPendingWorkspace
    admissionMaxPendingGlobal: admissionMaxPendingGlobal
  }
}

output resourceGroupName string = resourceGroup().name
output acrLoginServer string = registry.properties.loginServer
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output postgresName string = postgres.name
output storageAccountName string = storage.name
output dataShareName string = environmentDataStorage.name
output containerEnvironmentName string = containerEnvironment.name
output defaultDomain string = containerEnvironment.properties.defaultDomain
output apiName string = apiName
output webName string = webName
output workerName string = workerName
output migrateJobName string = migrateJobName
output apiUrl string = deployApps ? 'https://${apiName}.${containerEnvironment.properties.defaultDomain}' : ''
output webUrl string = deployApps ? 'https://${webName}.${containerEnvironment.properties.defaultDomain}' : ''
