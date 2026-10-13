"""Run the persistent run queue.

The configured run handler factory accepts Settings and returns a {kind: handler} mapping.
"""

from __future__ import annotations

import argparse
import importlib
import os
import socket
import sys
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.auth.housekeeping import PURGE_INTERVAL_SECONDS, purge_expired_auth
from app.config import configure_runtime_database, load_settings
from app.db.bootstrap import prepare_database
from app.db.connection import connector, open_connection
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore, LeaseLost
from app.jobs.worker import run_once


def _load_handlers(factory_path: str, settings) -> Mapping[str, Any]:
    module_name, separator, attribute = factory_path.partition(":")
    if (
        not separator
        or not module_name
        or not attribute
        or any(part.startswith("_") or not part.isidentifier() for part in module_name.split("."))
        or not attribute.isidentifier()
    ):
        raise ValueError("run_handler_factory must be a trusted module:function path")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("configured run handler factory is not callable")
    handlers = factory(settings)
    if not isinstance(handlers, Mapping):
        raise TypeError("run handler factory must return a mapping by artifact kind")
    return handlers


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
    parser.add_argument("--handler-factory", default=None)
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    args = parser.parse_args()
    if not 1 <= args.lease_seconds <= 3600 or not 0.05 <= args.idle_seconds <= 60:
        parser.error("worker timing settings are outside supported bounds")

    settings = load_settings()
    # Keep the connection context open for the whole worker lifetime: when
    # DATABASE_URL is unset this holds the embedded pgserver running. Exiting
    # the block would stop the server the loop still needs.
    with open_connection(settings) as startup_conn:
        prepare_database(startup_conn, settings.default_collection)
        dsn = startup_conn.info.dsn
        configure_runtime_database(dsn)
        settings = replace(settings, database_url=dsn)
        factory_path = args.handler_factory or settings.run_handler_factory

        handlers = _load_handlers(factory_path, settings)
        artifact_executor = RunExecutor(handlers, data_dir=settings.data_dir)
        conversation_executor = (getattr(handlers, "conversation_executor", None)
                                 or handlers.get("conversation.turn"))

        def get_conversation_executor():
            nonlocal conversation_executor
            if conversation_executor is None:
                from app.conversations.worker import build_default_conversation_executor
                from app.retrieval.config import load_retrieval_config

                conversation_executor = build_default_conversation_executor(
                    settings, load_retrieval_config(settings=settings),
                    connect=connector(settings, pool_name="conversation_execution"),
                )
            return conversation_executor

        class AdvisorExecutor:
            def __call__(self, job, context):
                if job.kind == "conversation.turn":
                    return get_conversation_executor()(job, context)
                return artifact_executor(job, context)

            def publish_success(self, conn, job, value):
                if job.kind == "conversation.turn":
                    return get_conversation_executor().publish_success(conn, job, value)
                return artifact_executor.publish_success(conn, job, value)

        executor = AdvisorExecutor()
        store = JobStore(connector(settings))
        next_purge = 0.0
        while True:
            next_purge = _purge_auth_if_due(connector(settings), next_purge)
            store.recover_expired(limit=100)
            try:
                job = run_once(store, args.worker_id, executor, lease_seconds=args.lease_seconds)
            except LeaseLost:
                job = None
            if args.once:
                return
            if job is None:
                time.sleep(args.idle_seconds)


if __name__ == "__main__":
    main()
