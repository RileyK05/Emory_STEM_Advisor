"""Durable, fail-closed idempotency ledger for model-requested tools."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.db import queries
from app.db.connection import connection_transaction
from app.util.digest import canonical_json_bytes
from app.util.workspace import is_valid_workspace_key

MAX_ARGUMENT_BYTES = 64_000
MAX_RESULT_BYTES = 64_000


class ToolLedgerError(RuntimeError):
    """Base class for safe ledger failures."""


class ToolCallChanged(ToolLedgerError):
    pass


class ToolExecutionInProgress(ToolLedgerError):
    pass


class ToolExecutionIndeterminate(ToolLedgerError):
    pass


@dataclass(frozen=True)
class ToolClaim:
    operation_id: UUID
    workspace_key: str
    call_id: str
    tool_id: str
    arguments_sha256: str
    scope_fingerprint: str


@dataclass(frozen=True)
class ClaimResult:
    claim: ToolClaim | None
    replay_json: str | None = None

    @property
    def replayed(self) -> bool:
        return self.claim is None


class ToolExecutionLedger:
    """Uses a fresh short transaction per transition; handlers run outside DB locks."""

    def __init__(self, connect: Callable[[], Any], *, max_argument_bytes: int = MAX_ARGUMENT_BYTES,
                 max_result_bytes: int = MAX_RESULT_BYTES, max_tool_calls: int = 4):
        if (max_argument_bytes < 1 or max_result_bytes < 1 or max_result_bytes > MAX_RESULT_BYTES
                or not 0 <= max_tool_calls <= 100):
            raise ValueError("tool ledger bounds are invalid")
        self._connect = connect
        self.max_argument_bytes = max_argument_bytes
        self.max_result_bytes = max_result_bytes
        self.max_tool_calls = max_tool_calls

    def claim_or_replay(self, *, operation_id: UUID, workspace_key: str, call_id: str,
                        tool_id: str, arguments: Mapping[str, Any],
                        scope_fingerprint: str | None = None) -> ClaimResult:
        _validate_identity(workspace_key, call_id, tool_id)
        encoded = canonical_arguments(arguments)
        if len(encoded) > self.max_argument_bytes:
            raise ToolLedgerError("tool arguments exceed the ledger size limit")
        digest = hashlib.sha256(encoded).hexdigest()
        scope_fingerprint = scope_fingerprint or hashlib.sha256(b"").hexdigest()
        if len(scope_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in scope_fingerprint):
            raise ToolLedgerError("tool execution scope fingerprint is invalid")
        params = {"operation_id": operation_id, "workspace_key": workspace_key,
                  "call_id": call_id, "tool_id": tool_id, "arguments_sha256": digest,
                  "scope_fingerprint": scope_fingerprint, "max_tool_calls": self.max_tool_calls}
        with self._transaction() as conn:
            conn.execute(queries.get("ensure_tool_execution_operation"), params)
            operation = conn.execute(queries.get("lock_tool_execution_operation"), params).fetchone()
            if operation is None:
                raise ToolLedgerError("tool operation scope could not be established")
            if (operation["workspace_key"] != workspace_key
                    or operation["scope_fingerprint"].strip() != scope_fingerprint
                    or operation["max_tool_calls"] != self.max_tool_calls):
                raise ToolCallChanged("tool operation context or limits changed")
            inserted = conn.execute(queries.get("insert_tool_execution_claim"), params).fetchone()
            if inserted:
                claimed = conn.execute(queries.get("increment_tool_execution_claims"), params).fetchone()
                if claimed is None:
                    raise ToolLedgerError("tool call limit has been reached")
                return ClaimResult(ToolClaim(operation_id, workspace_key, call_id, tool_id, digest,
                                             scope_fingerprint))
            row = conn.execute(queries.get("get_tool_execution"), params).fetchone()
            if row is None:
                raise ToolLedgerError("tool execution claim could not be read")
            if (row["workspace_key"] != workspace_key or row["tool_id"] != tool_id
                    or row["arguments_sha256"].strip() != digest
                    or row["scope_fingerprint"].strip() != scope_fingerprint):
                raise ToolCallChanged("a repeated tool call ID changed its request")
            if row["status"] == "completed":
                return ClaimResult(None, row["result_json"])
            if row["status"] == "unknown":
                raise ToolExecutionIndeterminate("tool execution may have completed; retry is blocked")
            raise ToolExecutionInProgress("tool execution is already claimed; retry is blocked")

    def complete(self, claim: ToolClaim, result_json: str) -> None:
        if not isinstance(result_json, str) or len(result_json.encode("utf-8")) > self.max_result_bytes:
            self.mark_unknown(claim)
            raise ToolLedgerError("tool result exceeds the ledger size limit")
        params = {"operation_id": claim.operation_id, "workspace_key": claim.workspace_key,
                  "call_id": claim.call_id, "tool_id": claim.tool_id,
                  "arguments_sha256": claim.arguments_sha256,
                  "scope_fingerprint": claim.scope_fingerprint, "result_json": result_json}
        with self._transaction() as conn:
            row = conn.execute(queries.get("complete_tool_execution"), params).fetchone()
            if row is None:
                raise ToolLedgerError("tool execution could not be completed")

    def mark_unknown(self, claim: ToolClaim) -> None:
        params = {"operation_id": claim.operation_id, "workspace_key": claim.workspace_key,
                  "call_id": claim.call_id}
        with self._transaction() as conn:
            conn.execute(queries.get("mark_tool_execution_unknown"), params)

    def _transaction(self):
        return connection_transaction(self._connect)


def canonical_arguments(arguments: Mapping[str, Any]) -> bytes:
    if not isinstance(arguments, Mapping):
        raise ToolLedgerError("tool arguments must be an object")
    try:
        return canonical_json_bytes(dict(arguments))
    except (TypeError, ValueError):
        raise ToolLedgerError("tool arguments are not valid JSON") from None


def _validate_identity(workspace_key: str, call_id: str, tool_id: str) -> None:
    if not is_valid_workspace_key(workspace_key):
        raise ToolLedgerError("tool execution workspace is invalid")
    if not isinstance(call_id, str) or not 1 <= len(call_id) <= 256:
        raise ToolLedgerError("provider tool call ID is invalid")
    if not isinstance(tool_id, str) or not 1 <= len(tool_id) <= 128:
        raise ToolLedgerError("tool ID is invalid")
