// Container Apps for the advisor. Deployed only after the registry contains the
// images and Key Vault contains the referenced secrets.

param location string
param tags object = {}

param environmentId string
param defaultDomain string
param acrLoginServer string
param identityId string
param keyVaultUri string
param storageName string

param apiName string
param webName string
param workerName string
param migrateJobName string
param backupJobName string
param backupSchedule string = '0 2 * * *'

param apiImage string
param webImage string
param workerImage string

param publicBaseUrl string = ''

param llmProvider string
param llmModelId string
param llmBaseUrl string = ''
param embeddingModelId string

param authMode string
param adminBootstrapEmails string
param adminBootstrapSubjects string
param adminPanelEnabled bool
param debugPanelEnabled bool
param debugSessionTtlSeconds int
param oidcIssuer string = ''
param oidcClientId string = ''

param apiMinReplicas int
param apiMaxReplicas int
param workerMinReplicas int
param workerMaxReplicas int
param workerConcurrency int
param workerDrainSeconds int

param dbPoolMaxSize int
param httpThreadpoolTokens int
param admissionMaxPendingWorkspace int
param admissionMaxPendingGlobal int
param workspaceStorageMaxBytes int

var apiPort = 8000
var webPort = 8080
var workerHealthPort = 8081
var dataDir = '/workspace/data'
var webBaseUrl = publicBaseUrl != '' ? publicBaseUrl : 'https://${webName}.${defaultDomain}'

func plainEnv(values object) array => map(items(values), item => {
  name: item.key
  value: item.value
})

func secretEnv(refs object) array => map(items(refs), item => {
  name: item.key
  secretRef: item.value
})

// Liveness restarts a stuck process; readiness holds traffic until it is up.
func httpProbes(port int, livenessPath string, readinessPath string) array => [
  {
    type: 'Liveness'
    httpGet: { path: livenessPath, port: port, scheme: 'HTTP' }
    initialDelaySeconds: 15
    periodSeconds: 10
    timeoutSeconds: 3
    failureThreshold: 3
  }
  {
    type: 'Readiness'
    httpGet: { path: readinessPath, port: port, scheme: 'HTTP' }
    initialDelaySeconds: 5
    periodSeconds: 10
    timeoutSeconds: 3
    failureThreshold: 3
  }
]

// Environment variable -> Key Vault secret of the same name. deploy.sh writes
// every one of these before this module runs, and the user-assigned identity
// has Key Vault Secrets User on the vault.
var appSecretEnv = {
  DATABASE_URL: 'database-url'
  // Port 5432, past PgBouncer: LISTEN, migrations, backups, probes and the
  // worker scaler. Equals database-url when the pooler is off.
  DATABASE_DIRECT_URL: 'database-direct-url'
  LLM_API_KEY: 'llm-api-key'
  AUTH_SERVER_KEY: 'auth-server-key'
  PROFILE_ENCRYPTION_KEY: 'profile-encryption-key'
  OIDC_CLIENT_SECRET: 'oidc-client-secret'
}

// Versionless references, so a rotated secret reaches the next revision.
var appSecrets = map(items(appSecretEnv), item => {
  name: item.value
  keyVaultUrl: '${keyVaultUri}secrets/${item.value}'
  identity: identityId
})

var appSettings = {
  DATA_DIR: dataDir
  DEFAULT_COLLECTION: 'default'
  LLM_PROVIDER: llmProvider
  LLM_MODEL_ID: llmModelId
  LLM_BASE_URL: llmBaseUrl
  EMBEDDING_PROVIDER: 'granite'
  EMBEDDING_MODEL_ID: embeddingModelId
  EMBEDDING_DEVICE: 'cpu'
  TOOLS_ENABLED: 'true'
  AUTH_MODE: authMode
  OIDC_ISSUER: oidcIssuer
  OIDC_CLIENT_ID: oidcClientId
  OIDC_REDIRECT_URI: '${webBaseUrl}/api/auth/callback'
  ADMIN_API_ENABLED: 'false'
  ADMIN_PANEL_ENABLED: string(adminPanelEnabled)
  DEBUG_PANEL_ENABLED: string(debugPanelEnabled)
  DEBUG_SESSION_TTL_SECONDS: string(debugSessionTtlSeconds)
  ADMIN_BOOTSTRAP_EMAILS: adminBootstrapEmails
  ADMIN_BOOTSTRAP_SUBJECTS: adminBootstrapSubjects
  DB_POOL_MAX_SIZE: string(dbPoolMaxSize)
  // Only checked without a pooler; behind PgBouncer the pooler caps server connections.
  DB_EXPECTED_REPLICAS: string(max(apiMaxReplicas, workerMaxReplicas))
  HTTP_THREADPOOL_TOKENS: string(httpThreadpoolTokens)
  ADMISSION_MAX_PENDING_WORKSPACE: string(admissionMaxPendingWorkspace)
  ADMISSION_MAX_PENDING_GLOBAL: string(admissionMaxPendingGlobal)
  WORKSPACE_STORAGE_MAX_BYTES: string(workspaceStorageMaxBytes)
}

