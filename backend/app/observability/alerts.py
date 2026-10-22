"""Observability telemetry, release-critical alerts, and runbooks (Item 28)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg

from app.db import queries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertRule:
    name: str
    severity: Literal["critical", "warning", "info"]
    threshold: float
    window_seconds: int
    owner: str
    description: str
    runbook: str
    metric_extractor: Callable[[dict[str, Any]], float | None]
    comparison: Literal["gt", "gte", "lt", "lte"] = "gt"


def _evaluate_rule(rule: AlertRule, metrics_data: dict[str, Any]) -> dict[str, Any]:
    raw = rule.metric_extractor(metrics_data)
    evaluated_at = datetime.now(UTC).isoformat()
    if raw is None:
        return {
            "alertName": rule.name,
            "status": "unknown",
            "severity": rule.severity,
            "currentValue": None,
            "threshold": rule.threshold,
            "owner": rule.owner,
            "runbook": rule.runbook,
            "description": rule.description,
            "evaluatedAt": evaluated_at,
        }
    current_value = float(raw)
    firing = (
        (rule.comparison in ("gt", "gte") and current_value >= rule.threshold)
        or (rule.comparison in ("lt", "lte") and current_value <= rule.threshold)
    )
    return {
        "alertName": rule.name,
        "status": "firing" if firing else "resolved",
        "severity": rule.severity,
        "currentValue": current_value,
        "threshold": rule.threshold,
        "owner": rule.owner,
        "runbook": rule.runbook,
        "description": rule.description,
        "evaluatedAt": evaluated_at,
    }


# Standard Release-Critical Alerts
ALERT_RULES: list[AlertRule] = [
    AlertRule(
        name="HighQueueWaitAge",
        severity="critical",
        threshold=60.0,
        window_seconds=120,
        owner="platform-oncall",
        description="Oldest queued job in the background worker queue exceeds 60 seconds",
        runbook="Check worker container scaling, DB pool availability, and stuck leases in jobs table.",
        metric_extractor=lambda m: m.get("oldest_job_age_seconds"),
    ),
    AlertRule(
        name="DBPoolWaitingSurge",
        severity="warning",
        threshold=5.0,
        window_seconds=60,
        owner="database-oncall",
        description="Database connection pool has 5 or more waiting client requests",
        runbook="Inspect long-running transactions, slow queries, or increase DB_POOL_MAX_SIZE if connection count permits.",
        metric_extractor=lambda m: m.get("pool_waiting_requests"),
    ),
    AlertRule(
        name="ProviderErrorRateHigh",
        severity="critical",
        threshold=10.0,
        window_seconds=300,
        owner="ai-runtime-oncall",
        description="Provider API calls experienced 10 or more failures/timeouts/429s in the current window",
        runbook="Inspect provider status page, check API quotas, verify connection profiles, or failover to alternative provider.",
        metric_extractor=lambda m: m.get("provider_failures_count"),
    ),
    AlertRule(
        name="BudgetBurnRateHigh",
        severity="warning",
        threshold=50.0,
        window_seconds=3600,
        owner="finops-advising",
        description="Current hourly model spend rate exceeds $50/hour",
        runbook="Verify no runaway batch loops or unauthorized high-volume queries; check rate limit enforcement.",
        metric_extractor=lambda m: m.get("hourly_spend_rate_usd"),
    ),
    AlertRule(
        name="StorageFailuresDetected",
        severity="critical",
        threshold=1.0,
        window_seconds=60,
        owner="infra-oncall",
        description="Storage adapters encountered one or more read/write/integrity failures",
        runbook="Inspect Azure Blob connectivity, local disk capacity, and permissions for the storage root.",
        metric_extractor=lambda m: m.get("storage_failures_count"),
    ),
]


class AlertManager:
    @staticmethod
    def _probe(conn, read: Callable[[], float | None]) -> float | None:
        """Read one metric in its own transaction so a failed probe cannot abort the next."""
        try:
            with conn.transaction():
                return read()
        except (psycopg.Error, KeyError, TypeError, ValueError) as exc:
            logger.debug("metric probe failed: %s", exc)
            return None

    @staticmethod
    def collect_live_metrics(conn, app_state: Any = None) -> dict[str, Any]:
        """Aggregate process-local metrics and DB gauges.

        A missing or failed reading stays unknown. It is not reported as a healthy zero.
        """
        metrics: dict[str, Any] = {
            "oldest_job_age_seconds": None,
            "pool_waiting_requests": None,
            "provider_failures_count": None,
            "hourly_spend_rate_usd": None,
            "storage_failures_count": None,
        }

        def oldest() -> float | None:
            row = conn.execute(queries.get("metrics_oldest_queued_seconds")).fetchone()
            if row is None or row["seconds"] is None:
                return None
            return float(row["seconds"])

        metrics["oldest_job_age_seconds"] = AlertManager._probe(conn, oldest)

        try:
            from app.db.connection import pool_stats

            waiting = 0.0
            for stat in pool_stats():
                waiting += float(stat.get("waiting", 0))
            metrics["pool_waiting_requests"] = waiting
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("Failed reading pool_stats: %s", exc)

        def failures() -> float | None:
            row = conn.execute(queries.get("telemetry_recent_failures")).fetchone()
            if row is None or row["failures"] is None:
                return None
            return float(row["failures"])

        metrics["provider_failures_count"] = AlertManager._probe(conn, failures)

        def spend() -> float | None:
            row = conn.execute(queries.get("metrics_hourly_spend_usd")).fetchone()
            if row is None or row["spend"] is None:
                return None
            return float(row["spend"])

        metrics["hourly_spend_rate_usd"] = AlertManager._probe(conn, spend)

        storage = getattr(app_state, "storage", None) if app_state is not None else None
        if storage is not None and hasattr(storage, "failure_count"):
            try:
                metrics["storage_failures_count"] = float(storage.failure_count)
            except (TypeError, ValueError) as exc:
                logger.debug("Failed reading storage failures: %s", exc)
        return metrics

    @staticmethod
    def evaluate_all(conn, app_state: Any = None, overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        metrics = AlertManager.collect_live_metrics(conn, app_state)
        if overrides:
            metrics.update(overrides)

        return [_evaluate_rule(rule, metrics) for rule in ALERT_RULES]
