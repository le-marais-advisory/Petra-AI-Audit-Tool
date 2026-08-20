targetScope = 'resourceGroup'

@description('Azure region for the environment.')
param location string = resourceGroup().location

@description('Logical azd environment name used for tags and unique naming.')
param environmentName string

@description('Optional tags applied to provisioned Azure resources.')
param tags object = {}

param containerAppsEnvironmentName string = 'petra-env'
param logAnalyticsWorkspaceName string = 'petra-logs'
param backendContainerAppName string = 'petra-api'
param frontendContainerAppName string = 'petra-frontend'
param acrPullIdentityName string = 'petra-acr-pull'
param containerRegistryName string = toLower(take('petra${uniqueString(subscription().subscriptionId, resourceGroup().id, environmentName)}', 50))

@description('Microsoft Entra tenant ID used by both the frontend SPA and protected API.')
param azureTenantId string

@description('App registration client ID for the protected backend API.')
param azureApiClientId string

@description('App registration client ID for the frontend SPA.')
param azureSpaClientId string

param azureRequiredScope string = 'access_as_user'
param authEnabled bool = true

@description('Show the validation rules sidebar in the frontend (dev affordance; hidden in client-facing deployments).')
param showRulesSidebar bool = false
param appName string = 'Petra Vision'
param apiPrefix string = '/api/v1'
param localFrontendOrigin string = 'http://localhost:5173'
param localWorkdir string = '/tmp/petra-data'
param apiAllowedOrigins string = ''

param textProvider string = 'openai'
param visionProvider string = 'openai'
param openAiTextModel string = ''
param openAiVisionModel string = ''
param openAiTextTemperature string = ''
param openAiTextMaxCompletionTokens string = ''
param claudeTextModel string = ''
param claudeVisionModel string = ''
param claudeTextTemperature string = ''
param claudeVisionTemperature string = ''
param claudeTextMaxTokens string = ''
param claudeVisionMaxTokens string = ''

@secure()
param openAiApiKey string = ''

@secure()
param anthropicApiKey string = ''

param backendProvisioningImage string = 'nginx:1.27-alpine'
param frontendProvisioningImage string = 'nginx:1.27-alpine'
param backendTargetPort int = 80
param frontendTargetPort int = 80

var acrPullRoleDefinitionId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var azureAuthority = 'https://login.microsoftonline.com/${azureTenantId}'
var azureAudience = 'api://${azureApiClientId}'
var azureApiScope = '${azureAudience}/${azureRequiredScope}'
var frontendEndpoint = 'https://${frontendContainerAppName}.${containerAppsEnvironment.properties.defaultDomain}'
var backendEndpoint = 'https://${backendContainerAppName}.${containerAppsEnvironment.properties.defaultDomain}'
var apiAllowedOriginsValue = empty(apiAllowedOrigins)
  ? '${localFrontendOrigin},${frontendEndpoint}'
  : apiAllowedOrigins

var backendSecrets = concat(
  !empty(openAiApiKey) ? [
    {
      name: 'openai-api-key'
      value: openAiApiKey
    }
  ] : [],
  !empty(anthropicApiKey) ? [
    {
      name: 'anthropic-api-key'
      value: anthropicApiKey
    }
  ] : []
)

var backendEnv = concat(
  [
    {
      name: 'APP_NAME'
      value: appName
    }
    {
      name: 'APP_ENV'
      value: 'production'
    }
    {
      name: 'ENABLE_UI'
      value: 'false'
    }
    {
      name: 'AUTH_ENABLED'
      value: authEnabled ? 'true' : 'false'
    }
    {
      name: 'LOCAL_WORKDIR'
      value: localWorkdir
    }
    {
      name: 'API_ALLOWED_ORIGINS'
      value: apiAllowedOriginsValue
    }
    {
      name: 'TEXT_PROVIDER'
      value: textProvider
    }
    {
      name: 'VISION_PROVIDER'
      value: visionProvider
    }
    {
      name: 'AZURE_TENANT_ID'
      value: azureTenantId
    }
    {
      name: 'AZURE_CLIENT_ID'
      value: azureApiClientId
    }
    {
      name: 'AZURE_AUDIENCE'
      value: azureAudience
    }
    {
      name: 'AZURE_REQUIRED_SCOPE'
      value: azureRequiredScope
    }
    {
      name: 'AZURE_ALLOWED_CLIENT_APP_IDS'
      value: azureSpaClientId
    }
    {
      name: 'AZURE_ACCEPTED_TOKEN_VERSIONS'
      value: '1.0,2.0'
    }
  ],
  !empty(openAiApiKey) ? [
    {
      name: 'OPENAI_API_KEY'
      secretRef: 'openai-api-key'
    }
  ] : [],
  !empty(anthropicApiKey) ? [
    {
      name: 'ANTHROPIC_API_KEY'
      secretRef: 'anthropic-api-key'
    }
  ] : [],
  !empty(openAiTextModel) ? [
    {
      name: 'OPENAI_TEXT_MODEL'
      value: openAiTextModel
    }
  ] : [],
  !empty(openAiTextTemperature) ? [
    {
      name: 'OPENAI_TEXT_TEMPERATURE'
      value: openAiTextTemperature
    }
  ] : [],
  !empty(openAiTextMaxCompletionTokens) ? [
    {
      name: 'OPENAI_TEXT_MAX_COMPLETION_TOKENS'
      value: openAiTextMaxCompletionTokens
    }
  ] : [],
  !empty(openAiVisionModel) ? [
    {
      name: 'OPENAI_VISION_MODEL'
      value: openAiVisionModel
    }
  ] : [],
  !empty(claudeTextModel) ? [
    {
      name: 'CLAUDE_TEXT_MODEL'
      value: claudeTextModel
    }
  ] : [],
  !empty(claudeTextTemperature) ? [
    {
      name: 'CLAUDE_TEXT_TEMPERATURE'
      value: claudeTextTemperature
    }
  ] : [],
  !empty(claudeVisionModel) ? [
    {
      name: 'CLAUDE_VISION_MODEL'
      value: claudeVisionModel
    }
  ] : [],
  !empty(claudeVisionTemperature) ? [
    {
      name: 'CLAUDE_VISION_TEMPERATURE'
      value: claudeVisionTemperature
    }
  ] : [],
  !empty(claudeTextMaxTokens) ? [
    {
      name: 'CLAUDE_TEXT_MAX_TOKENS'
      value: claudeTextMaxTokens
    }
  ] : [],
  !empty(claudeVisionMaxTokens) ? [
    {
      name: 'CLAUDE_VISION_MAX_TOKENS'
      value: claudeVisionMaxTokens
    }
  ] : []
)

