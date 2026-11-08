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

var alertNames = {
  api5xx: take('${namePrefix}-alert-api-5xx-${suffix}', 64)
  queueDepth: take('${namePrefix}-alert-queue-depth-${suffix}', 64)
  oldestJob: take('${namePrefix}-alert-oldest-job-${suffix}', 64)
  provider429: take('${namePrefix}-alert-provider-429-${suffix}', 64)
  dbConnections: take('${namePrefix}-alert-db-connections-${suffix}', 64)
  storage: take('${namePrefix}-alert-storage-${suffix}', 64)
}

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

// Percentage of AppRequests with a 5xx result code in the last five minutes.
resource api5xx 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertNames.api5xx
  location: location
  tags: tags
  properties: {
    displayName: 'API 5xx rate > 5% over 5 minutes'
    description: 'Query: AppRequests | summarize 100.0 * countif(toint(ResultCode) >= 500) / count(). Threshold: 5 percent.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      logAnalyticsId
    ]
    criteria: {
      allOf: [
        {
          query: 'AppRequests | where TimeGenerated > ago(5m) | summarize Total=count(), Failures=countif(toint(ResultCode) >= 500) | extend Rate=100.0*Failures/max(Total,1) | project Rate'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 5
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
}

// Queued jobs across the worker queue; the metric is advisor_jobs{status="queued"}.
resource queueDepth 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertNames.queueDepth
  location: location
  tags: tags
  properties: {
    displayName: 'Worker queue depth > 50 over 5 minutes'
    description: 'Query: AppMetrics | where Name == "advisor_jobs" and Properties.status == "queued" | summarize avg(Sum). Threshold: 50 queued jobs.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      logAnalyticsId
    ]
    criteria: {
      allOf: [
        {
          query: 'AppMetrics | where TimeGenerated > ago(5m) | where Name == "advisor_jobs" and tostring(Properties.status) == "queued" | summarize Value=avg(Sum) | project Value'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 50
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
}

// Oldest queued job age; the metric is advisor_job_oldest_queued_seconds.
resource oldestJob 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertNames.oldestJob
  location: location
  tags: tags
  properties: {
    displayName: 'Oldest queued job > 60 seconds over 5 minutes'
    description: 'Query: AppMetrics | where Name == "advisor_job_oldest_queued_seconds" | summarize avg(Sum). Threshold: 60 seconds.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      logAnalyticsId
    ]
    criteria: {
      allOf: [
        {
          query: 'AppMetrics | where TimeGenerated > ago(5m) | where Name == "advisor_job_oldest_queued_seconds" | summarize Value=avg(Sum) | project Value'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 60
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
}

// Provider 429s recorded by model_calls and exported as a counter metric.
resource provider429 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertNames.provider429
  location: location
  tags: tags
  properties: {
    displayName: 'Provider 429 rate > 10 per 5 minutes'
    description: 'Query: AppMetrics | where Name == "advisor_provider_rate_limited_total" | summarize sum(Sum). Threshold: 10 rate-limited calls in five minutes.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      logAnalyticsId
    ]
    criteria: {
      allOf: [
        {
          query: 'AppMetrics | where TimeGenerated > ago(5m) | where Name == "advisor_provider_rate_limited_total" | summarize Value=sum(Sum) | project Value'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 10
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
}

// Flexible Server active connections against a conservative 80-connection
// ceiling. The server's real max_connections is checked by the app preflight.
resource dbConnections 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: alertNames.dbConnections
  location: 'global'
  tags: tags
  properties: {
    description: 'PostgreSQL active connections > 80. Metric: Microsoft.DBforPostgreSQL/flexibleServers active_connections, average over 5 minutes. Threshold: 80 connections.'
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
          threshold: 80
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

// A workspace at 90% of WORKSPACE_STORAGE_MAX_BYTES (default 100 MiB).
resource storage 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertNames.storage
  location: location
  tags: tags
  properties: {
    displayName: 'Workspace storage > 90% of quota'
    description: 'Query: AppMetrics | where Name == "advisor_workspace_storage_bytes" | summarize max(Sum). Threshold: 90% of the 100 MiB workspace quota.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    scopes: [
      logAnalyticsId
    ]
    criteria: {
      allOf: [
        {
          query: 'AppMetrics | where TimeGenerated > ago(15m) | where Name == "advisor_workspace_storage_bytes" | summarize Value=max(Sum) | project Value'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: storageQuotaBytes * 9 / 10
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
}
