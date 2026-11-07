"""Quota and rate limit enforcement service."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import queries
from app.util.json import iso


@dataclass(frozen=True)
class ResolvedPolicy:
    queries_per_minute: int | None
    queries_per_day: int | None
    tokens_per_day: int | None
    spend_usd_per_day: float | None
    max_active_jobs: int | None


DEFAULT_POLICY = ResolvedPolicy(
    queries_per_minute=None,
    queries_per_day=None,
    tokens_per_day=None,
    spend_usd_per_day=None,
    max_active_jobs=None,
)


def _positive_or_unlimited(value: float | None):
    if value is None or value == 0:
        return None
    return value


def _daily_limit_detail(reset_label: str, *, bucket_type: str = "queries",
                        day_limit: float | None = None) -> str:
    if bucket_type == "queries" or day_limit is None:
        return f"You've reached today's limit. It resets at {reset_label}."
    label = {"tokens": "tokens", "cost_usd": "USD"}[bucket_type]
    return (
        f"You've reached today's limit (maximum {day_limit:g} {label} per day). "
        f"It resets at {reset_label}."
    )


class QuotaService:

    @staticmethod
    def default_policy(settings: Any = None) -> ResolvedPolicy:
        """Settings-backed default policy; 0 means unlimited (None)."""
        if settings is None:
            from app.config import load_settings

            settings = load_settings()
        return ResolvedPolicy(
            queries_per_minute=_positive_or_unlimited(
                getattr(settings, "quota_queries_per_minute", 0)
            ),
            queries_per_day=_positive_or_unlimited(
                getattr(settings, "quota_queries_per_day", 0)
            ),
            tokens_per_day=_positive_or_unlimited(
                getattr(settings, "quota_tokens_per_day", 0)
            ),
            spend_usd_per_day=_positive_or_unlimited(
                getattr(settings, "quota_spend_usd_per_day", 0.0)
            ),
            max_active_jobs=_positive_or_unlimited(
                getattr(settings, "quota_max_active_jobs", 0)
            ),
        )

    @staticmethod
    def resolve_policy(
        conn,
        *,
        workspace_key: str,
        user_id: UUID | None = None,
        settings: Any = None,
    ) -> ResolvedPolicy:
        """Resolve policy limits with precedence: User > Workspace > Global > Settings."""
        default = QuotaService.default_policy(settings)
        if user_id is None:
            user = conn.execute(queries.get("quota_workspace_user"),
                                {"workspace_key": workspace_key}).fetchone()
            user_id = user["user_id"] if user else None
        # 1. User policy
        if user_id:
            row = conn.execute(
                queries.get("get_quota_policy"),
                {"scope_type": "user", "scope_id": str(user_id)},
            ).fetchone()
            if row:
                return QuotaService._row_to_policy(row, default)

        # 2. Workspace policy
        ws_row = conn.execute(
            queries.get("get_quota_policy"),
            {"scope_type": "workspace", "scope_id": workspace_key},
        ).fetchone()
        if ws_row:
            return QuotaService._row_to_policy(ws_row, default)

        # 3. Global policy
        global_row = conn.execute(
            queries.get("get_quota_policy"),
            {"scope_type": "global", "scope_id": "default"},
        ).fetchone()
        if global_row:
            return QuotaService._row_to_policy(global_row, default)

        return default

    @staticmethod
    def _kept(value, default):
        """A stored zero is a real limit. Only NULL falls back to the default."""
        return default if value is None else value

    @staticmethod
    def _row_to_policy(row, default: ResolvedPolicy = DEFAULT_POLICY) -> ResolvedPolicy:
        return ResolvedPolicy(
            queries_per_minute=QuotaService._kept(
                row["queries_per_minute"], default.queries_per_minute
            ),
            queries_per_day=QuotaService._kept(
                row["queries_per_day"], default.queries_per_day
            ),
            tokens_per_day=QuotaService._kept(
                row["tokens_per_day"], default.tokens_per_day
            ),
            spend_usd_per_day=QuotaService._kept(
                row["spend_usd_per_day"], default.spend_usd_per_day
            ),
            max_active_jobs=QuotaService._kept(
                row["max_active_jobs"], default.max_active_jobs
            ),
        )

    @staticmethod
    def check_and_consume(
        conn,
        *,
        workspace_key: str,
        user_id: UUID | None = None,
        bucket_type: str = "queries",
        delta_count: int = 1,
        delta_amount: float = 0.0,
        check_concurrency: bool = False,
        commit: bool = True,
        settings: Any = None,
    ) -> None:
        """Check quota and rate limits; raise 429 Too Many Requests if exceeded, else consume."""
        now = datetime.now(UTC)
        minute_start = now.replace(second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        policy = QuotaService.resolve_policy(
            conn, workspace_key=workspace_key, user_id=user_id, settings=settings
        )
        QuotaService._consume_window(
            conn,
            workspace_key=workspace_key,
            policy=policy,
            bucket_type=bucket_type,
            delta_count=delta_count,
            delta_amount=delta_amount,
            check_concurrency=check_concurrency,
            now=now,
            minute_start=minute_start,
            day_start=day_start,
        )
        if commit:
            conn.commit()

    @staticmethod
    def settle_generation(
        conn,
        *,
        workspace_key: str,
        request_id: UUID,
        user_id: UUID | None = None,
        bucket_type: str = "queries",
        delta_count: int = 1,
        delta_amount: float = 0.0,
        policy: ResolvedPolicy | None = None,
        commit: bool = True,
        enforce: bool = True,
        settings: Any = None,
    ) -> dict[str, Any]:
        """Charge a generation request once. The same request id does not charge again.

        Pass ``commit=False`` to keep the settlement in the caller's transaction.
        A rolled-back admission then leaves the bucket unchanged. An admitted job
        is not refunded when its provider outcome is unknown.
        """
        now = datetime.now(UTC)
        minute_start = now.replace(second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if policy is None:
            policy = QuotaService.resolve_policy(
                conn, workspace_key=workspace_key, user_id=user_id, settings=settings
            )
        with conn.transaction():
            inserted = conn.execute(
                queries.get("insert_quota_settlement"),
                {
                    "workspace_key": workspace_key,
                    "request_id": request_id,
                    "bucket_type": bucket_type,
                    "delta_count": delta_count,
                    "delta_amount": delta_amount,
                },
            ).fetchone()
            if inserted is None:
                result = {"charged": False, "requestId": str(request_id)}
            else:
                QuotaService._consume_window(
                    conn,
                    workspace_key=workspace_key,
                    policy=policy,
                    bucket_type=bucket_type,
                    delta_count=delta_count,
                    delta_amount=delta_amount,
                    check_concurrency=False,
                    enforce=enforce,
                    now=now,
                    minute_start=minute_start,
                    day_start=day_start,
                )
                result = {"charged": True, "requestId": str(request_id)}
        if commit:
            conn.commit()
        return result

    @staticmethod
    def _consume_window(
        conn,
        *,
        workspace_key: str,
        policy: ResolvedPolicy,
        bucket_type: str,
        delta_count: int,
        delta_amount: float,
        check_concurrency: bool,
        now: datetime,
        minute_start: datetime,
        day_start: datetime,
        enforce: bool = True,
    ) -> None:
        # Serialize the read/check/increment sequence even when neither bucket
        # exists yet. Atomic increments alone do not make limit checks atomic.
        conn.execute(queries.get("lock_workspace_quota"), {"workspace_key": workspace_key})
        # Concurrency check
        if check_concurrency and policy.max_active_jobs is not None:
            active_jobs_row = conn.execute(
                queries.get("count_active_workspace_jobs"),
                {"workspace_key": workspace_key},
            ).fetchone()
            active_jobs = active_jobs_row["active_jobs"] if active_jobs_row else 0
            if active_jobs >= policy.max_active_jobs:
                raise HTTPException(
                    status_code=429,
                    detail=f"Concurrent job capacity limit reached ({policy.max_active_jobs} active). Please wait for ongoing jobs to complete.",
                    headers={"Retry-After": "5"},
                )

        # The minute limit is a query-rate limit. Tokens and spend are daily
        # quantities; comparing them against queries_per_minute would make a
        # 50,000-token settlement look like 50,000 queries.
        minute_limit = policy.queries_per_minute if bucket_type == "queries" else None
        if bucket_type == "queries":
            day_limit, day_measure = policy.queries_per_day, "count"
        elif bucket_type == "tokens":
            day_limit, day_measure = policy.tokens_per_day, "count"
        elif bucket_type == "cost_usd":
            day_limit, day_measure = policy.spend_usd_per_day, "amount"
        else:
            raise HTTPException(400, f"unsupported quota bucket: {bucket_type}")

        if minute_limit is not None and delta_count > 0:
            minute_row = conn.execute(
                queries.get("get_quota_bucket"),
                {
                    "scope_type": "workspace",
                    "scope_id": workspace_key,
                    "bucket_type": bucket_type,
                    "window_unit": "minute",
                    "window_start": minute_start,
                },
            ).fetchone()
            minute_count = minute_row["count"] if minute_row else 0
            if enforce and minute_count + delta_count > minute_limit:
                retry_after = max(1, 60 - now.second)
                reset_at = minute_start + timedelta(minutes=1)
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: maximum {minute_limit} queries per minute.",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-Quota-Reset-At": iso(reset_at),
                    },
                )

        seconds_in_day = now.hour * 3600 + now.minute * 60 + now.second
        day_retry_after = max(1, 86400 - seconds_in_day)
        day_reset_at = day_start + timedelta(days=1)
        day_reset_label = f"{day_reset_at:%H:%M} UTC"
        day_headers = {
            "Retry-After": str(day_retry_after),
            "X-Quota-Reset-At": iso(day_reset_at),
        }

        # Quota: Day check
        delta = delta_amount if day_measure == "amount" else delta_count
        if day_limit is not None and (delta > 0 or day_limit == 0):
            day_row = conn.execute(
                queries.get("get_quota_bucket"),
                {
                    "scope_type": "workspace",
                    "scope_id": workspace_key,
                    "bucket_type": bucket_type,
                    "window_unit": "day",
                    "window_start": day_start,
                },
            ).fetchone()
            current = day_row[day_measure] if day_row else 0
            if enforce and (current + delta > day_limit or (day_limit == 0 and current >= 0)):
                raise HTTPException(
                    status_code=429,
                    detail=_daily_limit_detail(
                        day_reset_label, bucket_type=bucket_type, day_limit=day_limit
                    ),
                    headers=day_headers,
                )

        if enforce and bucket_type == "queries" and delta_count > 0:
            for extra_bucket, extra_limit, extra_measure in (
                ("tokens", policy.tokens_per_day, "count"),
                ("cost_usd", policy.spend_usd_per_day, "amount"),
            ):
                if extra_limit is None:
                    continue
                extra_row = conn.execute(
                    queries.get("get_quota_bucket"),
                    {
                        "scope_type": "workspace",
                        "scope_id": workspace_key,
                        "bucket_type": extra_bucket,
                        "window_unit": "day",
                        "window_start": day_start,
                    },
                ).fetchone()
                used = extra_row[extra_measure] if extra_row else 0
                if used >= extra_limit:
                    raise HTTPException(
                        status_code=429,
                        detail=_daily_limit_detail(day_reset_label),
                        headers=day_headers,
                    )

        # Atomic consume
        if delta_count > 0 or delta_amount > 0:
            conn.execute(
                queries.get("increment_quota_bucket"),
                {
                    "scope_type": "workspace",
                    "scope_id": workspace_key,
                    "bucket_type": bucket_type,
                    "window_unit": "minute",
                    "window_start": minute_start,
                    "delta_count": delta_count,
                    "delta_amount": delta_amount,
                },
            )
            conn.execute(
                queries.get("increment_quota_bucket"),
                {
                    "scope_type": "workspace",
                    "scope_id": workspace_key,
                    "bucket_type": bucket_type,
                    "window_unit": "day",
                    "window_start": day_start,
                    "delta_count": delta_count,
                    "delta_amount": delta_amount,
                },
            )

    @staticmethod
    def prune_expired(conn, *, days: int = 2) -> None:
        """Drop buckets whose windows can no longer affect a limit."""
        cutoff = datetime.now(UTC) - timedelta(days=max(1, days))
        conn.execute(queries.get("prune_expired_quota_buckets"), {"cutoff": cutoff})
        conn.commit()

    @staticmethod
    def upsert_policy(
        conn,
        scope_type: str,
        scope_id: str,
        *,
        queries_per_minute: int | None = None,
        queries_per_day: int | None = None,
        tokens_per_day: int | None = None,
        spend_usd_per_day: float | None = None,
        max_active_jobs: int | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        if scope_type not in ("global", "cohort", "workspace", "user"):
            raise HTTPException(422, f"invalid scope_type: {scope_type}")
        scope_id = scope_id.strip()
        if not scope_id or (scope_type == "global" and scope_id != "default"):
            raise HTTPException(422, "global scope must be default; other scopes must not be blank")
        for value in (queries_per_minute, queries_per_day, tokens_per_day, max_active_jobs):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or not 0 <= value <= 2_147_483_647):
                raise HTTPException(422, "quota counts must be nonnegative integers")
        if spend_usd_per_day is not None and (
                not isfinite(spend_usd_per_day) or spend_usd_per_day < 0):
            raise HTTPException(422, "spend limit must be finite and nonnegative")

        policy_id = uuid4()
        row = conn.execute(
            queries.get("upsert_quota_policy"),
            {
                "policy_id": policy_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "queries_per_minute": queries_per_minute,
                "queries_per_day": queries_per_day,
                "tokens_per_day": tokens_per_day,
                "spend_usd_per_day": spend_usd_per_day,
                "max_active_jobs": max_active_jobs,
            },
        ).fetchone()
        if commit:
            conn.commit()
        d = dict(row)
        d["policyId"] = str(d["policy_id"])
        d["scopeType"] = d["scope_type"]
        d["scopeId"] = d["scope_id"]
        d["queriesPerMinute"] = d["queries_per_minute"]
        d["queriesPerDay"] = d["queries_per_day"]
        d["tokensPerDay"] = d["tokens_per_day"]
        d["spendUsdPerDay"] = d["spend_usd_per_day"]
        d["maxActiveJobs"] = d["max_active_jobs"]
        d["createdAt"] = iso(d["created_at"])
        d["updatedAt"] = iso(d["updated_at"])
        return d

