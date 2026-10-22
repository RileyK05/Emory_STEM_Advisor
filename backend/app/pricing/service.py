"""Model price schedule tracking, cache accounting, and usage cost telemetry."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import queries

# model_calls columns a caller may set; anything left out is stored as NULL.
_MODEL_CALL_FIELDS = (
    "trace_id", "decision_id", "claim_review_id", "status", "finish_reason", "request_id",
    "latency_ms", "error_type", "operation_id", "sampling_policy", "reasoning_effort",
    "requested_output_tokens", "requested_timeout_seconds", "requested_temperature",
    "profile_id", "profile_revision",
)


def format_schedule(row: dict) -> dict[str, Any]:
    return {
        "scheduleId": row["schedule_id"],
        "provider": row["provider"],
        "modelId": row["model_id"],
        "currency": row["currency"],
        "billingMode": row["billing_mode"],
        "inputRatePerMillion": float(row["input_rate_per_million"]),
        "cachedReadRatePerMillion": float(row["cached_read_rate_per_million"]),
        "outputRatePerMillion": float(row["output_rate_per_million"]),
        "effectiveFrom": (
            row["effective_from"].isoformat()
            if hasattr(row["effective_from"], "isoformat")
            else str(row["effective_from"])
        ),
        "effectiveTo": (
            row["effective_to"].isoformat()
            if row.get("effective_to") and hasattr(row["effective_to"], "isoformat")
            else (str(row["effective_to"]) if row.get("effective_to") else None)
        ),
        "provenance": row["provenance"],
        "createdAt": (
            row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"])
        ),
    }


class PricingService:
    @staticmethod
    def create_schedule(
        conn,
        *,
        provider: str,
        model_id: str,
        currency: str = "USD",
        billing_mode: str = "standard",
        input_rate_per_million: Decimal | float,
        cached_read_rate_per_million: Decimal | float = 0,
        output_rate_per_million: Decimal | float,
        effective_from: datetime,
        effective_to: datetime | None = None,
        provenance: str,
    ) -> dict:
        if (effective_from.tzinfo is None or effective_from.utcoffset() is None
                or (effective_to is not None and (
                    effective_to.tzinfo is None or effective_to.utcoffset() is None))):
            raise HTTPException(422, "price schedule dates must include a timezone")
        if effective_to and effective_to <= effective_from:
            raise HTTPException(422, "effective_to must be after effective_from")
        rates = [Decimal(str(value)) for value in (
            input_rate_per_million, cached_read_rate_per_million, output_rate_per_million)]
        if any(not rate.is_finite() or rate < 0 or rate >= 1_000_000 for rate in rates):
            raise HTTPException(422, "rates must be finite, non-negative and below one million")
        currency = currency.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise HTTPException(422, "currency must be a three-letter code")
        if not provider.strip() or not model_id.strip() or not provenance.strip():
            raise HTTPException(422, "provider, model and price provenance are required")
        if billing_mode not in {"standard", "batch"}:
            raise HTTPException(422, "unsupported billing mode")

        schedule_id = uuid4()
        row = conn.execute(
            queries.get("insert_price_schedule"),
            {
                "schedule_id": schedule_id,
                "provider": provider.strip(),
                "model_id": model_id.strip(),
                "currency": currency,
                "billing_mode": billing_mode,
                "input_rate_per_million": rates[0],
                "cached_read_rate_per_million": rates[1],
                "output_rate_per_million": rates[2],
                "effective_from": effective_from,
                "effective_to": effective_to,
                "provenance": provenance.strip(),
            },
        ).fetchone()
        conn.commit()
        return format_schedule(dict(row))

    @staticmethod
    def list_schedules(conn) -> list[dict]:
        rows = conn.execute(queries.get("list_price_schedules")).fetchall()
        conn.commit()
        return [format_schedule(dict(row)) for row in rows]

    @staticmethod
    def record_model_call(conn, *, provider: str, model_id: str | None, usage,
                          created_at: datetime, billing_mode: str = "standard",
                          pricing_usage_source: str | None = None, **fields) -> None:
        """Price one provider call and append its model_calls row (caller's transaction).

        `pricing_usage_source` overrides the usage source used for pricing only, e.g.
        a native batch with no recorded pricing time is priced as unknown.
        """
        unknown = set(fields) - set(_MODEL_CALL_FIELDS)
        if unknown:
            raise TypeError(f"unknown model_calls fields: {sorted(unknown)}")
        schedule_id, cost, cost_status = PricingService.compute_call_cost(
            conn, provider=provider, model_id=model_id, created_at=created_at,
            prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
            cached_prompt_tokens=usage.cached_prompt_tokens,
            usage_source=pricing_usage_source or usage.source, billing_mode=billing_mode,
        )
        conn.execute(queries.get("insert_model_call"), {
            **dict.fromkeys(_MODEL_CALL_FIELDS), **fields,
            "model_call_id": uuid4(), "provider": provider, "model_id": model_id,
            "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "usage_source": usage.source, "cached_prompt_tokens": usage.cached_prompt_tokens,
            "schedule_id": schedule_id, "estimated_cost": cost, "cost_status": cost_status,
        })

    @staticmethod
    def compute_call_cost(
        conn,
        *,
        provider: str,
        model_id: str | None,
        created_at: datetime,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cached_prompt_tokens: int | None,
        usage_source: str,
        billing_mode: str = "standard",
    ) -> tuple[UUID | None, Decimal | None, str]:
        """Compute estimated cost for a model call.

        Prompt tokens already include cached tokens per provider contracts; uncached
        tokens are prompt_tokens - cached_prompt_tokens to avoid double-charging.
        """
        if (usage_source not in {"provider", "estimated"}
                or any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                       for value in (prompt_tokens, completion_tokens))
                or (cached_prompt_tokens is not None and (
                    isinstance(cached_prompt_tokens, bool)
                    or not isinstance(cached_prompt_tokens, int)
                    or not 0 <= cached_prompt_tokens <= prompt_tokens))):
            return None, None, "unknown"

        row = conn.execute(
            queries.get("find_active_price_schedule"),
            {
                "provider": provider,
                "model_id": model_id or "*",
                "timestamp": created_at,
                "billing_mode": billing_mode,
            },
        ).fetchone()
        if row is None:
            return None, None, "unknown"

        cached = cached_prompt_tokens if cached_prompt_tokens is not None else 0
        uncached = prompt_tokens - cached

        input_rate = Decimal(str(row["input_rate_per_million"]))
        cached_rate = Decimal(str(row["cached_read_rate_per_million"]))
        output_rate = Decimal(str(row["output_rate_per_million"]))

        cost = (
            Decimal(uncached) * input_rate
            + Decimal(cached) * cached_rate
            + Decimal(completion_tokens) * output_rate
        ) / Decimal(1000000)

        cost_status = "measured" if usage_source == "provider" else "estimated"
        if cached_prompt_tokens is None and input_rate != cached_rate:
            cost_status = "estimated"
        return row["schedule_id"], cost, cost_status

    @staticmethod
    def telemetry_summary(
        conn,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        provider: str = "",
        model_id: str = "",
    ) -> dict[str, Any]:
        row = conn.execute(
            queries.get("telemetry_usage_summary"),
            {
                "since": since,
                "until": until,
                "provider": provider.strip(),
                "model_id": model_id.strip(),
            },
        ).fetchone()
        conn.commit()
        return _format_telemetry_row(dict(row))

    @staticmethod
    def telemetry_breakdown(
        conn,
        *,
        group_by: str = "date",
        since: datetime | None = None,
        until: datetime | None = None,
        provider: str = "",
        model_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query_name = {
            "date": "telemetry_usage_by_date",
            "model": "telemetry_usage_by_model",
            "operation": "telemetry_usage_by_operation",
        }.get(group_by, "telemetry_usage_by_date")

        rows = conn.execute(
            queries.get(query_name),
            {
                "since": since,
                "until": until,
                "provider": provider.strip(),
                "model_id": model_id.strip(),
                "limit": max(1, min(limit, 500)),
            },
        ).fetchall()
        conn.commit()

        results = []
        for r in rows:
            data = dict(r)
            bucket_str = str(data.get("bucket", ""))
            formatted = _format_telemetry_row(data)
            formatted["bucket"] = bucket_str
            if "provider" in data:
                formatted["provider"] = data["provider"]
            if "model_id" in data:
                formatted["modelId"] = data["model_id"]
            results.append(formatted)
        return results


def _format_telemetry_row(row: dict) -> dict[str, Any]:
    total_calls = row.get("total_calls") or 0
    completed_calls = row.get("completed_calls") or 0
    failed_calls = row.get("failed_calls") or 0
    total_prompt_tokens = row.get("total_prompt_tokens")
    total_completion_tokens = row.get("total_completion_tokens")
    total_cached_tokens = row.get("total_cached_tokens")
    calls_reporting_cache = row.get("calls_reporting_cache") or 0
    calls_with_cache_hits = row.get("calls_with_cache_hits") or 0

    cached_token_share = None
    observed_prompt_tokens = row.get("cache_observed_prompt_tokens")
    if observed_prompt_tokens and observed_prompt_tokens > 0 and total_cached_tokens is not None:
        cached_token_share = round(float(total_cached_tokens) / float(observed_prompt_tokens), 4)

    cache_hit_fraction = None
    if calls_reporting_cache > 0:
        cache_hit_fraction = round(float(calls_with_cache_hits) / float(calls_reporting_cache), 4)

    cache_reporting_coverage = None
    if total_calls > 0:
        cache_reporting_coverage = round(float(calls_reporting_cache) / float(total_calls), 4)

    total_cost = float(row["total_cost"]) if row.get("total_cost") is not None else None
    measured_cost = (
        float(row["measured_cost"]) if row.get("measured_cost") is not None else None
    )
    estimated_cost = (
        float(row["estimated_cost"]) if row.get("estimated_cost") is not None else None
    )
    unknown_cost_calls = row.get("unknown_cost_calls") or 0
    currencies = sorted(row.get("cost_currencies") or [])
    currency = currencies[0] if len(currencies) == 1 and currencies[0] != "unknown" else None
    if currency is None:
        total_cost = measured_cost = estimated_cost = None
    if total_calls == 0:
        cost_completeness = "complete"
    elif currency is None or (total_cost is None and unknown_cost_calls == total_calls):
        cost_completeness = "unknown"
    elif unknown_cost_calls > 0:
        cost_completeness = "partial"
    else:
        cost_completeness = "complete"

    return {
        "totalCalls": total_calls,
        "completedCalls": completed_calls,
        "failedCalls": failed_calls,
        "totalPromptTokens": total_prompt_tokens,
        "totalCompletionTokens": total_completion_tokens,
        "totalCachedTokens": total_cached_tokens,
        "cachedTokenShare": cached_token_share,
        "callsReportingCache": calls_reporting_cache,
        "callsWithCacheHits": calls_with_cache_hits,
        "cacheHitFraction": cache_hit_fraction,
        "cacheReportingCoverage": cache_reporting_coverage,
        "totalCost": total_cost,
        "measuredCost": measured_cost,
        "estimatedCost": estimated_cost,
        "unknownCostCalls": unknown_cost_calls,
        "costCompleteness": cost_completeness,
        "costCurrency": currency,
        "costCurrencies": currencies,
    }
