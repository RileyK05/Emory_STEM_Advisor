"""Run the persistent run queue.

Artifact jobs and saved conversation turns share one queue and one model runtime.
`--concurrency` runs several claim/execute threads in one process; each thread
owns its connections and leases, while the model and embedder seams are shared.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from contextlib import nullcontext
from dataclasses import replace

from app.auth.factory import build_auth_service
from app.auth.housekeeping import PURGE_INTERVAL_SECONDS, purge_expired_auth
from app.auth.service import AuthConfigurationError
from app.config import configure_runtime_database, load_settings
from app.conversations.recovery import reconcile_pending_requests
from app.db.bootstrap import prepare_database
from app.db.connection import connector, open_connection, resolved_database_url
from app.generation.artifacts import build_worker_handlers
from app.jobs.dispatch import KindDispatchingExecutor
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import WorkerHealthServer, WorkerRunner, install_stop_signals, run_once
from app.ops.readiness import ReadinessProbe


def _drain_source_invalidations(connect, worker_id: str) -> None:
    """Consume leased source-invalidation rows without stopping the job loop."""
    conn = None
    try:
        conn = connect()
        from app.admin.invalidation import consume_source_invalidations

        consume_source_invalidations(conn, worker_id)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - invalidation must not stop job claims
        print(f"invalidation consumer failed: {type(exc).__name__}", file=sys.stderr)
    finally:
        if conn is not None:
            conn.close()


def _prune_quotas_if_due(connect_fn, next_prune: float) -> float:
    if time.monotonic() < next_prune:
        return next_prune
    try:
        from app.privacy.service import PrivacyService
        from app.quotas.service import QuotaService

        with connect_fn() as conn:
            QuotaService.prune_expired(conn)
            PrivacyService.prune_expired_exports(conn)
    except Exception as exc:  # noqa: BLE001 - maintenance must not stop claims
        print(f"quota/privacy prune failed: {type(exc).__name__}", file=sys.stderr)
    return time.monotonic() + 3600


def _reconcile_conversations(connect_fn) -> None:
    """Settle turns whose bound job is already terminal or missing."""
    try:
        with connect_fn() as conn:
            settled = reconcile_pending_requests(conn)
        if settled:
            sample = ", ".join(item["requestId"] for item in settled[:10])
            print(f"settled {len(settled)} stale conversation request(s): {sample}",
                  file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - housekeeping must not stop claims
        print(f"conversation reconciliation failed: {type(exc).__name__}", file=sys.stderr)


def _purge_auth_if_due(connect_fn, next_purge: float) -> float:
    """Run the auth housekeeping when due; a failure is retried at the next interval."""
    if time.monotonic() < next_purge:
        return next_purge
    try:
        conn = connect_fn()
        try:
            purge_expired_auth(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - housekeeping must never stop the job loop
        print(f"auth housekeeping failed ({type(exc).__name__}); retrying next interval",
              file=sys.stderr)
    return time.monotonic() + PURGE_INTERVAL_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    parser.add_argument("--concurrency", type=int, default=1,
                        help="claim/execute threads in this process (1-32)")
    parser.add_argument("--health-port", type=int, default=None,
                        help="HTTP health/readiness port (defaults to WORKER_HEALTH_PORT; 0 disables)")
    parser.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    args = parser.parse_args()
    if not 1 <= args.lease_seconds <= 3600 or not 0.05 <= args.idle_seconds <= 60:
        parser.error("worker timing settings are outside supported bounds")
    if not 1 <= args.concurrency <= 32:
        parser.error("--concurrency must be between 1 and 32")

    settings = load_settings()
    health_port = settings.worker_health_port if args.health_port is None else args.health_port
    if not 0 <= health_port <= 65535:
        parser.error("--health-port must be between 0 (disabled) and 65535")

    # Keep the connection context open for the whole worker lifetime: when
    # DATABASE_URL is unset this holds the embedded pgserver running. Exiting
    # the block would stop the server the loop still needs.
    with open_connection(settings) as startup_conn:
        prepare_database(startup_conn, settings.default_collection)
        dsn = resolved_database_url(settings, startup_conn)
        configure_runtime_database(dsn)
        settings = replace(settings, database_url=dsn)
        handlers = build_worker_handlers(settings)
        artifact_executor = RunExecutor(handlers.artifacts, data_dir=settings.data_dir)
        executor = KindDispatchingExecutor(
            conversation=handlers.conversation, artifacts=artifact_executor)
        connect_fn = connector(settings)
        store = JobStore(connect_fn)

        if args.once:
            _drain_source_invalidations(connect_fn, args.worker_id)
            _reconcile_conversations(connect_fn)
            run_once(store, args.worker_id, executor, lease_seconds=args.lease_seconds)
            _reconcile_conversations(connect_fn)
            return

        next_purge = 0.0
        next_quota_prune = 0.0

        def maintenance() -> None:
            nonlocal next_purge, next_quota_prune
            next_purge = _purge_auth_if_due(connect_fn, next_purge)
            _drain_source_invalidations(connect_fn, args.worker_id)
            next_quota_prune = _prune_quotas_if_due(connect_fn, next_quota_prune)
            try:
                store.recover_expired(limit=100)
            except Exception as exc:  # noqa: BLE001 - maintenance must not stop claims
                print(f"lease recovery failed: {type(exc).__name__}", file=sys.stderr)
            _reconcile_conversations(connect_fn)

        def on_error(thread_worker_id: str, exc: Exception) -> None:
            print(f"worker {thread_worker_id} failed: {type(exc).__name__}", file=sys.stderr)

        runner = WorkerRunner(
            store,
            args.worker_id,
            executor,
            lease_seconds=args.lease_seconds,
            idle_seconds=args.idle_seconds,
            concurrency=args.concurrency,
            maintenance=maintenance,
            maintenance_interval_seconds=5.0,
            on_error=on_error,
        )
        try:
            auth_service = build_auth_service(settings, connect_fn)
        except AuthConfigurationError:
            auth_service = None
        probe = ReadinessProbe(timeout_seconds=settings.readiness_timeout_seconds)
        health_context = (
            WorkerHealthServer(
                "0.0.0.0",
                health_port,
                accepting=lambda: runner.accepting,
                stopping=lambda: runner.stopping,
                readiness_check=lambda: probe.check(
                    dsn=settings.database_url,
                    auth_service=auth_service,
                    auth_mode=settings.auth_mode,
                ),
            )
            if health_port > 0
            else nullcontext()
        )
        restore_signals = install_stop_signals(runner.request_stop)
        try:
            with health_context:
                runner.run()
        finally:
            restore_signals()


if __name__ == "__main__":
    main()
