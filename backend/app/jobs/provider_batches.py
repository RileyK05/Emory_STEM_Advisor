"""Durable tracking for provider-native asynchronous LLM batches.

This service never executes a provider request inside a database transaction. A
submission intent is committed before upload/submission; if the remote submit
outcome is ambiguous, it becomes `unknown` and is never automatically repeated.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

from app.db import queries
from app.db.connection import connection_transaction
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    ToolDefinition,
    Usage,
    normalize_message,
)
from app.llm.batch import (
    BATCH_STATES,
    TERMINAL_BATCH_STATES,
    BatchItem,
    BatchItemResult,
    BatchProgress,
    BatchSubmission,
)
from app.operations.budget import (
    OperationBudgetController,
    OperationLimits,
    UsageReservation,
    estimate_prompt_reservation,
)
from app.util.digest import canonical_json_bytes
from app.util.workspace import is_valid_workspace_key

MAX_STORED_ITEM_RESULT_BYTES = 1_000_000


class _BatchPublicationDeadlineExpired(RuntimeError):
    pass


class ProviderBatchError(RuntimeError):
    """Base class for safe durable-batch failures."""


class ProviderBatchNotFound(ProviderBatchError):
    pass


class ProviderBatchConflict(ProviderBatchError):
    pass


class ProviderBatchInProgress(ProviderBatchError):
    pass


class ProviderBatchUnknown(ProviderBatchError):
    pass


class ProviderBatchProfileChanged(ProviderBatchError):
    pass


class ProviderBatchNotPublishable(ProviderBatchError):
    pass


@dataclass(frozen=True)
class BatchItemSnapshot:
    correlation_id: str
    operation_id: UUID
    state: str
    result: GenerationResult | None
    error_code: str | None
    usage: Usage
    publication_state: str = "pending"
    artifact_id: UUID | None = None
    revision_id: UUID | None = None


@dataclass(frozen=True)
class ProviderBatchSnapshot:
    batch_id: UUID
    workspace_key: str
    provider: str
    model_id: str
    state: str
    provider_batch_id: str | None
    input_file_id: str | None
    profile_id: UUID | None
    profile_revision: int | None
    item_count: int
    total_count: int | None
    completed_count: int | None
    failed_count: int | None
    items: tuple[BatchItemSnapshot, ...]
    route_role: str | None = None
    route_data_scope: str | None = None
    cancel_requested: bool = False


def _request_record(request: GenerationRequest) -> dict[str, Any]:
    if request.tools:
        # Native batches are independent requests; tool loops need interactive
        # orchestration and durable side-effect handling outside this service.
        raise ValueError("provider-native batches do not accept tool requests")
    return {
        "messages": [normalize_message(message) for message in request.messages],
        "model": request.model,
        "max_output_tokens": request.max_output_tokens,
        "temperature": request.temperature,
        "reasoning_effort": request.reasoning_effort,
        "response_schema": dict(request.response_schema) if request.response_schema else None,
        "tool_choice": request.tool_choice,
        "metadata": dict(request.metadata),
        "timeout_seconds": request.timeout_seconds,
    }


def _request_from_record(value: Mapping[str, Any]) -> GenerationRequest:
    tools = tuple(ToolDefinition(item["name"], item["description"], item["input_schema"])
                  for item in value.get("tools", ()))
    return GenerationRequest(
        messages=tuple(value["messages"]), model=value.get("model"),
        max_output_tokens=value.get("max_output_tokens"), temperature=value.get("temperature"),
        reasoning_effort=value.get("reasoning_effort"), response_schema=value.get("response_schema"),
        tools=tools, tool_choice=value.get("tool_choice"), metadata=value.get("metadata", {}),
        timeout_seconds=value.get("timeout_seconds"),
    )


def _canonical(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        raise ValueError("provider batch input is not valid JSON") from None


def _request_result(result: GenerationResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "tool_calls": [{"id": call.id, "name": call.name,
                        "arguments": dict(call.arguments)} for call in result.tool_calls],
        "finish_reason": result.finish_reason,
        "refusal": result.refusal,
        "request_id": result.request_id,
        "model": result.model,
        "usage": {"prompt_tokens": result.usage.prompt_tokens,
                  "completion_tokens": result.usage.completion_tokens,
                  "total_tokens": result.usage.total_tokens,
                  "source": result.usage.source},
    }


def _result_from_record(value: Mapping[str, Any]) -> GenerationResult:
    from app.llm.base import ToolCall

    usage = value.get("usage") or {}
    return GenerationResult(
        text=value.get("text", ""),
        tool_calls=tuple(ToolCall(item["id"], item["name"], item["arguments"])
                         for item in value.get("tool_calls", ())),
        finish_reason=value.get("finish_reason", "unknown"),
        refusal=value.get("refusal"), request_id=value.get("request_id"), model=value.get("model"),
        usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens"),
                    usage.get("total_tokens"), usage.get("source", "unknown")),
    )


class ProviderBatchService:
    """Persisted submit/poll/cancel and per-item budget accounting for native batches."""

    def __init__(self, connect: Callable[[], Any], *, max_items: int = 50_000,
                 max_request_bytes: int = 200_000_000,
                 submission_lease_seconds: float = 120.0,
                 max_result_bytes: int = MAX_STORED_ITEM_RESULT_BYTES):
        if (max_items < 1 or max_request_bytes < 1 or max_result_bytes < 1
                or max_result_bytes > MAX_STORED_ITEM_RESULT_BYTES
                or not math.isfinite(submission_lease_seconds) or submission_lease_seconds <= 0):
            raise ValueError("provider batch limits are invalid")
        self._connect = connect
        self.max_items = max_items
        self.max_request_bytes = max_request_bytes
        self.submission_lease_seconds = submission_lease_seconds
        self.max_result_bytes = max_result_bytes
        self._budgets = OperationBudgetController()

    def _transaction(self):
        return connection_transaction(self._connect)

    def submit(self, *, batch_id: UUID, workspace_key: str, provider,
               items: Sequence[BatchItem], limits: OperationLimits | Callable[[BatchItem], OperationLimits],
               profile_id: UUID | None = None, profile_revision: int | None = None,
               consumer_data: Mapping[str, Mapping[str, Any]] | None = None) -> ProviderBatchSnapshot:
        provider_name = getattr(provider, "provider", None)
        model_id = getattr(provider, "model", None)
        if not isinstance(batch_id, UUID) or not is_valid_workspace_key(workspace_key):
            raise ValueError("provider batch identity is invalid")
        if not provider_name or not model_id:
            raise ValueError("provider batch adapter must expose its configured provider and model")
        if (profile_id is None) != (profile_revision is None):
            raise ValueError("profile ID and revision must be supplied together")
        if profile_revision is not None and profile_revision < 1:
            raise ValueError("profile revision must be positive")
        records, bounded_consumer_data, limits_data = self._validate_items(
            items, limits, consumer_data, model_id)
        identity = {"batch_id": str(batch_id), "workspace_key": workspace_key,
                    "provider": provider_name, "model_id": model_id,
                    "profile_id": str(profile_id) if profile_id else None,
                    "profile_revision": profile_revision,
                    "items": [{"correlation_id": key, "request_fingerprint": request_fingerprint}
                              for key, _request, request_fingerprint in records],
                    "limits": limits_data, "consumer_data": bounded_consumer_data}
        fingerprint = hashlib.sha256(_canonical(identity)).hexdigest()
        total_request_bytes = sum(len(_canonical(record)) for _key, record, _hash in records)
        if total_request_bytes > self.max_request_bytes:
            raise ValueError("provider batch inputs exceed the configured byte limit")

        parent = self._ensure_record(batch_id, workspace_key, provider_name, model_id,
                                     profile_id, profile_revision, fingerprint, limits_data)
        if parent["state"] == "unknown":
            raise ProviderBatchUnknown("provider batch submission outcome is unknown; it was not repeated")
        if parent["state"] == "submitting":
            if parent["submission_lease_until"] and parent["submission_lease_until"] > datetime.now(UTC):
                return self.get(batch_id, workspace_key)
            self._mark_unknown(batch_id, workspace_key, "submission_outcome_unknown")
            raise ProviderBatchUnknown("provider batch submission outcome is unknown; it was not repeated")
        if parent["state"] != "prepared":
            return self.get(batch_id, workspace_key)

        batch_items = [BatchItem(key, _request_from_record(request))
                       for key, request, _hash in records]
        self._ensure_reservations(batch_id, workspace_key, batch_items, limits,
                                  bounded_consumer_data)
        with self._transaction() as conn:
            row = conn.execute(queries.get("get_provider_batch_for_update"),
                               {"batch_id": batch_id}).fetchone()
            _assert_workspace(row, workspace_key)
            if (row["request_fingerprint"].strip() != fingerprint or row["state"] != "prepared"
                    or row["cancel_requested"]):
                raise ProviderBatchConflict("provider batch changed before submission")
            self._assert_profile_current(conn, row)
            stored_items = conn.execute(queries.get("list_provider_batch_items"),
                                        {"batch_id": batch_id}).fetchall()
            if len(stored_items) != len(records) or any(item["state"] != "reserved" for item in stored_items):
                raise ProviderBatchConflict("provider batch item reservations are incomplete")
            token = uuid4()
            start = conn.execute(queries.get("start_provider_batch_submission"), {
                "batch_id": batch_id, "workspace_key": workspace_key,
                "submission_token": token,
                "submission_lease_until": datetime.now(UTC) +
                    timedelta(seconds=self.submission_lease_seconds),
            }).fetchone()
            if start is None:
                raise ProviderBatchInProgress("provider batch submission is already claimed")

        # Submit exactly once after the persisted fence and with no DB txn open.
        try:
            submission = provider.submit(batch_items)
            if (not isinstance(submission, BatchSubmission) or not submission.batch_id
                    or submission.item_count != len(batch_items)):
                raise ValueError("provider returned an invalid batch submission")
        except Exception:  # noqa: BLE001 - remote acceptance can be ambiguous
            self._mark_unknown(batch_id, workspace_key, "submission_outcome_unknown", token=token)
            raise ProviderBatchUnknown("provider batch submission outcome is unknown; it was not repeated") from None

        with self._transaction() as conn:
            state = submission.state if submission.state in BATCH_STATES else "unknown"
            row = conn.execute(queries.get("mark_provider_batch_submitted"), {
                "batch_id": batch_id, "workspace_key": workspace_key,
                "submission_token": token, "provider_batch_id": submission.batch_id,
                "input_file_id": submission.input_file_id, "state": state,
                "total_count": submission.item_count, "completed_count": None,
                "failed_count": None, "terminal": state in TERMINAL_BATCH_STATES,
            }).fetchone()
            if row is None:
                raise ProviderBatchUnknown("provider batch handle could not be committed safely")
        return self.get(batch_id, workspace_key)

    def _validate_items(self, items, limits, consumer_data, model_id):
        """Bound and fingerprint every item: (records, consumer data, per-item limits)."""
        if not items or len(items) > self.max_items:
            raise ValueError("provider batch item count is outside its configured limit")
        ids = [item.correlation_id for item in items]
        if any(not key or len(key) > 256 for key in ids) or len(ids) != len(set(ids)):
            raise ValueError("provider batch correlation IDs must be unique and bounded")
        consumer_data = dict(consumer_data or {})
        if set(consumer_data) - set(ids):
            raise ValueError("consumer data contains an unknown batch item")
        bounded_consumer_data = {}
        for key in ids:
            value = consumer_data.get(key, {})
            if not isinstance(value, Mapping):
                raise TypeError("consumer data must be an object per item")
            if len(_canonical(value)) > 1_900_000:
                raise ValueError("consumer data exceeds the configured per-item limit")
            bounded_consumer_data[key] = dict(value)
        records, limits_data = [], []
        for item in items:
            if item.request.model not in (None, model_id):
                raise ValueError("provider batch requests must use the configured model")
            request = _request_record(item.request)
            encoded = _canonical(request)
            if len(encoded) > self.max_request_bytes:
                raise ValueError("provider batch item input exceeds the configured byte limit")
            item_limits = limits(item) if callable(limits) else limits
            if not isinstance(item_limits, OperationLimits):
                raise TypeError("batch limits must be OperationLimits")
            if item_limits.max_calls != 1:
                raise ValueError("each native batch item must reserve exactly one provider call")
            records.append((item.correlation_id, request, hashlib.sha256(encoded).hexdigest()))
            limits_data.append(_limits_dict(item_limits))
        return records, bounded_consumer_data, limits_data

    def list(self, workspace_key: str) -> tuple[dict[str, Any], ...]:
        """Return recent summary metadata for one workspace without provider I/O."""
        if not is_valid_workspace_key(workspace_key):
            raise ValueError("provider batch workspace is invalid")
        with self._transaction() as conn:
            rows = conn.execute(queries.get("list_provider_batches_for_workspace"), {
                "workspace_key": workspace_key,
            }).fetchall()
        return tuple({
            "batch_id": row["batch_id"], "state": row["state"],
            "cancel_requested": bool(row["cancel_requested"]),
            "item_count": int(row["item_count"]), "total_count": row["total_count"],
            "completed_count": row["completed_count"], "failed_count": row["failed_count"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        } for row in rows)

    def get(self, batch_id: UUID, workspace_key: str) -> ProviderBatchSnapshot:
        with self._transaction() as conn:
            parent = conn.execute(queries.get("get_provider_batch"), {"batch_id": batch_id}).fetchone()
            _assert_workspace(parent, workspace_key)
            rows = conn.execute(queries.get("list_provider_batch_items"),
                                {"batch_id": batch_id}).fetchall()
            return _snapshot(parent, rows)

    def idempotent_replay(self, batch_id: UUID, workspace_key: str,
                          request_fingerprint: str) -> ProviderBatchSnapshot | None:
        """Resolve a client retry before repeating retrieval, routing, or SDK setup."""
        with self._transaction() as conn:
            parent = conn.execute(queries.get("get_provider_batch"), {
                "batch_id": batch_id,
            }).fetchone()
            if parent is None:
                return None
            _assert_workspace(parent, workspace_key)
            # A prepared batch has no provider handle yet: a crash between item
            # reservation and submission must resume submission, not replay a
            # non-existent result. The caller re-enters submit() for it.
            if parent["state"] == "prepared":
                return None
            rows = conn.execute(queries.get("list_provider_batch_items"), {
                "batch_id": batch_id,
            }).fetchall()
            stored = {row["consumer_data"].get("client_request_fingerprint") for row in rows}
            if not rows or stored != {request_fingerprint}:
                raise ProviderBatchConflict("batch ID was already used for different inputs")
            return _snapshot(parent, rows)

    def poll(self, batch_id: UUID, workspace_key: str, provider) -> ProviderBatchSnapshot:
        parent = self._read_parent(batch_id, workspace_key)
        if parent["state"] == "submitting":
            if parent["submission_lease_until"] and parent["submission_lease_until"] <= datetime.now(UTC):
                self._mark_unknown(batch_id, workspace_key, "submission_outcome_unknown")
            return self.get(batch_id, workspace_key)
        remote_id = parent["provider_batch_id"]
        if parent["state"] == "unknown" and not remote_id:
            return self.get(batch_id, workspace_key)
        if not remote_id:
            if parent["state"] == "prepared":
                return self.get(batch_id, workspace_key)
            self._mark_unknown(batch_id, workspace_key, "provider_handle_missing")
            return self.get(batch_id, workspace_key)
        if getattr(provider, "provider", None) != parent["provider"]:
            raise ProviderBatchConflict("provider adapter does not match this batch")
        if parent["state"] not in TERMINAL_BATCH_STATES:
            progress = provider.status(remote_id)
            if not isinstance(progress, BatchProgress) or progress.batch_id != remote_id:
                self._mark_unknown(batch_id, workspace_key, "invalid_provider_status")
                return self.get(batch_id, workspace_key)
            self._store_progress(batch_id, workspace_key, progress)
            parent = self._read_parent(batch_id, workspace_key)
        if parent["state"] in TERMINAL_BATCH_STATES:
            self._collect_terminal(batch_id, workspace_key, provider, parent)
        return self.get(batch_id, workspace_key)

    def _store_progress(self, batch_id: UUID, workspace_key: str, progress: BatchProgress) -> None:
        """Persist normalized provider progress; a state we do not know is recorded as unknown."""
        state = progress.state if progress.state in BATCH_STATES else "unknown"
        with self._transaction() as conn:
            conn.execute(queries.get("update_provider_batch_progress"), {
                "batch_id": batch_id, "workspace_key": workspace_key,
                "state": state, "total_count": progress.total_count,
                "completed_count": progress.completed_count, "failed_count": progress.failed_count,
                "failure_code": "provider_state_unknown" if state == "unknown" else None,
                "terminal": state in TERMINAL_BATCH_STATES,
            })

    def request_cancel(self, batch_id: UUID, workspace_key: str) -> ProviderBatchSnapshot:
        """Durably fence future publication before attempting provider cancellation."""
        with self._transaction() as conn:
            row = conn.execute(queries.get("request_provider_batch_cancel"), {
                "batch_id": batch_id, "workspace_key": workspace_key,
            }).fetchone()
            if row is None:
                raise ProviderBatchNotFound("provider batch was not found")
        return self.get(batch_id, workspace_key)

    def cancel(self, batch_id: UUID, workspace_key: str, provider) -> ProviderBatchSnapshot:
        self.request_cancel(batch_id, workspace_key)
        parent = self._read_parent(batch_id, workspace_key)
        if parent["state"] in TERMINAL_BATCH_STATES or parent["state"] == "unknown":
            if parent["state"] in TERMINAL_BATCH_STATES:
                self._collect_terminal(batch_id, workspace_key, provider, parent)
            return self.get(batch_id, workspace_key)
        if not parent["provider_batch_id"]:
            self._mark_unknown(batch_id, workspace_key, "cannot_cancel_without_provider_handle")
            return self.get(batch_id, workspace_key)
        # Provider cancellation may race completion; always inspect normalized result state.
        progress = provider.cancel(parent["provider_batch_id"])
        if not isinstance(progress, BatchProgress) or progress.batch_id != parent["provider_batch_id"]:
            self._mark_unknown(batch_id, workspace_key, "invalid_provider_cancel_status")
            return self.get(batch_id, workspace_key)
        self._store_progress(batch_id, workspace_key, progress)
        parent = self._read_parent(batch_id, workspace_key)
        if parent["state"] in TERMINAL_BATCH_STATES:
            self._collect_terminal(batch_id, workspace_key, provider, parent)
        return self.get(batch_id, workspace_key)

    def publishable_results(self, batch_id: UUID, workspace_key: str, *,
                            require_all: bool = False) -> tuple[BatchItemResult, ...]:
        """Return only complete, measured, budget-settled results for fenced publication."""
        with self._transaction() as conn:
            parent = conn.execute(queries.get("get_provider_batch"), {"batch_id": batch_id}).fetchone()
            _assert_workspace(parent, workspace_key)
            if parent["cancel_requested"] or parent["state"] in {"cancel_requested", "cancelling", "cancelled"}:
                raise ProviderBatchNotPublishable("cancelled batch results cannot be published")
            if parent["state"] not in TERMINAL_BATCH_STATES:
                raise ProviderBatchNotPublishable("provider batch has not reached a terminal state")
            self._assert_profile_current(conn, parent)
            rows = conn.execute(queries.get("list_provider_batch_items"),
                                {"batch_id": batch_id}).fetchall()
            output = []
            for row in rows:
                if row["state"] != "completed" or row["usage_source"] != "provider":
                    if require_all:
                        raise ProviderBatchNotPublishable("not every batch item has a publishable result")
                    continue
                budget = self._budgets.get(conn, row["operation_id"])
                if budget.reserved_tokens or budget.accounted_tokens > budget.total_token_budget:
                    raise ProviderBatchNotPublishable("batch item usage has not settled within its budget")
                if budget.remaining_seconds <= 0:
                    raise ProviderBatchNotPublishable("batch item operation deadline has expired")
                if row["result_json"] is None:
                    raise ProviderBatchNotPublishable("completed batch item has no stored result")
                result = _result_from_record(row["result_json"])
                output.append(BatchItemResult(row["correlation_id"], result=result, usage=result.usage))
            return tuple(output)

    def publish_artifacts(self, batch_id: UUID, workspace_key: str, publisher) -> tuple[dict, ...]:
        """Publish validated per-item artifacts under cancellation/profile/budget fences.

        `publisher(conn, parent, item, result)` must perform local validation and
        writes in the caller's transaction and return artifact/revision UUIDs.
        It must not perform provider, filesystem, or other external I/O.
        """
        published = []
        with self._transaction() as conn:
            def withhold(item):
                conn.execute(queries.get("set_provider_batch_item_withheld"), {
                    "batch_id": batch_id, "workspace_key": workspace_key,
                    "correlation_id": item["correlation_id"],
                })

            parent = conn.execute(queries.get("get_provider_batch_for_update"), {
                "batch_id": batch_id,
            }).fetchone()
            _assert_workspace(parent, workspace_key)
            if parent["state"] not in TERMINAL_BATCH_STATES:
                raise ProviderBatchNotPublishable("provider batch has not reached a terminal state")
            try:
                self._assert_profile_current(conn, parent)
            except ProviderBatchProfileChanged:
                stale_items = conn.execute(queries.get("list_provider_batch_items_for_update"), {
                    "batch_id": batch_id,
                }).fetchall()
                for item in stale_items:
                    if item["publication_state"] == "pending":
                        withhold(item)
                return ()
            items = conn.execute(queries.get("list_provider_batch_items_for_update"), {
                "batch_id": batch_id,
            }).fetchall()
            cancel_requested = parent["cancel_requested"] or parent["state"] in {
                "cancel_requested", "cancelling", "cancelled",
            }
            for item in items:
                if item["publication_state"] != "pending":
                    continue
                if cancel_requested:
                    withhold(item)
                    continue
                if (item["state"] != "completed" or item["usage_source"] != "provider"
                        or item["result_json"] is None):
                    if item["state"] in {"completed", "failed", "cancelled", "unknown"}:
                        withhold(item)
                    continue
                conn.execute(queries.get("lock_provider_batch_operation_budget"), {
                    "operation_id": item["operation_id"],
                })
                budget = self._budgets.get(conn, item["operation_id"])
                call = conn.execute(queries.get("get_provider_batch_operation_call"), {
                    "operation_id": item["operation_id"],
                }).fetchone()
                if (budget.workspace_key != workspace_key or budget.remaining_seconds <= 0
                        or budget.reserved_tokens or budget.accounted_tokens > budget.total_token_budget
                        or call is None or call["status"] != "completed"
                        or call["measured_prompt_tokens"] != item["prompt_tokens"]
                        or call["measured_completion_tokens"] != item["completion_tokens"]):
                    withhold(item)
                    continue
                result = _result_from_record(item["result_json"])
                try:
                    # Savepoint lets the outer transaction persist a withheld
                    # state if local validation/writes crossed the operation deadline.
                    with conn.transaction():
                        artifact_id, revision_id = publisher(conn, parent, item, result)
                        after_publish = self._budgets.get(conn, item["operation_id"])
                        if after_publish.remaining_seconds <= 0:
                            raise _BatchPublicationDeadlineExpired
                except (_BatchPublicationDeadlineExpired, ValueError, TypeError, KeyError):
                    withhold(item)
                    continue
                updated = conn.execute(queries.get("set_provider_batch_item_published"), {
                    "batch_id": batch_id, "workspace_key": workspace_key,
                    "correlation_id": item["correlation_id"],
                    "artifact_id": artifact_id, "revision_id": revision_id,
                }).fetchone()
                if updated:
                    published.append({"correlation_id": item["correlation_id"],
                                      "artifact_id": artifact_id,
                                      "revision_id": revision_id})
        return tuple(published)

    def _ensure_record(self, batch_id, workspace_key, provider, model_id, profile_id,
                       profile_revision, fingerprint, limits_data):
        with self._transaction() as conn:
            row = conn.execute(queries.get("insert_provider_batch"), {
                "batch_id": batch_id, "workspace_key": workspace_key, "provider": provider,
                "model_id": model_id, "profile_id": profile_id,
                "profile_revision": profile_revision, "request_fingerprint": fingerprint,
                "operation_limits": Jsonb(limits_data),
            }).fetchone()
            if row is None:
                row = conn.execute(queries.get("get_provider_batch_for_update"),
                                   {"batch_id": batch_id}).fetchone()
            _assert_workspace(row, workspace_key)
            if row["request_fingerprint"].strip() != fingerprint:
                raise ProviderBatchConflict("batch ID was already used for different inputs")
            if row["provider"] != provider or row["model_id"] != model_id:
                raise ProviderBatchConflict("batch provider or model changed")
            if row["profile_id"] != profile_id or row["profile_revision"] != profile_revision:
                raise ProviderBatchConflict("batch connection binding changed")
            return row

    def _ensure_reservations(self, batch_id, workspace_key, items, limits, consumer_data):
        conn = self._connect()
        try:
            records = []
            for item in items:
                item_limits = limits(item) if callable(limits) else limits
                operation_id = uuid5(batch_id, item.correlation_id)
                status = self._budgets.ensure(conn, operation_id, workspace_key, item_limits)
                prompt_tokens = estimate_prompt_reservation(item.request)
                if status.call_count == 0:
                    reservation = self._budgets.reserve_before_call(
                        conn, operation_id, prompt_reserved_tokens=prompt_tokens,
                        requested_output_tokens=item.request.max_output_tokens,
                    )
                    attempt_no = reservation.attempt_no
                elif status.call_count == 1:
                    row = conn.execute(queries.get("get_provider_batch_operation_call"), {
                        "operation_id": operation_id,
                    }).fetchone()
                    if (row is None or row["attempt_no"] != 1 or row["status"] != "reserved"
                            or row["prompt_reserved_tokens"] != prompt_tokens):
                        raise ProviderBatchConflict("batch item budget reservation changed")
                    attempt_no = row["attempt_no"]
                else:
                    raise ProviderBatchConflict("batch item operation already has an attempt")
                records.append((item, operation_id, attempt_no))
            with conn.transaction():
                for item, operation_id, _attempt_no in records:
                    request = _request_record(item.request)
                    encoded = _canonical(request)
                    conn.execute(queries.get("insert_provider_batch_item"), {
                        "batch_id": batch_id, "workspace_key": workspace_key,
                        "correlation_id": item.correlation_id, "operation_id": operation_id,
                        "request_fingerprint": hashlib.sha256(encoded).hexdigest(),
                        "request_json": Jsonb(request),
                    })
                    current = conn.execute(queries.get("get_provider_batch_item"), {
                        "batch_id": batch_id, "correlation_id": item.correlation_id,
                    }).fetchone()
                    if current and current["consumer_data"] == {}:
                        conn.execute(queries.get("set_provider_batch_item_consumer_data"), {
                            "batch_id": batch_id, "workspace_key": workspace_key,
                            "correlation_id": item.correlation_id,
                            "consumer_data": Jsonb(consumer_data[item.correlation_id]),
                        })
                rows = conn.execute(queries.get("list_provider_batch_items"), {
                    "batch_id": batch_id,
                }).fetchall()
                if len(rows) != len(records) or any(row["state"] != "reserved" for row in rows):
                    raise ProviderBatchConflict("persisted batch item set does not match the request")
        finally:
            conn.close()

    def _collect_terminal(self, batch_id, workspace_key, provider, parent):
        rows = self._read_items(batch_id)
        if rows and all(row["state"] not in {"reserved", "received"} for row in rows):
            return
        expected = tuple(row["correlation_id"] for row in rows)
        if not expected:
            self._mark_unknown(batch_id, workspace_key, "batch_items_missing")
            return
        # A terminal cancellation can still have real partial outputs; retrieve
        # and account every returned record before exposing any of them.
        try:
            returned = tuple(provider.results(parent["provider_batch_id"], expected_ids=expected))
            returned_ids = [item.correlation_id for item in returned]
            if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(expected):
                raise ValueError("provider results do not match the persisted item set")
        except Exception as exc:  # noqa: BLE001 - no resubmission after a completed remote job
            if getattr(exc, "retryable", False):
                return
            self._mark_unknown(batch_id, workspace_key, "provider_results_invalid")
            return
        by_id = {item.correlation_id: item for item in returned}
        by_row = {row["correlation_id"]: row for row in rows}
        for key in expected:
            row = by_row[key]
            item_result = by_id[key]
            if row["state"] == "completed":
                continue
            if row["state"] == "received":
                stored = row["result_json"]
                usage = _usage_from_row(row)
                error_code = row["error_code"]
            else:
                outcome = self._receive_item(batch_id, workspace_key, key, item_result)
                if outcome is None:
                    continue
                stored, usage, error_code = outcome
            state = self._account_item(row["operation_id"], usage,
                                       success=(stored is not None and error_code is None),
                                       cancelled=parent["state"] == "cancelled")
            with self._transaction() as conn:
                updated = conn.execute(queries.get("set_provider_batch_item_result"), {
                    "batch_id": batch_id, "workspace_key": workspace_key,
                    "correlation_id": key, "state": state,
                    "result_json": Jsonb(stored) if stored is not None else None,
                    "error_code": error_code,
                    "usage_source": usage.source,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                }).fetchone()
                if updated and row["consumer_data"].get("trace_id"):
                    _insert_batch_model_call(conn, parent, row, stored, usage,
                                             error_code=error_code, state=state)

    def _receive_item(self, batch_id, workspace_key, key, item_result):
        """Durably store one returned item as `received`: (result, usage, error_code).

        Returns None when another collector already moved the item past `received`.
        If another collector received it first, that collector's stored outcome wins.
        """
        stored, usage, error_code = _normalize_item_result(item_result)
        encoded_result = _canonical(stored) if stored is not None else b"null"
        if len(encoded_result) > self.max_result_bytes:
            stored = None
            error_code = "item_result_too_large"
        with self._transaction() as conn:
            received = conn.execute(queries.get("set_provider_batch_item_received"), {
                "batch_id": batch_id, "workspace_key": workspace_key,
                "correlation_id": key, "result_json": Jsonb(stored) if stored is not None else None,
                "error_code": error_code, "usage_source": usage.source,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            }).fetchone()
            if received is None:
                current = conn.execute(queries.get("get_provider_batch_item"), {
                    "batch_id": batch_id, "correlation_id": key,
                }).fetchone()
                if current and current["state"] != "received":
                    return None
                if current:
                    return current["result_json"], _usage_from_row(current), current["error_code"]
        return stored, usage, error_code

    def _account_item(self, operation_id, usage, *, success, cancelled):
        conn = self._connect()
        try:
            status = self._budgets.get(conn, operation_id)
            call = conn.execute(queries.get("get_provider_batch_operation_call"), {
                "operation_id": operation_id,
            }).fetchone()
            if call is None:
                raise ProviderBatchUnknown("reserved batch item budget is missing")
            if call["status"] == "reserved":
                reservation = UsageReservation(
                    operation_id=operation_id, attempt_no=call["attempt_no"],
                    prompt_reserved_tokens=call["prompt_reserved_tokens"],
                    output_grant=call["output_reserved_tokens"],
                    timeout_seconds=status.per_call_timeout_seconds,
                    deadline_at=status.deadline_at, remaining_tokens=status.remaining_tokens,
                )
                self._budgets.account_usage(conn, reservation, usage=usage,
                    outcome="completed" if success else "cancelled" if cancelled else "failed")
                call = conn.execute(queries.get("get_provider_batch_operation_call"), {
                    "operation_id": operation_id,
                }).fetchone()
            elif usage.source == "provider" and usage.prompt_tokens is not None:
                if (call["measured_prompt_tokens"] != usage.prompt_tokens
                        or call["measured_completion_tokens"] != usage.completion_tokens):
                    return "unknown"
            if usage.source != "provider" or usage.prompt_tokens is None or usage.completion_tokens is None:
                return "unknown"
            if not success:
                return "failed"
            if call["status"] != "completed":
                return "unknown"
            return "completed"
        finally:
            conn.close()

    def _read_parent(self, batch_id, workspace_key):
        with self._transaction() as conn:
            row = conn.execute(queries.get("get_provider_batch"), {"batch_id": batch_id}).fetchone()
            _assert_workspace(row, workspace_key)
            return row

    def _read_items(self, batch_id):
        with self._transaction() as conn:
            return conn.execute(queries.get("list_provider_batch_items"),
                                {"batch_id": batch_id}).fetchall()

    def _mark_unknown(self, batch_id, workspace_key, failure_code, *, token=None):
        with self._transaction() as conn:
            if token is None:
                updated = conn.execute(queries.get("mark_provider_batch_unknown"), {
                    "batch_id": batch_id, "workspace_key": workspace_key,
                    "failure_code": failure_code,
                }).fetchone()
            else:
                # The token-specific update also prevents one submitter from
                # replacing another submitter's persisted state.
                updated = conn.execute(queries.get("mark_provider_batch_submission_unknown"), {
                    "batch_id": batch_id, "workspace_key": workspace_key,
                    "failure_code": failure_code, "submission_token": token,
                }).fetchone()
            if updated:
                conn.execute(queries.get("mark_provider_batch_reserved_items_unknown"), {
                    "batch_id": batch_id, "workspace_key": workspace_key,
                    "failure_code": failure_code,
                })

    @staticmethod
    def _assert_profile_current(conn, row):
        if row["profile_id"] is None:
            return
        current = conn.execute(queries.get("get_provider_batch_profile_for_share"), {
            "profile_id": row["profile_id"],
        }).fetchone()
        if (current is None or not current["enabled"]
                or current["revision"] != row["profile_revision"]
                or current["provider"] != row["provider"]
                or current["model_id"] != row["model_id"]):
            raise ProviderBatchProfileChanged("provider profile changed before batch use")


def _limits_dict(limits: OperationLimits) -> dict[str, Any]:
    return {"active_budget_seconds": limits.active_budget_seconds, "max_calls": limits.max_calls,
            "total_token_budget": limits.total_token_budget,
            "per_call_output_cap": limits.per_call_output_cap,
            "per_call_timeout_seconds": limits.per_call_timeout_seconds}


def _assert_workspace(row, workspace_key: str) -> None:
    if row is None or row["workspace_key"] != workspace_key:
        raise ProviderBatchNotFound("provider batch was not found")


def _usage_from_row(row) -> Usage:
    source = row["usage_source"]
    return Usage(row["prompt_tokens"], row["completion_tokens"], source=source)


def _normalize_item_result(item: BatchItemResult):
    usage = item.result.usage if item.result is not None else item.usage
    if item.error is not None or item.result is None:
        code = "provider_item_missing" if item.error and "no result" in item.error else "provider_item_failed"
        return None, usage, code
    result = item.result
    normalized = _request_result(result)
    if result.refusal or result.tool_calls or result.finish_reason.lower() in {
        "length", "max_tokens", "content_filter", "error", "incomplete", "failed", "tool_calls",
    }:
        return normalized, usage, "incomplete_or_unsupported_output"
    return normalized, usage, None


def _insert_batch_model_call(conn, parent, item, stored, usage, *, error_code, state):
    """Record an accounted native request once in the same transaction as its item."""
    metadata = item["consumer_data"]
    policy = metadata.get("request_policy", {})
    if not isinstance(policy, Mapping):
        policy = {}
    result = _result_from_record(stored) if stored is not None else None
    conn.execute(queries.get("insert_model_call"), {
        "model_call_id": uuid4(), "trace_id": UUID(str(metadata["trace_id"])),
        "provider": parent["provider"],
        "model_id": result.model if result and result.model else parent["model_id"],
        "status": "completed" if state == "completed" else "failed",
        "finish_reason": result.finish_reason if result else None,
        "request_id": result.request_id if result else None,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "usage_source": usage.source,
        # Native batch APIs do not report per-item wall-clock latency.
        "latency_ms": 0,
        "error_type": error_code,
        "operation_id": item["operation_id"],
        "sampling_policy": policy.get("sampling_policy", "greedy"),
        "reasoning_effort": policy.get("reasoning_effort"),
        "requested_output_tokens": policy.get("max_output_tokens"),
        "requested_timeout_seconds": policy.get("timeout_seconds"),
        "requested_temperature": policy.get("temperature"),
        "profile_id": parent["profile_id"],
        "profile_revision": parent["profile_revision"],
        "cached_prompt_tokens": usage.cached_prompt_tokens,
    })


def _first_consumer_value(rows, key: str) -> str | None:
    """The first non-empty routing hint any item's consumer data carries."""
    return next((row["consumer_data"][key] for row in rows if row["consumer_data"].get(key)), None)


def _snapshot(parent, rows) -> ProviderBatchSnapshot:
    items = []
    for row in rows:
        result = _result_from_record(row["result_json"]) if row["result_json"] is not None else None
        items.append(BatchItemSnapshot(
            row["correlation_id"], row["operation_id"], row["state"], result,
            row["error_code"], _usage_from_row(row), row["publication_state"],
            row["published_artifact_id"], row["published_revision_id"],
        ))
    return ProviderBatchSnapshot(
        batch_id=parent["batch_id"], workspace_key=parent["workspace_key"],
        provider=parent["provider"], model_id=parent["model_id"], state=parent["state"],
        provider_batch_id=parent["provider_batch_id"], input_file_id=parent["input_file_id"],
        profile_id=parent["profile_id"], profile_revision=parent["profile_revision"],
        item_count=len(items), total_count=parent["total_count"],
        completed_count=parent["completed_count"], failed_count=parent["failed_count"],
        items=tuple(items),
        route_role=_first_consumer_value(rows, "role"),
        route_data_scope=_first_consumer_value(rows, "data_scope"),
        cancel_requested=parent["cancel_requested"],
    )
