"""Persisted, concurrency-safe limits shared by calls in one logical operation.

The byte-based prompt reservation is a bounded planning estimate, not an exact
provider tokenizer count or a hard ceiling on provider usage. SDK-reported usage
replaces it only when the provider returns both prompt and completion counts;
unknown usage remains reserved so process retries cannot silently free capacity.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.db import queries
from app.llm.base import GenerationRequest, Usage, normalize_message
from app.util.workspace import is_valid_workspace_key

MAX_PROMPT_SERIALIZED_BYTES = 8_000_000


class OperationBudgetError(RuntimeError):
    """Safe-to-surface failure from the operation budget controller."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class BudgetExceeded(OperationBudgetError):
    """No call is allowed under this operation's persisted budget."""


class OperationNotFound(OperationBudgetError):
    def __init__(self):
        super().__init__("operation_missing", "The generation operation is unavailable.")


class OperationLimitsConflict(OperationBudgetError):
    def __init__(self):
        super().__init__("limits_changed", "The generation operation limits cannot be changed.")


def fence_violation(row, workspace_key: str | None = None) -> str | None:
    """Classify a locked operation row against its publication fence.

    Returns None when the row may publish, else one of "missing",
    "workspace", "deadline_exceeded", or "token_budget". Callers map the
    code to their own safe error type and message.
    """
    if row is None:
        return "missing"
    if workspace_key is not None and row["workspace_key"] != workspace_key:
        return "workspace"
    if row["deadline_at"] <= row["observed_at"]:
        return "deadline_exceeded"
    if row["accounted_tokens"] + row["reserved_tokens"] > row["total_token_budget"]:
        return "token_budget"
    return None


@dataclass(frozen=True)
class OperationLimits:
    active_budget_seconds: float = 120.0
    max_calls: int = 2
    total_token_budget: int = 16_000
    per_call_output_cap: int = 1_024
    per_call_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.active_budget_seconds) or self.active_budget_seconds <= 0:
            raise ValueError("active operation budget must be positive and finite")
        if type(self.max_calls) is not int or not 1 <= self.max_calls <= 100:
            raise ValueError("operation max_calls must be between 1 and 100")
        if (type(self.total_token_budget) is not int or type(self.per_call_output_cap) is not int
                or self.total_token_budget < 1 or self.per_call_output_cap < 1):
            raise ValueError("operation token budgets must be positive")
        if (not math.isfinite(self.per_call_timeout_seconds)
                or self.per_call_timeout_seconds <= 0):
            raise ValueError("per-call timeout must be positive and finite")


@dataclass(frozen=True)
class OperationStatus:
    operation_id: UUID
    workspace_key: str
    started_at: datetime
    deadline_at: datetime
    remaining_seconds: float
    max_calls: int
    call_count: int
    total_token_budget: int
    reserved_tokens: int
    accounted_tokens: int
    per_call_output_cap: int
    per_call_timeout_seconds: float

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.total_token_budget - self.reserved_tokens - self.accounted_tokens)

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.call_count)


@dataclass(frozen=True)
class UsageReservation:
    operation_id: UUID
    attempt_no: int
    prompt_reserved_tokens: int
    output_grant: int
    timeout_seconds: float
    deadline_at: datetime
    remaining_tokens: int


