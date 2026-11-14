// Release alerts for the advisor deployment.
//
// The container and database metrics below are read from the application's
// existing metrics surface: /metrics exposes advisor_jobs (queued/running),
// advisor_job_oldest_queued_seconds, advisory DB pool gauges, and the alert
// probe reads provider failures and workspace storage from the database.
// AppMetrics/AppRequests rows are produced when those signals are exported to
// Application Insights; the queries and thresholds are named here so the
// deployment owns the alert definitions even though telemetry export is an
// operator step.

param location string
param tags object = {}
param namePrefix string
param suffix string
param logAnalyticsId string
param postgresId string
param alertEmail string = ''
param storageQuotaBytes int = 104857600
@description('Active PostgreSQL connections above which the database alert fires.')
param dbConnectionsThreshold int = 80

// Log-query rules over the Log Analytics workspace, one row per alert. The
// rule names keep their first-deploy spelling: <prefix>-alert-<key>-<suffix>.
var logAlerts = [
  {
    key: 'api-5xx'
    displayName: 'API 5xx rate > 5% over 5 minutes'
    description: 'Share of AppRequests with a 5xx result code in the last five minutes. Threshold: 5 percent.'
    severity: 1
    window: 'PT5M'
    query: 'AppRequests | where TimeGenerated > ago(5m) | summarize Total=count(), Failures=countif(toint(ResultCode) >= 500) | extend Rate=100.0*Failures/max(Total,1) | project Rate'
    threshold: 5
  }
  {
    key: 'queue-depth'
    displayName: 'Worker queue depth > 50 over 5 minutes'
    description: 'AppMetrics advisor_jobs with status queued, averaged. Threshold: 50 queued jobs.'
    severity: 2
    window: 'PT5M'
    query: 'AppMetrics | where TimeGenerated > ago(5m) | where Name == "advisor_jobs" and tostring(Properties.status) == "queued" | summarize Value=avg(Sum) | project Value'
    threshold: 50
  }
  {
    key: 'oldest-job'
    displayName: 'Oldest queued job > 60 seconds over 5 minutes'
    description: 'AppMetrics advisor_job_oldest_queued_seconds, averaged. Threshold: 60 seconds.'
    severity: 1
    window: 'PT5M'
    query: 'AppMetrics | where TimeGenerated > ago(5m) | where Name == "advisor_job_oldest_queued_seconds" | summarize Value=avg(Sum) | project Value'
    threshold: 60
  }
  {
    key: 'provider-429'
    displayName: 'Provider 429 rate > 10 per 5 minutes'
    description: 'AppMetrics advisor_provider_rate_limited_total (from model_calls), summed. Threshold: 10 rate-limited calls in five minutes.'
    severity: 1
    window: 'PT5M'
    query: 'AppMetrics | where TimeGenerated > ago(5m) | where Name == "advisor_provider_rate_limited_total" | summarize Value=sum(Sum) | project Value'
    threshold: 10
  }
  {
    key: 'storage'
    displayName: 'Workspace storage > 90% of quota'
    description: 'AppMetrics advisor_workspace_storage_bytes, the largest workspace. Threshold: 90% of WORKSPACE_STORAGE_MAX_BYTES (${storageQuotaBytes} bytes).'
    severity: 2
    window: 'PT15M'
    query: 'AppMetrics | where TimeGenerated > ago(15m) | where Name == "advisor_workspace_storage_bytes" | summarize Value=max(Sum) | project Value'
    threshold: storageQuotaBytes * 9 / 10
  }
]

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: take('${namePrefix}-alerts-${suffix}', 64)
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take('${namePrefix}alerts', 12)
    enabled: true
    emailReceivers: empty(alertEmail) ? [] : [
      {
        name: 'advisor-oncall'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource logAlertRules 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [for alert in logAlerts: {
  name: take('${namePrefix}-alert-${alert.key}-${suffix}', 64)
  location: location
  tags: tags
  properties: {
    displayName: alert.displayName
    description: alert.description
    severity: alert.severity
    enabled: true
    evaluationFrequency: alert.window
    windowSize: alert.window
    scopes: [
      logAnalyticsId
    ]
    criteria: {
      allOf: [
        {
          query: alert.query
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: alert.threshold
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}]

// Flexible Server active connections above what the deployment expects at
// peak; main.bicep derives the threshold from the pool and replica settings.
resource dbConnections 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: take('${namePrefix}-alert-db-connections-${suffix}', 64)
  location: 'global'
  tags: tags
  properties: {
    description: 'PostgreSQL active connections > ${dbConnectionsThreshold}. Metric: Microsoft.DBforPostgreSQL/flexibleServers active_connections, average over 5 minutes.'
    severity: 2
    enabled: true
    scopes: [
      postgresId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'active-connections'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
          metricName: 'active_connections'
          operator: 'GreaterThan'
          threshold: dbConnectionsThreshold
          timeAggregation: 'Average'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}
