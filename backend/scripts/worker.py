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
import threading
import time
from dataclasses import replace

from app.auth.housekeeping import PURGE_INTERVAL_SECONDS, purge_expired_auth
from app.config import configure_runtime_database, load_settings
from app.db.bootstrap import prepare_database
from app.db.connection import connector, open_connection, resolved_database_url
from app.generation.artifacts import build_worker_handlers
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore, LeaseLost
from app.jobs.worker import run_once


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
    parser.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    args = parser.parse_args()
    if not 1 <= args.lease_seconds <= 3600 or not 0.05 <= args.idle_seconds <= 60:
        parser.error("worker timing settings are outside supported bounds")
    if not 1 <= args.concurrency <= 32:
        parser.error("--concurrency must be between 1 and 32")

    settings = load_settings()
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
        conversation_executor = handlers.conversation

        class AdvisorExecutor:
            def __call__(self, job, context):
                if job.kind == "conversation.turn":
                    return conversation_executor(job, context)
                return artifact_executor(job, context)

            def publish_success(self, conn, job, value):
                if job.kind == "conversation.turn":
                    return conversation_executor.publish_success(conn, job, value)
                return artifact_executor.publish_success(conn, job, value)

        executor = AdvisorExecutor()
        store = JobStore(connector(settings))

        if args.once:
            _drain_source_invalidations(connector(settings), args.worker_id)
            run_once(store, args.worker_id, executor, lease_seconds=args.lease_seconds)
            return

        stop = threading.Event()

        def work(index: int) -> None:
            worker_id = (args.worker_id if args.concurrency == 1
                         else f"{args.worker_id}-{index}")
            while not stop.is_set():
                try:
                    job = run_once(
                        store, worker_id, executor, lease_seconds=args.lease_seconds)
                except LeaseLost:
                    job = None
                except Exception as exc:  # noqa: BLE001 - one thread must not stop the queue
                    print(f"worker {worker_id} failed: {type(exc).__name__}",
                          file=sys.stderr)
                    stop.wait(max(0.5, args.idle_seconds))
                    continue
                if job is None:
                    stop.wait(args.idle_seconds)

        threads = [
            threading.Thread(target=work, args=(index,), daemon=True,
                             name=f"advisor-worker-{index}")
            for index in range(args.concurrency)
        ]
        for thread in threads:
            thread.start()
        next_purge = 0.0
        next_quota_prune = 0.0
        try:
            while True:
                next_purge = _purge_auth_if_due(connector(settings), next_purge)
                _drain_source_invalidations(connector(settings), args.worker_id)
                next_quota_prune = _prune_quotas_if_due(connector(settings), next_quota_prune)
                try:
                    store.recover_expired(limit=100)
                except Exception as exc:  # noqa: BLE001 - maintenance must not stop claims
                    print(f"lease recovery failed: {type(exc).__name__}", file=sys.stderr)
                stop.wait(5.0)
        except KeyboardInterrupt:
            stop.set()
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=min(30.0, float(args.lease_seconds)))


if __name__ == "__main__":
    main()
