"""Shared pre-call reservations and post-call accounting for generation workflows."""
from dataclasses import replace

from app.llm.base import Usage
from app.operations.budget import (
    BudgetExceeded,
    OperationBudgetController,
    OperationLimits,
    estimate_prompt_reservation,
    fence_violation,
)


class GenerationOperation:
    def __init__(self, conn, settings, operation_id, workspace_key, *, limits=None):
        self.conn = conn
        self.operation_id = operation_id
        self.workspace_key = workspace_key
        self.controller = OperationBudgetController()
        self.reservation = None
        self.last_request = None
        self.controller.ensure(conn, operation_id, workspace_key, limits or OperationLimits(
            active_budget_seconds=settings.operation_timeout_seconds,
            max_calls=settings.operation_max_calls,
            total_token_budget=settings.operation_token_budget,
            per_call_output_cap=settings.llm_max_output_tokens,
            per_call_timeout_seconds=settings.llm_timeout_seconds,
        ))

    def assert_publishable(self):
        """Fence deadline and accounting in the caller's publication transaction."""
        from app.db import queries

        row = self.conn.execute(queries.get("get_operation_budget_for_update"),
                                {"operation_id": self.operation_id}).fetchone()
        violation = fence_violation(row, self.workspace_key)
        if violation in {"missing", "workspace"}:
            raise BudgetExceeded("operation_missing", "The generation operation is unavailable.")
        if violation == "deadline_exceeded":
            raise BudgetExceeded("deadline_exceeded", "The generation operation deadline has expired.")
        if violation == "token_budget":
            raise BudgetExceeded("token_budget", "Provider usage exceeded the operation planning budget.")

    def prepare(self, request, *, require_full_output: bool = False):
        self.reservation = self.controller.reserve_before_call(
            self.conn, self.operation_id,
            prompt_reserved_tokens=estimate_prompt_reservation(request),
            requested_output_tokens=request.max_output_tokens,
            minimum_output_tokens=request.max_output_tokens if require_full_output else None,
        )
        self.last_request = replace(request, max_output_tokens=self.reservation.output_grant,
                                    timeout_seconds=self.reservation.timeout_seconds)
        return self.last_request

    def account(self, result, error):
        if self.reservation is None:
            raise RuntimeError("model call has no persisted reservation")
        usage = result.usage if result else getattr(error, "usage", None)
        if not isinstance(usage, Usage):
            usage = None
        status = self.controller.account_usage(
            self.conn, self.reservation, usage=usage,
            outcome="failed" if error else "completed",
        )
        self.reservation = None
        # Completed usage stays recorded even when its output must be withheld.
        if not error and status.remaining_seconds <= 0:
            raise BudgetExceeded("deadline_exceeded", "The generation operation deadline has expired.")
        if not error and status.accounted_tokens + status.reserved_tokens > status.total_token_budget:
            raise BudgetExceeded("token_budget", "Provider usage exceeded the operation planning budget.")