var appEnv = concat(plainEnv(appSettings), secretEnv(appSecretEnv))
var workerEnv = concat(appEnv, plainEnv({ WORKER_HEALTH_PORT: string(workerHealthPort) }))

// Migrations and backups are whole sessions, so they skip the pooler entirely.
var migrateEnv = concat(
  plainEnv({ DEFAULT_COLLECTION: 'default', AUTH_MODE: authMode }),
  secretEnv({ DATABASE_URL: 'database-direct-url' }))
var backupEnv = concat(
  plainEnv({ DATA_DIR: dataDir }),
  secretEnv({ DATABASE_URL: 'database-direct-url' }))

var registries = [
  {
    server: acrLoginServer
    identity: identityId
  }
]
var serviceIdentity = {
  type: 'UserAssigned'
  userAssignedIdentities: {
    '${identityId}': {}
  }
}

// The shared Azure Files DATA_DIR, until the Blob adapter replaces it.
var dataVolumeName = 'advisor-data'
var dataVolumes = [
  {
    name: dataVolumeName
    storageType: 'AzureFile'
    storageName: storageName
  }
]
var dataVolumeMounts = [
  {
    volumeName: dataVolumeName
    mountPath: dataDir
  }
]

var latestRevisionOnly = [
  {
    latestRevision: true
    weight: 100
  }
]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: apiPort
        transport: 'http'
        allowInsecure: false
        traffic: latestRevisionOnly
      }
      registries: registries
      secrets: appSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: appEnv
          probes: httpProbes(apiPort, '/health', '/ready')
          volumeMounts: dataVolumeMounts
        }
      ]
      volumes: dataVolumes
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: webName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: webPort
        transport: 'http'
        allowInsecure: false
        traffic: latestRevisionOnly
      }
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'web'
          image: webImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: plainEnv({ API_UPSTREAM: 'https://${apiName}.${defaultDomain}' })
          probes: httpProbes(webPort, '/', '/')
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: workerName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registries
      secrets: appSecrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: workerImage
          command: [
            'python'
            '-m'
            'scripts.worker'
            '--concurrency'
            string(workerConcurrency)
            '--drain-seconds'
            string(workerDrainSeconds)
          ]
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: workerEnv
          probes: httpProbes(workerHealthPort, '/health', '/ready')
          volumeMounts: dataVolumeMounts
        }
      ]
      volumes: dataVolumes
      // SIGTERM starts the drain; the platform kills the container after this.
      terminationGracePeriodSeconds: workerDrainSeconds + 30
      scale: {
        minReplicas: workerMinReplicas
        maxReplicas: workerMaxReplicas
        rules: [
          {
            // KEDA's postgresql scaler: replicas = ceil(jobs needing a slot /
            // slots per replica). Running jobs count too, so a busy replica is
            // not scaled in under its own answers. The query uses the jobs
            // table's two partial indexes (queued, running).
            name: 'job-slots'
            custom: {
              type: 'postgresql'
              metadata: {
                query: 'SELECT count(*) FROM jobs WHERE status = \'running\' OR (status = \'queued\' AND available_at <= now())'
                targetQueryValue: string(workerConcurrency)
                activationTargetQueryValue: '0'
              }
              auth: [
                {
                  secretRef: 'database-direct-url'
                  triggerParameter: 'connection'
                }
              ]
            }
          }
        ]
      }
    }
  }
}

resource backupJob 'Microsoft.App/jobs@2024-03-01' = {
  name: backupJobName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Schedule'
      replicaRetryLimit: 1
      replicaTimeout: 3600
      scheduleTriggerConfig: {
        cronExpression: backupSchedule
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: registries
      secrets: appSecrets
    }
    template: {
      containers: [
        {
          name: 'backup'
          image: apiImage
          command: [
            '/bin/sh'
            '-c'
            'python -m scripts.backup create --backup "${dataDir}/backups/advisor-$(date -u +%Y%m%dT%H%M%SZ).zip" --maintenance-confirmed'
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: backupEnv
          volumeMounts: dataVolumeMounts
        }
      ]
      volumes: dataVolumes
    }
  }
}

resource migrateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: migrateJobName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaRetryLimit: 1
      replicaTimeout: 900
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: registries
      secrets: appSecrets
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: apiImage
          command: [
            'python'
            '-m'
            'scripts.migrate'
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: migrateEnv
        }
      ]
    }
  }
}
