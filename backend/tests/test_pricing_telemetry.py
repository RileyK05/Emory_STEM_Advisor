"""Unit and integration tests for pricing schedules, cache accounting, and cost telemetry."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db import queries
from app.pricing import PricingService
from tests.helpers import api_client


def test_pricing_schedule_validation_and_lookup(db):
    now = datetime.now(UTC)

    # 1. Negative rates rejected
    with pytest.raises(HTTPException) as exc:
        PricingService.create_schedule(
            db,
            provider="openai",
            model_id="gpt-4o",
            input_rate_per_million=-1.0,
            output_rate_per_million=10.0,
            effective_from=now,
            provenance="test",
        )
    assert exc.value.status_code == 422

    for rate in (float("nan"), float("inf"), 1_000_000):
        with pytest.raises(HTTPException) as invalid:
            PricingService.create_schedule(
                db, provider="test", model_id="test", input_rate_per_million=rate,
                output_rate_per_million=1, effective_from=now, provenance="test")
        assert invalid.value.status_code == 422

    # 2. Inverted dates rejected
    with pytest.raises(HTTPException) as exc:
        PricingService.create_schedule(
            db,
            provider="openai",
            model_id="gpt-4o",
            input_rate_per_million=2.5,
            output_rate_per_million=10.0,
            effective_from=now,
            effective_to=now - timedelta(days=1),
            provenance="test",
        )
    assert exc.value.status_code == 422

    # 3. Create wildcard fallback schedule
    wildcard = PricingService.create_schedule(
        db,
        provider="openai",
        model_id="*",
        input_rate_per_million=5.0,
        cached_read_rate_per_million=2.5,
        output_rate_per_million=15.0,
        effective_from=now - timedelta(days=10),
        provenance="test-wildcard",
    )
    assert wildcard["scheduleId"] is not None

    # 4. Lookup before specific schedule exists returns wildcard fallback
    sched_id, cost, status = PricingService.compute_call_cost(
        db,
        provider="openai",
        model_id="gpt-4o-mini",
        created_at=now,
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        cached_prompt_tokens=0,
        usage_source="provider",
    )
    assert sched_id == wildcard["scheduleId"]
    assert cost == Decimal("20.0")  # 5 + 15
    assert status == "measured"

    # 5. Create specific schedule with newer priority
    specific = PricingService.create_schedule(
        db,
        provider="openai",
        model_id="gpt-4o-mini",
        input_rate_per_million=0.15,
        cached_read_rate_per_million=0.075,
        output_rate_per_million=0.60,
        effective_from=now - timedelta(days=5),
        provenance="test-specific",
    )

    sched_id2, cost2, status2 = PricingService.compute_call_cost(
        db,
        provider="openai",
        model_id="gpt-4o-mini",
        created_at=now,
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        cached_prompt_tokens=0,
        usage_source="provider",
    )
    assert sched_id2 == specific["scheduleId"]
    assert cost2 == Decimal("0.75")  # 0.15 + 0.60
    assert status2 == "measured"


def test_compute_call_cost_uncached_vs_cached_accounting(db):
    now = datetime.now(UTC)
    PricingService.create_schedule(
        db,
        provider="anthropic",
        model_id="claude-3-5-sonnet",
        input_rate_per_million=3.0,
        cached_read_rate_per_million=0.30,
        output_rate_per_million=15.0,
        effective_from=now - timedelta(days=1),
        provenance="test-sonnet",
    )

    # 1. Cached prompt tokens are subtracted from input tokens so not double-counted:
    # prompt_tokens = 10,000; cached = 4,000 -> uncached = 6,000
    # uncached cost: 6,000 * 3.0 / 1M = 0.018
    # cached cost: 4,000 * 0.30 / 1M = 0.0012
    # completion cost: 1,000 * 15.0 / 1M = 0.015
    # total = 0.0342
    sched_id, cost, status = PricingService.compute_call_cost(
        db,
        provider="anthropic",
        model_id="claude-3-5-sonnet",
        created_at=now,
        prompt_tokens=10_000,
        completion_tokens=1_000,
        cached_prompt_tokens=4_000,
        usage_source="provider",
    )
    assert sched_id is not None
    assert cost == Decimal("0.0342")
    assert status == "measured"

    # 2. Estimated usage source gives 'estimated' status
    _, cost_est, status_est = PricingService.compute_call_cost(
        db,
        provider="anthropic",
        model_id="claude-3-5-sonnet",
        created_at=now,
        prompt_tokens=10_000,
        completion_tokens=1_000,
        cached_prompt_tokens=4_000,
        usage_source="estimated",
    )
    assert cost_est == Decimal("0.0342")
    assert status_est == "estimated"

    # 3. Unknown usage source yields None cost and 'unknown' status (never zero)
    sched_unkn, cost_unkn, status_unkn = PricingService.compute_call_cost(
        db,
        provider="anthropic",
        model_id="claude-3-5-sonnet",
        created_at=now,
        prompt_tokens=None,
        completion_tokens=None,
        cached_prompt_tokens=None,
        usage_source="unknown",
    )
    assert sched_unkn is None
    assert cost_unkn is None
    assert status_unkn == "unknown"

    # 4. Missing schedule for provider/model yields None cost and 'unknown' status
    sched_none, cost_none, status_none = PricingService.compute_call_cost(
        db,
        provider="unknown-provider",
        model_id="unknown-model",
        created_at=now,
        prompt_tokens=1000,
        completion_tokens=500,
        cached_prompt_tokens=0,
        usage_source="provider",
    )
    assert sched_none is None
    assert cost_none is None
    assert status_none == "unknown"

    # Unreported cache counts are a conservative rate estimate, never measured cache use.
    for prompt, completion, cached, expected in [
        (10_000, 1_000, None, (Decimal("0.045"), "estimated")),
        (10_000, 1_000, 0, (Decimal("0.045"), "measured")),
        (0, 0, 0, (Decimal(0), "measured")),
        (10, 1, 11, (None, "unknown")),
        (-1, 1, 0, (None, "unknown")),
        (True, 1, 0, (None, "unknown")),
    ]:
        _, amount, measured = PricingService.compute_call_cost(
            db, provider="anthropic", model_id="claude-3-5-sonnet", created_at=now,
            prompt_tokens=prompt, completion_tokens=completion,
            cached_prompt_tokens=cached, usage_source="provider")
        assert (amount, measured) == expected

    # A standard tariff must not silently price native batches.
    _, amount, measured = PricingService.compute_call_cost(
        db, provider="anthropic", model_id="claude-3-5-sonnet", created_at=now,
        prompt_tokens=10_000, completion_tokens=1_000, cached_prompt_tokens=0,
        usage_source="provider", billing_mode="batch")
    assert (amount, measured) == (None, "unknown")
    batch = PricingService.create_schedule(
        db, provider="anthropic", model_id="claude-3-5-sonnet", billing_mode="batch",
        input_rate_per_million=1, cached_read_rate_per_million=1,
        output_rate_per_million=2, effective_from=now - timedelta(days=1),
        effective_to=now + timedelta(days=1), provenance="reviewed batch tariff")
    schedule, amount, measured = PricingService.compute_call_cost(
        db, provider="anthropic", model_id="claude-3-5-sonnet", created_at=now,
        prompt_tokens=10_000, completion_tokens=1_000, cached_prompt_tokens=None,
        usage_source="provider", billing_mode="batch")
    assert (schedule, amount, measured) == (batch["scheduleId"], Decimal("0.012"), "measured")
    _, amount, _ = PricingService.compute_call_cost(
        db, provider="anthropic", model_id="claude-3-5-sonnet",
        created_at=now + timedelta(days=1), prompt_tokens=10_000,
        completion_tokens=1_000, cached_prompt_tokens=0,
        usage_source="provider", billing_mode="batch")
    assert amount is None  # end timestamps are exclusive


def test_telemetry_metrics_and_breakdowns(db):
    now = datetime.now(UTC)
    sched = PricingService.create_schedule(
        db,
        provider="openai",
        model_id="gpt-4o",
        input_rate_per_million=2.50,
        cached_read_rate_per_million=1.25,
        output_rate_per_million=10.00,
        effective_from=now - timedelta(days=2),
        provenance="test",
    )

    # Insert 3 test model calls:
    # Call 1: provider measured, cache hit
    call_id_1 = uuid4()
    db.execute(queries.get("insert_model_call"), {
        "model_call_id": call_id_1, "trace_id": None, "provider": "openai",
        "model_id": "gpt-4o", "status": "completed", "finish_reason": "stop",
        "request_id": "req-1", "prompt_tokens": 1000, "completion_tokens": 200,
        "usage_source": "provider", "latency_ms": 150, "error_type": None,
        "operation_id": None, "sampling_policy": "greedy", "reasoning_effort": None,
        "requested_output_tokens": 500, "requested_timeout_seconds": 30.0,
        "requested_temperature": None, "profile_id": None, "profile_revision": None,
        "cached_prompt_tokens": 400, "schedule_id": sched["scheduleId"],
        "estimated_cost": Decimal("0.004"), "cost_status": "measured",
    })

    # Call 2: estimated, reporting cache = 0 (no hit)
    call_id_2 = uuid4()
    db.execute(queries.get("insert_model_call"), {
        "model_call_id": call_id_2, "trace_id": None, "provider": "openai",
        "model_id": "gpt-4o", "status": "completed", "finish_reason": "stop",
        "request_id": "req-2", "prompt_tokens": 2000, "completion_tokens": 300,
        "usage_source": "estimated", "latency_ms": 200, "error_type": None,
        "operation_id": None, "sampling_policy": "greedy", "reasoning_effort": None,
        "requested_output_tokens": 500, "requested_timeout_seconds": 30.0,
        "requested_temperature": None, "profile_id": None, "profile_revision": None,
        "cached_prompt_tokens": 0, "schedule_id": sched["scheduleId"],
        "estimated_cost": Decimal("0.008"), "cost_status": "estimated",
    })

    # Call 3: failed, unknown usage, no cached tokens reported
    call_id_3 = uuid4()
    db.execute(queries.get("insert_model_call"), {
        "model_call_id": call_id_3, "trace_id": None, "provider": "openai",
        "model_id": "gpt-4o", "status": "failed", "finish_reason": None,
        "request_id": "req-3", "prompt_tokens": None, "completion_tokens": None,
        "usage_source": "unknown", "latency_ms": 50, "error_type": "ConnectionError",
        "operation_id": None, "sampling_policy": "greedy", "reasoning_effort": None,
        "requested_output_tokens": None, "requested_timeout_seconds": 30.0,
        "requested_temperature": None, "profile_id": None, "profile_revision": None,
        "cached_prompt_tokens": None, "schedule_id": None,
        "estimated_cost": None, "cost_status": "unknown",
    })
    db.commit()

    # Query summary
    summary = PricingService.telemetry_summary(db, provider="openai")
    assert summary["totalCalls"] == 3
    assert summary["completedCalls"] == 2
    assert summary["failedCalls"] == 1
    assert summary["totalPromptTokens"] == 3000
    assert summary["totalCompletionTokens"] == 500
    assert summary["totalCachedTokens"] == 400
    # cachedTokenShare: 400 / 3000 = 0.1333
    assert summary["cachedTokenShare"] == 0.1333
    assert summary["callsReportingCache"] == 2
    assert summary["callsWithCacheHits"] == 1
    # cacheHitFraction: 1 / 2 = 0.5
    assert summary["cacheHitFraction"] == 0.5
    # cacheReportingCoverage: 2 / 3 = 0.6667
    assert summary["cacheReportingCoverage"] == 0.6667
    assert summary["totalCost"] == 0.012
    assert summary["measuredCost"] == 0.004
    assert summary["estimatedCost"] == 0.008
    assert summary["unknownCostCalls"] == 1
    assert summary["costCompleteness"] == "partial"

    # Query breakdown by model
    by_model = PricingService.telemetry_breakdown(db, group_by="model")
    assert len(by_model) >= 1
    row = next(r for r in by_model if r["modelId"] == "gpt-4o")
    assert row["totalCalls"] == 3
    assert row["completedCalls"] == 2
    assert row["totalCost"] == 0.012
    assert row["costCurrency"] == "USD"

    # Unreported cache use does not dilute the observed cached-token share.
    db.execute("UPDATE model_calls SET prompt_tokens=9000 WHERE model_call_id=%s", (call_id_3,))
    assert PricingService.telemetry_summary(db)["cachedTokenShare"] == 0.1333
    for group in ("date", "model", "operation"):
        assert PricingService.telemetry_breakdown(db, group_by=group, provider="absent") == []

    euro = PricingService.create_schedule(
        db, provider="openai", model_id="gpt-4o", currency="EUR",
        input_rate_per_million=1, output_rate_per_million=1, effective_from=now,
        provenance="test EUR tariff")
    db.execute("UPDATE model_calls SET schedule_id=%s WHERE model_call_id=%s",
               (euro["scheduleId"], call_id_2))
    summary = PricingService.telemetry_summary(db)
    assert summary["costCurrencies"] == ["EUR", "USD"]
    assert summary["costCurrency"] is None
    assert summary["totalCost"] is None
    assert summary["measuredCost"] is None
    assert summary["estimatedCost"] is None
    assert summary["costCompleteness"] == "unknown"


def test_unknown_cost_is_not_reported_as_zero(db):
    db.execute(queries.get("insert_model_call"), {
        "model_call_id": uuid4(), "trace_id": None, "provider": "openai",
        "model_id": "gpt-4o", "status": "failed", "finish_reason": None,
        "request_id": "req-unknown", "prompt_tokens": None, "completion_tokens": None,
        "usage_source": "unknown", "latency_ms": 50, "error_type": "ConnectionError",
        "operation_id": None, "sampling_policy": "greedy", "reasoning_effort": None,
        "requested_output_tokens": None, "requested_timeout_seconds": 30.0,
        "requested_temperature": None, "profile_id": None, "profile_revision": None,
        "cached_prompt_tokens": None, "schedule_id": None,
        "estimated_cost": None, "cost_status": "unknown",
    })
    db.commit()
    summary = PricingService.telemetry_summary(db, provider="openai")
    assert summary["totalCalls"] == 1
    assert summary["unknownCostCalls"] == 1
    assert summary["totalCost"] is None
    assert summary["totalCost"] != 0
    assert summary["costCompleteness"] == "unknown"


def test_admin_pricing_and_telemetry_routes(db, collection):
    client = api_client(db, collection)
    now = datetime.now(UTC)

    # 1. Create schedule via Admin API
    resp = client.post(
        "/api/admin/pricing/schedules",
        json={
            "provider": "google",
            "modelId": "gemini-1.5-pro",
            "currency": "USD",
            "inputRatePerMillion": 3.50,
            "cachedReadRatePerMillion": 0.875,
            "outputRatePerMillion": 10.50,
            "effectiveFrom": (now - timedelta(days=1)).isoformat(),
            "provenance": "admin-test-post",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["provider"] == "google"
    assert created["modelId"] == "gemini-1.5-pro"
    assert created["inputRatePerMillion"] == 3.50

    # 2. List schedules
    list_resp = client.get("/api/admin/pricing/schedules")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert any(s["modelId"] == "gemini-1.5-pro" for s in items)

    # 3. Query telemetry usage
    telemetry_resp = client.get("/api/admin/telemetry/usage?group_by=date")
    assert telemetry_resp.status_code == 200
    body = telemetry_resp.json()
    assert "summary" in body
    assert "breakdown" in body
    assert isinstance(body["summary"]["totalCalls"], int)
    assert isinstance(body["breakdown"], list)

    invalid = {
        "provider": "google", "modelId": "model", "inputRatePerMillion": 1,
        "outputRatePerMillion": 2, "effectiveFrom": "invalid", "provenance": "test",
    }
    for date in ("invalid", "2026-01-01T00:00:00"):
        invalid["effectiveFrom"] = date
        assert client.post("/api/admin/pricing/schedules", json=invalid).status_code == 422

