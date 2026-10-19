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

param apiImage string
param webImage string
param workerImage string

param publicBaseUrl string = ''

param llmProvider string
param llmModelId string
param llmBaseUrl string = ''
param llmContextWindowTokens int
param conversationCompactionEnabled bool
param conversationCompactionThreshold string
param conversationCompactionKeepTurns int
param conversationCompactionMaxOutputTokens int
param conversationTokenEstimateBytes int
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
param workerReplicas int
param workerConcurrency int

param dbPoolMaxSize int
param httpThreadpoolTokens int
param admissionMaxPendingWorkspace int
param admissionMaxPendingGlobal int

var apiFqdn = '${apiName}.${defaultDomain}'
var webBaseUrl = publicBaseUrl != '' ? publicBaseUrl : 'https://${webName}.${defaultDomain}'
var apiUpstream = 'https://${apiFqdn}'
var dataVolumeName = 'advisor-data'
var serviceIdentity = {
  type: 'UserAssigned'
  userAssignedIdentities: {
    '${identityId}': {}
  }
}

// Versionless Key Vault references. The user-assigned identity has Key Vault
// Secrets User on the vault; the deploy script creates every referenced secret.
var appSecrets = [
  {
    name: 'database-url'
    keyVaultUrl: '${keyVaultUri}secrets/database-url'
    identity: identityId
  }
  {
    name: 'llm-api-key'
    keyVaultUrl: '${keyVaultUri}secrets/llm-api-key'
    identity: identityId
  }
  {
    name: 'auth-server-key'
    keyVaultUrl: '${keyVaultUri}secrets/auth-server-key'
    identity: identityId
  }
  {
    name: 'profile-encryption-key'
    keyVaultUrl: '${keyVaultUri}secrets/profile-encryption-key'
    identity: identityId
  }
  {
    name: 'oidc-client-secret'
    keyVaultUrl: '${keyVaultUri}secrets/oidc-client-secret'
    identity: identityId
  }
]

var commonEnv = [
  {
    name: 'DATABASE_URL'
    secretRef: 'database-url'
  }
  {
    name: 'DATA_DIR'
    value: '/workspace/data'
  }
  {
    name: 'DEFAULT_COLLECTION'
    value: 'default'
  }
  {
    name: 'LLM_PROVIDER'
    value: llmProvider
  }
  {
    name: 'LLM_MODEL_ID'
    value: llmModelId
  }
  {
    name: 'LLM_BASE_URL'
    value: llmBaseUrl
  }
  {
    name: 'LLM_API_KEY'
    secretRef: 'llm-api-key'
  }
  {
    name: 'LLM_CONTEXT_WINDOW_TOKENS'
    value: string(llmContextWindowTokens)
  }
  {
    name: 'CONVERSATION_COMPACTION_ENABLED'
    value: string(conversationCompactionEnabled)
  }
  {
    name: 'CONVERSATION_COMPACTION_THRESHOLD'
    value: conversationCompactionThreshold
  }
  {
    name: 'CONVERSATION_COMPACTION_KEEP_TURNS'
    value: string(conversationCompactionKeepTurns)
  }
  {
    name: 'CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS'
    value: string(conversationCompactionMaxOutputTokens)
  }
  {
    name: 'CONVERSATION_COMPACTION_TOKEN_BUDGET'
    value: '0'
  }
  {
    name: 'CONVERSATION_TOKEN_ESTIMATE_BYTES'
    value: string(conversationTokenEstimateBytes)
  }
  {
    name: 'EMBEDDING_PROVIDER'
    value: 'granite'
  }
  {
    name: 'EMBEDDING_MODEL_ID'
    value: embeddingModelId
  }
  {
    name: 'EMBEDDING_DEVICE'
    value: 'cpu'
  }
  {
    name: 'AUTH_MODE'
    value: authMode
  }
  {
    name: 'AUTH_SERVER_KEY'
    secretRef: 'auth-server-key'
  }
  {
    name: 'PROFILE_ENCRYPTION_KEY'
    secretRef: 'profile-encryption-key'
  }
  {
    name: 'OIDC_ISSUER'
    value: oidcIssuer
  }
  {
    name: 'OIDC_CLIENT_ID'
    value: oidcClientId
  }
  {
    name: 'OIDC_CLIENT_SECRET'
    secretRef: 'oidc-client-secret'
  }
  {
    name: 'OIDC_REDIRECT_URI'
    value: '${webBaseUrl}/api/auth/callback'
  }
  {
    name: 'DB_POOL_MAX_SIZE'
    value: string(dbPoolMaxSize)
  }
  {
    name: 'HTTP_THREADPOOL_TOKENS'
    value: string(httpThreadpoolTokens)
  }
  {
    name: 'ADMISSION_MAX_PENDING_WORKSPACE'
    value: string(admissionMaxPendingWorkspace)
  }
  {
    name: 'ADMISSION_MAX_PENDING_GLOBAL'
    value: string(admissionMaxPendingGlobal)
  }
  {
    name: 'ADMIN_API_ENABLED'
    value: 'false'
  }
  {
    name: 'ADMIN_PANEL_ENABLED'
    value: string(adminPanelEnabled)
  }
  {
    name: 'DEBUG_PANEL_ENABLED'
    value: string(debugPanelEnabled)
  }
  {
    name: 'DEBUG_SESSION_TTL_SECONDS'
    value: string(debugSessionTtlSeconds)
  }
  {
    name: 'ADMIN_BOOTSTRAP_EMAILS'
    value: adminBootstrapEmails
  }
  {
    name: 'ADMIN_BOOTSTRAP_SUBJECTS'
    value: adminBootstrapSubjects
  }
  {
    name: 'TOOLS_ENABLED'
    value: 'false'
  }
  {
    name: 'QUERY_PLANNING_ENABLED'
    value: 'false'
  }
  {
    name: 'ADVISING_RESEARCH_ENABLED'
    value: 'false'
  }
]

var workerEnv = concat(commonEnv, [
  {
    name: 'RUN_HANDLER_FACTORY'
    value: 'app.generation.artifacts:default_handlers'
  }
])

var migrateEnv = [
  {
    name: 'DATABASE_URL'
    secretRef: 'database-url'
  }
  {
    name: 'DEFAULT_COLLECTION'
    value: 'default'
  }
  {
    name: 'AUTH_MODE'
    value: authMode
  }
]

resource api 'Microsoft.App/containerApps@2023-05-01' = {
  name: apiName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
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
          env: commonEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 15
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
          volumeMounts: [
            {
              volumeName: dataVolumeName
              mountPath: '/workspace/data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: dataVolumeName
          storageType: 'AzureFile'
          storageName: storageName
        }
      ]
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

resource web 'Microsoft.App/containerApps@2023-05-01' = {
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
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
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
          env: [
            {
              name: 'API_UPSTREAM'
              value: apiUpstream
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 8080
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
                port: 8080
                scheme: 'HTTP'
              }
              initialDelaySeconds: 3
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
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

resource worker 'Microsoft.App/containerApps@2023-05-01' = {
  name: workerName
  location: location
  tags: tags
  identity: serviceIdentity
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
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
          ]
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: workerEnv
          volumeMounts: [
            {
              volumeName: dataVolumeName
              mountPath: '/workspace/data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: dataVolumeName
          storageType: 'AzureFile'
          storageName: storageName
        }
      ]
      scale: {
        minReplicas: workerReplicas
        maxReplicas: workerReplicas
      }
    }
  }
}

resource migrateJob 'Microsoft.App/jobs@2023-05-01' = {
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
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
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
