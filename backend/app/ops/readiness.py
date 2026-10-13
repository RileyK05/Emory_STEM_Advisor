"""Bounded, read-only process readiness checks."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db import migrate, queries
from app.db.connection import connect

_CACHE_SECONDS = 5.0


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        if self.ready:
            return {"status": "ready"}
        return {"status": "unavailable", "reasons": list(self.reasons)}


class ReadinessProbe:
    """Check auth configuration and database/migration integrity without bootstrapping."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 3,
        connect_fn: Callable[[str], object] = connect,
        monotonic: Callable[[], float] = time.monotonic,
        cache_seconds: float = _CACHE_SECONDS,
    ) -> None:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 10:
            raise ValueError("readiness timeout must be between 1 and 10 seconds")
        if not 0 <= cache_seconds <= 30:
            raise ValueError("readiness cache duration is invalid")
        self.timeout_seconds = timeout_seconds
        self.connect_fn = connect_fn
        self.monotonic = monotonic
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._expires_at = 0.0
        self._cached: ReadinessResult | None = None

    def check(self, *, dsn: str | None, auth_service: object | None, auth_mode: str) -> ReadinessResult:
        now = self.monotonic()
        cached = self._cached
        if cached is not None and now < self._expires_at:
            return cached
        with self._lock:
            now = self.monotonic()
            cached = self._cached
            if cached is not None and now < self._expires_at:
                return cached
            result = self._probe(dsn=dsn, auth_service=auth_service, auth_mode=auth_mode)
            self._cached = result
            self._expires_at = self.monotonic() + self.cache_seconds
            return result

    def _probe(self, *, dsn: str | None, auth_service: object | None, auth_mode: str) -> ReadinessResult:
        reasons: list[str] = []
        service_mode = getattr(auth_service, "mode", None)
        if auth_service is None or service_mode != auth_mode or auth_mode not in {"development", "oidc"}:
            reasons.append("auth_not_configured")

        if not dsn:
            reasons.append("database_not_configured")
            return ReadinessResult(False, tuple(reasons))

        try:
            migrate.fresh_manifest()
        except (migrate.MigrationError, OSError):
            reasons.append("migration_manifest_unavailable")
            return ReadinessResult(False, tuple(reasons))

        conn = None
        try:
            options = conninfo_to_dict(dsn)
            existing_options = (options.get("options") or "").strip()
            timeout_ms = self.timeout_seconds * 1000
            options["options"] = (
                f"{existing_options} -c default_transaction_read_only=on "
                f"-c statement_timeout={timeout_ms}"
            ).strip()
            options["connect_timeout"] = self.timeout_seconds
            bounded_dsn = make_conninfo(**options)
            started = self.monotonic()
            conn = self.connect_fn(bounded_dsn)
            if self.monotonic() - started >= self.timeout_seconds:
                reasons.append("database_probe_timeout")
            else:
                rows = conn.execute(queries.get("list_applied_migrations")).fetchall()
                if self.monotonic() - started >= self.timeout_seconds:
                    reasons.append("database_probe_timeout")
                else:
                    try:
                        migrate.verify_release_history(rows)
                    except migrate.MigrationError:
                        reasons.append("schema_mismatch")
        except psycopg.errors.QueryCanceled:
            reasons.append("database_probe_timeout")
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn,
                psycopg.errors.UndefinedObject):
            reasons.append("schema_mismatch")
        except Exception:  # noqa: BLE001 - collapse driver details to a bounded public code
            reasons.append("database_unavailable")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - driver cleanup must not leak connection details
                    reasons.append("database_unavailable")
        return ReadinessResult(not reasons, tuple(dict.fromkeys(reasons)))