class OperationBudgetController:
    """Ensure immutable operation limits and reserve each attempt before I/O."""

    def ensure(self, conn, operation_id: UUID, workspace_key: str,
               limits: OperationLimits | None = None) -> OperationStatus:
        limits = limits or OperationLimits()
        self._validate_identity(operation_id, workspace_key)
        # Persist first-start time and limits independently from later provider I/O.
        conn.commit()
        with conn.transaction():
            conn.execute(queries.get("create_operation_budget"), {
                "operation_id": operation_id, "workspace_key": workspace_key,
                "active_budget_seconds": limits.active_budget_seconds,
                "max_calls": limits.max_calls,
                "total_token_budget": limits.total_token_budget,
                "per_call_output_cap": limits.per_call_output_cap,
                "per_call_timeout_seconds": limits.per_call_timeout_seconds,
            })
            row = conn.execute(queries.get("get_operation_budget_for_update"), {
                "operation_id": operation_id,
            }).fetchone()
            if row is None:
                raise OperationNotFound()
            actual_budget = (row["deadline_at"] - row["started_at"]).total_seconds()
            if (row["workspace_key"] != workspace_key
                    or row["max_calls"] != limits.max_calls
                    or row["total_token_budget"] != limits.total_token_budget
                    or row["per_call_output_cap"] != limits.per_call_output_cap
                    or float(row["per_call_timeout_seconds"]) != limits.per_call_timeout_seconds
                    or abs(actual_budget - limits.active_budget_seconds) > 0.05):
                raise OperationLimitsConflict()
            return self._status(row)

    def reserve_before_call(self, conn, operation_id: UUID, *,
                            prompt_reserved_tokens: int,
                            requested_output_tokens: int | None = None,
                            minimum_output_tokens: int | None = None) -> UsageReservation:
        if isinstance(prompt_reserved_tokens, bool) or not isinstance(prompt_reserved_tokens, int):
            raise TypeError("prompt token reservation must be an integer")
        if prompt_reserved_tokens < 0:
            raise ValueError("prompt token reservation cannot be negative")
        conn.commit()
        with conn.transaction():
            row = conn.execute(queries.get("get_operation_budget_for_update"), {
                "operation_id": operation_id,
            }).fetchone()
            if row is None:
                raise OperationNotFound()
            remaining_seconds = (row["deadline_at"] - row["observed_at"]).total_seconds()
            if remaining_seconds <= 0:
                raise BudgetExceeded("deadline_exceeded", "The generation operation deadline has expired.")
            if row["call_count"] >= row["max_calls"]:
                raise BudgetExceeded("call_limit", "The generation operation call limit was reached.")
            remaining_tokens = (row["total_token_budget"] - row["accounted_tokens"]
                                - row["reserved_tokens"])
            available_output = remaining_tokens - prompt_reserved_tokens
            requested = (row["per_call_output_cap"] if requested_output_tokens is None
                         else requested_output_tokens)
            if isinstance(requested, bool) or not isinstance(requested, int):
                raise TypeError("requested output tokens must be an integer")
            if requested < 1 or available_output < 1:
                raise BudgetExceeded("token_budget", "The generation operation token budget was exhausted.")
            output_grant = min(requested, row["per_call_output_cap"], available_output)
            if minimum_output_tokens is not None:
                if (isinstance(minimum_output_tokens, bool)
                        or not isinstance(minimum_output_tokens, int)
                        or minimum_output_tokens < 1):
                    raise ValueError("minimum output token grant must be a positive integer")
                if output_grant < minimum_output_tokens:
                    raise BudgetExceeded(
                        "token_budget",
                        "The shared operation budget cannot grant the planned output limit.",
                    )
            attempt_no = row["call_count"] + 1
            reservation_tokens = prompt_reserved_tokens + output_grant
            updated = conn.execute(queries.get("reserve_operation_budget_call"), {
                "operation_id": operation_id,
                "expected_call_count": row["call_count"],
                "expected_reserved_tokens": row["reserved_tokens"],
                "reservation_tokens": reservation_tokens,
            }).fetchone()
            if updated is None:
                raise BudgetExceeded("concurrent_reservation", "The generation operation budget changed; retry safely.")
            conn.execute(queries.get("insert_operation_budget_call"), {
                "operation_id": operation_id, "attempt_no": attempt_no,
                "prompt_reserved_tokens": prompt_reserved_tokens,
                "output_reserved_tokens": output_grant,
            })
            remaining_tokens -= reservation_tokens
            return UsageReservation(
                operation_id=operation_id, attempt_no=attempt_no,
                prompt_reserved_tokens=prompt_reserved_tokens, output_grant=output_grant,
                timeout_seconds=min(float(row["per_call_timeout_seconds"]), remaining_seconds),
                deadline_at=row["deadline_at"], remaining_tokens=remaining_tokens,
            )

    def account_usage(self, conn, reservation: UsageReservation, *, usage: Usage | None,
                      outcome: Literal["completed", "failed", "cancelled"] = "completed"
                      ) -> OperationStatus:
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid operation call outcome")
        conn.commit()
        with conn.transaction():
            row = conn.execute(queries.get("get_operation_call_for_update"), {
                "operation_id": reservation.operation_id,
                "attempt_no": reservation.attempt_no,
            }).fetchone()
            if row is None:
                raise OperationNotFound()
            measured = bool(usage and usage.source == "provider"
                            and usage.prompt_tokens is not None
                            and usage.completion_tokens is not None)
            late_measurement = (
                measured
                and row["status"] in {"unknown", "failed", "cancelled"}
                and row["measured_prompt_tokens"] is None
                and row["measured_completion_tokens"] is None
            )
            if row["status"] != "reserved" and not late_measurement:
                raise OperationBudgetError(
                    "attempt_already_accounted", "This model attempt was already accounted."
                )
            if measured:
                prompt = int(usage.prompt_tokens)
                completion = int(usage.completion_tokens)
                if prompt < 0 or completion < 0:
                    raise ValueError("provider usage counts cannot be negative")
                reserved = row["prompt_reserved_tokens"] + row["output_reserved_tokens"]
                result = conn.execute(queries.get("settle_operation_budget_call"), {
                    "operation_id": reservation.operation_id,
                    "reservation_tokens": reserved,
                    "measured_tokens": prompt + completion,
                }).fetchone()
                if result is None:
                    raise OperationBudgetError("reservation_inconsistent", "The model attempt reservation is inconsistent.")
                settled = conn.execute(queries.get("settle_operation_call"), {
                    "operation_id": reservation.operation_id,
                    "attempt_no": reservation.attempt_no,
                    "prompt_tokens": prompt, "completion_tokens": completion,
                    "status": outcome,
                }).fetchone()
                if settled is None:
                    raise OperationBudgetError(
                        "attempt_already_accounted",
                        "This model attempt was already accounted.",
                    )
            else:
                # Unknown/estimated usage never releases reserved capacity.
                conn.execute(queries.get("keep_operation_call_reservation"), {
                    "operation_id": reservation.operation_id,
                    "attempt_no": reservation.attempt_no,
                    "status": "unknown" if outcome == "completed" else outcome,
                })
            status = conn.execute(queries.get("get_operation_budget"), {
                "operation_id": reservation.operation_id,
            }).fetchone()
            if status is None:
                raise OperationNotFound()
            return self._status(status)

    def get(self, conn, operation_id: UUID) -> OperationStatus:
        row = conn.execute(queries.get("get_operation_budget"), {
            "operation_id": operation_id,
        }).fetchone()
        if row is None:
            raise OperationNotFound()
        return self._status(row)

    @staticmethod
    def _status(row: Mapping[str, Any]) -> OperationStatus:
        remaining = max(0.0, (row["deadline_at"] - row["observed_at"]).total_seconds())
        return OperationStatus(
            operation_id=row["operation_id"], workspace_key=row["workspace_key"],
            started_at=row["started_at"], deadline_at=row["deadline_at"],
            remaining_seconds=remaining, max_calls=row["max_calls"], call_count=row["call_count"],
            total_token_budget=row["total_token_budget"], reserved_tokens=row["reserved_tokens"],
            accounted_tokens=row["accounted_tokens"],
            per_call_output_cap=row["per_call_output_cap"],
            per_call_timeout_seconds=float(row["per_call_timeout_seconds"]),
        )

    @staticmethod
    def _validate_identity(operation_id: UUID, workspace_key: str) -> None:
        if not isinstance(operation_id, UUID):
            raise TypeError("operation_id must be a UUID")
        if not is_valid_workspace_key(workspace_key):
            raise ValueError("workspace key must contain 1-200 characters")


def estimate_prompt_reservation(request: GenerationRequest, *,
                                max_serialized_bytes: int = MAX_PROMPT_SERIALIZED_BYTES) -> int:
    """Estimate reservation from bounded serialized UTF-8 bytes and fixed overhead.

    This deliberately reserves one planning unit per serialized byte. Hidden
    provider prompt templates and provider tokenizers are outside this estimate;
    observed provider usage is recorded even if it exceeds the reservation.
    """
    records: dict[str, Any] = {
        "messages": [normalize_message(m) for m in request.messages],
        "schema": dict(request.response_schema) if request.response_schema else None,
        "tools": [{"name": tool.name, "description": tool.description,
                   "schema": dict(tool.input_schema)} for tool in request.tools],
    }
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    byte_count = 0
    for part in encoder.iterencode(records):
        byte_count += len(part.encode("utf-8"))
        if byte_count > max_serialized_bytes:
            raise BudgetExceeded("prompt_too_large", "The model prompt exceeds the operation input budget.")
    fixed_overhead = 32 * len(request.messages)
    if request.response_schema:
        fixed_overhead += 256
    fixed_overhead += 64 * len(request.tools)
    return max(1, byte_count + fixed_overhead)