var frontendEnv = [
  {
    name: 'VITE_APP_NAME'
    value: appName
  }
  {
    name: 'VITE_API_BASE_URL'
    value: backendEndpoint
  }
  {
    name: 'VITE_API_PREFIX'
    value: apiPrefix
  }
  {
    name: 'VITE_AUTH_ENABLED'
    value: authEnabled ? 'true' : 'false'
  }
  {
    name: 'VITE_AZURE_CLIENT_ID'
    value: azureSpaClientId
  }
  {
    name: 'VITE_AZURE_TENANT_ID'
    value: azureTenantId
  }
  {
    name: 'VITE_AZURE_AUTHORITY'
    value: azureAuthority
  }
  {
    name: 'VITE_AZURE_REDIRECT_URI'
    value: frontendEndpoint
  }
  {
    name: 'VITE_AZURE_POST_LOGOUT_REDIRECT_URI'
    value: frontendEndpoint
  }
  {
    name: 'VITE_API_SCOPE'
    value: azureApiScope
  }
  {
    name: 'VITE_SHOW_RULES_SIDEBAR'
    value: showRulesSidebar ? 'true' : 'false'
  }
]

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
  })
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
  })
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource acrPullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: acrPullIdentityName
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
  })
}

resource acrPullIdentityRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, acrPullIdentity.id, acrPullRoleDefinitionId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleDefinitionId)
    principalId: acrPullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Persistent storage for feedback and app data
// ---------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: toLower(take('petrast${uniqueString(resourceGroup().id, environmentName)}', 24))
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
  })
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'petra-data'
  properties: {
    shareQuota: 1
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
  })
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: containerAppsEnvironment
  name: 'petra-data-storage'
  properties: {
    azureFile: {
      accountName: storageAccount.name
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: fileShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource backendContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: backendContainerAppName
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
    'azd-service-name': 'api'
  })
  identity: {
    type: 'SystemAssigned,UserAssigned'
    userAssignedIdentities: {
      '${acrPullIdentity.id}': {}
    }
  }
  dependsOn: [
    acrPullIdentityRole
    envStorage
  ]
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'single'
      ingress: {
        external: true
        targetPort: backendTargetPort
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: acrPullIdentity.id
        }
      ]
      secrets: backendSecrets
    }
    template: {
      volumes: [
        {
          name: 'petra-data-vol'
          storageName: envStorage.name
          storageType: 'AzureFile'
        }
      ]
      containers: [
        {
          name: 'api'
          image: backendProvisioningImage
          env: backendEnv
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          volumeMounts: [
            {
              volumeName: 'petra-data-vol'
              mountPath: '/app/data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource frontendContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendContainerAppName
  location: location
  tags: union(tags, {
    'azd-env-name': environmentName
    'azd-service-name': 'frontend'
  })
  identity: {
    type: 'SystemAssigned,UserAssigned'
    userAssignedIdentities: {
      '${acrPullIdentity.id}': {}
    }
  }
  dependsOn: [
    acrPullIdentityRole
  ]
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'single'
      ingress: {
        external: true
        targetPort: frontendTargetPort
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: acrPullIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: frontendProvisioningImage
          env: frontendEnv
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

output FRONTEND_ENDPOINT string = frontendEndpoint
output BACKEND_ENDPOINT string = backendEndpoint
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.properties.loginServer
output AZURE_CONTAINER_REGISTRY_MANAGED_IDENTITY_ID string = acrPullIdentity.id
output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.name
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = containerAppsEnvironment.name
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerAppsEnvironment.id
output AZURE_CONTAINER_APPS_ENVIRONMENT_DEFAULT_DOMAIN string = containerAppsEnvironment.properties.defaultDomain
output AZURE_CONTAINER_ENVIRONMENT_NAME string = containerAppsEnvironment.name
output AZURE_CONTAINER_ENVIRONMENT_ID string = containerAppsEnvironment.id
output SERVICE_API_CONTAINER_ENVIRONMENT_NAME string = containerAppsEnvironment.name
output SERVICE_FRONTEND_CONTAINER_ENVIRONMENT_NAME string = containerAppsEnvironment.name
output BACKEND_CONTAINER_APP_NAME string = backendContainerApp.name
output FRONTEND_CONTAINER_APP_NAME string = frontendContainerApp.name
