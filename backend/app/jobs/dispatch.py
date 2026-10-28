"""Compose the production job executor once, dispatching by durable job kind.

A wrapper that forwards only ``__call__`` and ``publish_success`` silently
drops settlement hooks.  Keeping the dispatch in one class makes that omission
visible to the worker and to tests.
"""

from __future__ import annotations

from typing import Any

from app.jobs.types import Job


class KindDispatchingExecutor:
    """Route one job to the executor for its kind and forward every hook."""

    def __init__(self, *, conversation, artifacts) -> None:
        self._conversation = conversation
        self._artifacts = artifacts

    def _target(self, job: Job):
        if job.kind == "conversation.turn":
            return self._conversation
        return self._artifacts

    def __call__(self, job: Job, context):
        return self._target(job)(job, context)

    def publish_success(self, conn, job: Job, value: Any) -> Any:
        return self._target(job).publish_success(conn, job, value)

    def on_failure(self, conn, job: Job, exc: Exception) -> None:
        hook = getattr(self._target(job), "on_failure", None)
        if callable(hook):
            hook(conn, job, exc)

    def on_cancelled(self, conn, job: Job, exc: Exception) -> None:
        hook = getattr(self._target(job), "on_cancelled", None)
        if callable(hook):
            hook(conn, job, exc)
