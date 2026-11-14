// Azure platform for the Emory STEM Advisor.
//
// deploy/azure/deploy.sh deploys this file twice:
//   1. deployApps=false creates the registry, database, storage, vault,
//      monitoring, identity and Container Apps environment. The script then
//      builds the images in the registry and writes the Key Vault secrets.
//   2. deployApps=true adds the API, web, worker, migrate and backup apps.
//
// Every deployment default lives in this file's parameters; the script passes
// only the settings an operator sets.
//
// PostgreSQL, Blob/File storage and Key Vault are reached through private
// endpoints. Only the web container has public ingress; the API is internal to
// the Container Apps environment. Blob-backed artifact storage and the school's
// custom identity adapter remain follow-up work; see infra/README.md.

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

@description('Run Flexible Server\'s built-in PgBouncer (transaction mode, port 6432). The API and workers connect through it, so replicas can autoscale without exhausting server connections; LISTEN, migrations, backups and readiness probes use port 5432. Not available on the Burstable tier, where this is ignored.')
param postgresPooler bool = true

@description('Server connections PgBouncer keeps for the advisor database. Keep it well under max_connections, which leaves room for direct sessions.')
@minValue(10)
@maxValue(1000)
param pgbouncerPoolSize int = 60

@description('Client connections PgBouncer accepts: the sum of every replica\'s pools.')
@minValue(100)
@maxValue(50000)
param pgbouncerMaxClientConnections int = 5000

@description('Model provider: openai, openai-compatible, anthropic or google.')
param llmProvider string = 'openai-compatible'

@description('Deployment or model name for the generation endpoint.')
param llmModelId string

@description('Optional provider base URL (Azure OpenAI endpoint / gateway).')
param llmBaseUrl string = ''

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

@description('Minimum worker replicas. Two keep answers flowing while one restarts or drains.')
@minValue(1)
@maxValue(50)
param workerMinReplicas int = 2

@description('Maximum worker replicas. Workers scale on queued plus running jobs, one replica per workerConcurrency of them. 14 x 24 slots covers about 300 answers in progress, the 6,000-student peak estimate.')
@minValue(1)
@maxValue(50)
param workerMaxReplicas int = 14

@description('Concurrent jobs executed inside one worker process. Jobs mostly wait on the model provider.')
@minValue(1)
@maxValue(32)
param workerConcurrency int = 24

@description('On scale-in or redeploy, seconds a worker waits for in-flight answers before requeueing them. The container gets 30 s more before it is killed (600 s at most). Keep it above RESEARCH_ADVICE_OPERATION_TIMEOUT_SECONDS (540) so the longest answer finishes instead of restarting.')
@minValue(30)
@maxValue(570)
param workerDrainSeconds int = 570

@description('Per-process, per-role PostgreSQL pool size used by the API and worker. Behind PgBouncer these are cheap client connections; keep it at least workerConcurrency.')
@minValue(1)
@maxValue(200)
param dbPoolMaxSize int = 30

@description('Per-process AnyIO thread tokens for sync dependencies and handlers.')
@minValue(1)
@maxValue(1000)
param httpThreadpoolTokens int = 80

@description('Outstanding conversation turns allowed per workspace (0 disables).')
@minValue(0)
@maxValue(100000)
param admissionMaxPendingWorkspace int = 4

@description('Outstanding (queued plus running) conversation turns allowed across the instance (0 disables). Keep it above workerMaxReplicas x workerConcurrency so a burst queues instead of being refused.')
@minValue(0)
@maxValue(1000000)
param admissionMaxPendingGlobal int = 600

@description('Optional Azure Files share size in GiB for DATA_DIR.')
@minValue(1)
@maxValue(5120)
param dataShareQuotaGb int = 100

@description('UTC cron schedule for the logical backup job.')
param backupSchedule string = '0 2 * * *'

@description('Optional email address for release alerts. Empty creates the rules without a receiver.')
param alertEmail string = ''

@description('Per-workspace storage ceiling in bytes: the apps\' WORKSPACE_STORAGE_MAX_BYTES and the storage alert\'s 90% line.')
@minValue(1)
@maxValue(107374182400)
param workspaceStorageMaxBytes int = 104857600

@description('VNet address space for the deployment.')
param vnetAddressPrefix string = '10.42.0.0/16'

@description('Subnet delegated to the Container Apps environment.')
param appsSubnetPrefix string = '10.42.0.0/23'

@description('Subnet that holds the private endpoints.')
param privateEndpointsSubnetPrefix string = '10.42.2.0/24'

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
var backupJobName = take('${namePrefix}-backup-${suffix}', 32)
var dataShareName = 'advisor-data'
// Flexible Server offers the built-in PgBouncer only on General Purpose and
// Memory Optimized compute.
var poolerEnabled = postgresPooler && postgresTier != 'Burstable'
// Behind a transaction pooler the app cannot send per-connection limits, so
// they become server defaults (the app's own DB_STATEMENT_TIMEOUT_MS and
// DB_IDLE_TRANSACTION_TIMEOUT_MS defaults). Direct sessions (migrations,
// backups, scripts) clear them for themselves. Burstable has no PgBouncer
// settings at all.
var poolerSettings = [
  { name: 'pgbouncer.pool_mode', value: 'TRANSACTION' }
  { name: 'pgbouncer.default_pool_size', value: string(pgbouncerPoolSize) }
  { name: 'pgbouncer.max_client_conn', value: string(pgbouncerMaxClientConnections) }
  { name: 'statement_timeout', value: '30000' }
  { name: 'idle_in_transaction_session_timeout', value: '60000' }
]
var postgresSettings = postgresTier == 'Burstable' ? [] : concat(
  [{ name: 'pgbouncer.enabled', value: poolerEnabled ? 'true' : 'false' }],
  poolerEnabled ? poolerSettings : [])
