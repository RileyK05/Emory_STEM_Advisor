"""Tests for observability alerts, runbooks, and controlled fault evaluation (Item 28)."""

from app.observability.alerts import AlertManager
from tests.helpers import api_client


def test_alert_rules_evaluation_normal_state(db):
    results = AlertManager.evaluate_all(
        db,
        overrides={
            "oldest_job_age_seconds": 5.0,
            "pool_waiting_requests": 0.0,
            "provider_failures_count": 0.0,
            "hourly_spend_rate_usd": 2.50,
            "storage_failures_count": 0.0,
        },
    )
    assert len(results) >= 5
    for alert in results:
        assert alert["status"] == "resolved"
        assert alert["runbook"] != ""
        assert alert["owner"] != ""


def test_controlled_faults_trigger_alerts_and_recover(db):
    # 1. Fault: High queue wait age
    fault_queue = AlertManager.evaluate_all(db, overrides={"oldest_job_age_seconds": 120.0})
    queue_alert = next(a for a in fault_queue if a["alertName"] == "HighQueueWaitAge")
    assert queue_alert["status"] == "firing"
    assert queue_alert["severity"] == "critical"
    assert "worker container" in queue_alert["runbook"].lower()

    # 2. Fault: Database pool exhaustion
    fault_pool = AlertManager.evaluate_all(db, overrides={"pool_waiting_requests": 8.0})
    pool_alert = next(a for a in fault_pool if a["alertName"] == "DBPoolWaitingSurge")
    assert pool_alert["status"] == "firing"
    assert pool_alert["severity"] == "warning"

    # 3. Fault: Provider error rate spike
    fault_provider = AlertManager.evaluate_all(db, overrides={"provider_failures_count": 15.0})
    provider_alert = next(a for a in fault_provider if a["alertName"] == "ProviderErrorRateHigh")
    assert provider_alert["status"] == "firing"
    assert provider_alert["severity"] == "critical"

    # 4. Fault: Spend cap / burn rate
    fault_budget = AlertManager.evaluate_all(db, overrides={"hourly_spend_rate_usd": 75.0})
    budget_alert = next(a for a in fault_budget if a["alertName"] == "BudgetBurnRateHigh")
    assert budget_alert["status"] == "firing"

    # 5. Fault: Storage failure spike
    fault_storage = AlertManager.evaluate_all(db, overrides={"storage_failures_count": 2.0})
    storage_alert = next(a for a in fault_storage if a["alertName"] == "StorageFailuresDetected")
    assert storage_alert["status"] == "firing"
    assert storage_alert["severity"] == "critical"

    # 6. Recovery: normal conditions resolve all alerts
    recovered = AlertManager.evaluate_all(
        db,
        overrides={
            "oldest_job_age_seconds": 0.0,
            "pool_waiting_requests": 0.0,
            "provider_failures_count": 0.0,
            "hourly_spend_rate_usd": 5.0,
            "storage_failures_count": 0.0,
        },
    )
    for alert in recovered:
        assert alert["status"] == "resolved"


def test_admin_alerts_api_endpoint(db, collection):
    client = api_client(db, collection)
    res = client.get("/api/admin/alerts")
    assert res.status_code == 200
    alerts = res.json()
    assert len(alerts) >= 5
    names = {a["alertName"] for a in alerts}
    assert "HighQueueWaitAge" in names
    assert "DBPoolWaitingSurge" in names
    assert "ProviderErrorRateHigh" in names
    assert "BudgetBurnRateHigh" in names
    assert "StorageFailuresDetected" in names

    for a in alerts:
        assert a["status"] in ("firing", "resolved", "unknown")
        assert a["severity"] in ("critical", "warning", "info")
        assert len(a["runbook"]) > 0
        if a["status"] == "unknown":
            assert a["currentValue"] is None
        else:
            assert a["currentValue"] is not None


def test_failed_probe_stays_unknown_and_does_not_block_the_next(db, monkeypatch):
    from app.db import queries as query_loader

    original = query_loader.get

    def wrapped(name):
        if name == "metrics_oldest_queued_seconds":
            return "SELECT (1/0)::float AS seconds"
        return original(name)

    monkeypatch.setattr(query_loader, "get", wrapped)
    first = AlertManager.collect_live_metrics(db)
    assert first["oldest_job_age_seconds"] is None
    assert first["provider_failures_count"] == 0.0
    second = AlertManager.collect_live_metrics(db)
    assert second["oldest_job_age_seconds"] is None
    assert second["provider_failures_count"] == 0.0
    alerts = AlertManager.evaluate_all(db)
    queue = next(item for item in alerts if item["alertName"] == "HighQueueWaitAge")
    provider = next(item for item in alerts if item["alertName"] == "ProviderErrorRateHigh")
    assert queue["status"] == "unknown"
    assert queue["currentValue"] is None
    assert provider["status"] == "resolved"
    assert provider["currentValue"] == 0.0