var vnetName = take('${namePrefix}-vnet-${suffix}', 64)
var appsSubnetName = 'apps'
var privateEndpointsSubnetName = 'private-endpoints'

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

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2023-04-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
  }
}

resource appsSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-04-01' = {
  parent: virtualNetwork
  name: appsSubnetName
  properties: {
    addressPrefix: appsSubnetPrefix
    delegations: [
      {
        name: 'container-apps'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource privateEndpointsSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-04-01' = {
  parent: virtualNetwork
  name: privateEndpointsSubnetName
  properties: {
    addressPrefix: privateEndpointsSubnetPrefix
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
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
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

// One at a time: Flexible Server rejects concurrent configuration updates.
@batchSize(1)
resource postgresConfiguration 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2022-12-01' = [for setting in postgresSettings: {
  parent: postgres
  name: setting.name
  properties: {
    value: setting.value
    source: 'user-override'
  }
  dependsOn: [
    vectorExtension
  ]
}]

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
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

// Private access: each service gets a DNS zone linked to the VNet and an
// endpoint in the private-endpoints subnet. `connection` and `zoneConfig` keep
// the names the endpoints were first created with. The target ids are a
// separate list so the DNS zones need not wait for the servers.
var privateLinkTargets = [
  postgres.id
  storage.id
  storage.id
  keyVault.id
]
var privateLinks = [
  {
    zone: 'privatelink.postgres.database.azure.com'
    endpoint: take('${postgresName}-pe', 63)
    groupId: 'postgresqlServer'
    connection: 'postgresql'
    zoneConfig: 'postgres'
  }
  {
    zone: 'privatelink.blob.${environment().suffixes.storage}'
    endpoint: take('${storageName}-blob-pe', 63)
    groupId: 'blob'
    connection: 'blob'
    zoneConfig: 'blob'
  }
  {
    zone: 'privatelink.file.${environment().suffixes.storage}'
    endpoint: take('${storageName}-file-pe', 63)
    groupId: 'file'
    connection: 'file'
    zoneConfig: 'file'
  }
  {
    zone: 'privatelink.vaultcore.azure.net'
    endpoint: take('${keyVaultName}-pe', 63)
    groupId: 'vault'
    connection: 'vault'
    zoneConfig: 'vault'
  }
]

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for link in privateLinks: {
  name: link.zone
  location: 'global'
  tags: tags
}]

resource privateDnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (link, i) in privateLinks: {
  parent: privateDnsZones[i]
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: virtualNetwork.id
    }
    registrationEnabled: false
  }
}]

resource privateEndpoints 'Microsoft.Network/privateEndpoints@2023-04-01' = [for (link, i) in privateLinks: {
  name: link.endpoint
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: link.connection
        properties: {
          privateLinkServiceId: privateLinkTargets[i]
          groupIds: [
            link.groupId
          ]
        }
      }
    ]
  }
}]

resource privateDnsGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01' = [for (link, i) in privateLinks: {
  parent: privateEndpoints[i]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: link.zoneConfig
        properties: {
          privateDnsZoneId: privateDnsZones[i].id
        }
      }
    ]
  }
}]

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentNameResource
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: appsSubnet.id
      internal: false
    }
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
resource environmentDataStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
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

module alerts 'modules/alerts.bicep' = {
  name: 'advisor-alerts'
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    suffix: suffix
    logAnalyticsId: logAnalytics.id
    postgresId: postgres.id
    alertEmail: alertEmail
    storageQuotaBytes: workspaceStorageMaxBytes
    // Behind PgBouncer the expected peak is its server pool, one LISTEN session
    // per worker replica, and short direct probes from every replica, plus room
    // for migrations and backups. Without the pooler, keep a fixed ceiling.
    dbConnectionsThreshold: poolerEnabled
      ? pgbouncerPoolSize + workerMaxReplicas + apiMaxReplicas + 10
      : 80
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
    backupJobName: backupJobName
    backupSchedule: backupSchedule
    apiImage: apiImage
    webImage: webImage
    workerImage: empty(workerImage) ? apiImage : workerImage
    publicBaseUrl: publicBaseUrl
    llmProvider: llmProvider
    llmModelId: llmModelId
    llmBaseUrl: llmBaseUrl
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
    workerMinReplicas: workerMinReplicas
    workerMaxReplicas: workerMaxReplicas
    workerConcurrency: workerConcurrency
    workerDrainSeconds: workerDrainSeconds
    dbPoolMaxSize: dbPoolMaxSize
    httpThreadpoolTokens: httpThreadpoolTokens
    admissionMaxPendingWorkspace: admissionMaxPendingWorkspace
    admissionMaxPendingGlobal: admissionMaxPendingGlobal
    workspaceStorageMaxBytes: workspaceStorageMaxBytes
  }
}

output resourceGroupName string = resourceGroup().name
output acrLoginServer string = registry.properties.loginServer
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output postgresName string = postgres.name
output postgresAdminLogin string = postgresAdminLogin
output postgresPoolerEnabled bool = poolerEnabled
output storageAccountName string = storage.name
output dataShareName string = environmentDataStorage.name
output containerEnvironmentName string = containerEnvironment.name
output defaultDomain string = containerEnvironment.properties.defaultDomain
output apiName string = apiName
output webName string = webName
output workerName string = workerName
output migrateJobName string = migrateJobName
output backupJobName string = backupJobName
output apiUrl string = deployApps ? 'https://${apiName}.${containerEnvironment.properties.defaultDomain}' : ''
output webUrl string = deployApps ? 'https://${webName}.${containerEnvironment.properties.defaultDomain}' : ''
