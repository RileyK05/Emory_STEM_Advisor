"""Bounded provider-neutral tool conversations over the typed application registry."""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.llm.base import (
    AssistantToolMessage,
    GenerationRequest,
    GenerationResult,
    Message,
    TextMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
    consume_stream,
)
from app.tools.executions import (
    ToolCallChanged,
    ToolClaim,
    ToolExecutionIndeterminate,
    ToolExecutionInProgress,
    ToolExecutionLedger,
    ToolLedgerError,
)
from app.tools.registry import (
    ToolInputError,
    ToolPermissionDenied,
    ToolRegistry,
    ToolScope,
    UnknownTool,
)

MAX_TOOL_RESULT_BYTES = 64_000
MAX_TOOL_ARGUMENT_BYTES = 64_000
_FINAL_CALL_NOTE = ("The tool budget for this answer is used. Answer now from the numbered "
                    "context and the tool results above; do not request more tools.")


class ToolConversationError(RuntimeError):
    """Sanitized failure for malformed or unsafe tool conversations."""


class ToolCallLimitExceeded(ToolConversationError):
    pass


class GenerationCancelled(ToolConversationError):
    pass


class ToolConversationController:
    def __init__(self, *, max_tool_calls: int = 4, max_provider_calls: int = 8,
                 max_result_bytes: int = MAX_TOOL_RESULT_BYTES):
        if max_tool_calls < 0 or max_provider_calls < 1 or max_result_bytes < 1:
            raise ValueError("tool conversation limits must be positive")
        self.max_tool_calls = max_tool_calls
        self.max_provider_calls = max_provider_calls
        self.max_result_bytes = max_result_bytes

    def run(self, provider, request: GenerationRequest, *, registry: ToolRegistry,
            scope: ToolScope, operation, ledger: ToolExecutionLedger | None = None,
            operation_id: UUID | None = None, workspace_key: str | None = None,
            scope_fingerprint: str | None = None,
            terminal_tool_names: frozenset[str] = frozenset(),
            cancellation: threading.Event | Callable[[], bool] | None = None,
            on_progress: Callable[[str, Mapping[str, Any]], None] | None = None,
            on_provisional_text: Callable[[str], None] | None = None,
            on_provisional_reset: Callable[[], None] | None = None,
            final_repair: Callable[[GenerationResult], str | None] | None = None,
            required_tool_names: frozenset[str] = frozenset(),
            on_attempt: Callable[[GenerationRequest, GenerationResult | None, Exception | None], None] | None = None,
            before_provider_call: Callable[[], None] | None = None,
            ) -> GenerationResult:
        messages: list[Message] = list(request.messages)
        request_with_tools = replace(request, tools=registry.definitions)
        if not required_tool_names <= {tool.name for tool in registry.definitions}:
            raise ToolConversationError("required deterministic tool is unavailable in this scope")
        executed: dict[str, tuple[str, str, str]] = {}
        usages: list[Usage] = []
        tool_count = provider_count = 0
        repaired = False
        if ledger is not None and (operation_id is None or workspace_key is None):
            raise ValueError("durable tool execution requires operation and workspace IDs")
        if ledger is not None and scope.principal_id != workspace_key:
            raise ValueError("tool scope and execution workspace must match")
        scope_fingerprint = scope_fingerprint or hashlib.sha256(json.dumps(
            {"principal_id": scope.principal_id, "resource_ids": sorted(scope.resource_ids),
             "capabilities": sorted(str(capability) for capability in scope.capabilities)},
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        while provider_count < self.max_provider_calls:
            if _is_cancelled(cancellation):
                raise GenerationCancelled("generation was cancelled")
            pending = required_tool_names - {name for name, _args, _output in executed.values()}
            active_request = replace(request_with_tools, messages=tuple(messages))
            if pending:
                active_request = replace(active_request, tool_choice="required",
                    tools=tuple(tool for tool in registry.definitions
                                if provider_count == 0 or tool.name in pending))
            elif provider_count == self.max_provider_calls - 1 and active_request.tools:
                # The last allowed call must finish: answer from the results
                # already gathered or call a terminal tool. Any other tool
                # request could never be followed up.
                if executed and not terminal_tool_names:
                    messages.append(TextMessage("user", _FINAL_CALL_NOTE))
                active_request = replace(active_request, messages=tuple(messages), tools=tuple(
                    tool for tool in active_request.tools if tool.name in terminal_tool_names))
            prepared = operation.prepare(active_request)
            provider_count += 1
            result = _attempt(provider, prepared, operation, on_attempt=on_attempt,
                              on_provisional_text=on_provisional_text,
                              on_provisional_reset=on_provisional_reset,
                              before_provider_call=before_provider_call)
            usages.append(result.usage)
            if repaired and result.tool_calls:
                raise ToolConversationError("answer rewrite requested an unexecuted tool")
            if result.tool_calls and (result.finish_reason or "").lower() in {
                "length", "max_tokens", "content_filter", "error", "incomplete", "failed",
            }:
                raise ToolConversationError("provider did not complete the tool request")
            if result.refusal or not result.tool_calls:
                if pending and not result.refusal:
                    if on_provisional_reset is not None:
                        on_provisional_reset()
                    messages.extend([TextMessage("assistant", result.text), TextMessage("user",
                        "No deterministic sequence result has been executed. Invoke the required "
                        "scoped planner before answering. Do not infer or claim a sequence yourself.")])
                    continue
                hint = final_repair(result) if final_repair and not repaired else None
                if hint and provider_count < self.max_provider_calls:
                    repaired = True
                    if on_provisional_reset is not None:
                        on_provisional_reset()
                    messages.extend([TextMessage("assistant", result.text), TextMessage("user", hint)])
                    # Retain executed tool messages, but the rewrite may not execute more tools.
                    request_with_tools = replace(request_with_tools, tools=())
                    continue
                return replace(result, usage=_aggregate_usage(usages))
            if tool_count + len(result.tool_calls) > self.max_tool_calls:
                raise ToolCallLimitExceeded("the model requested more tools than this operation allows")
            terminal_calls = [call for call in result.tool_calls if call.name in terminal_tool_names]
            if terminal_calls and (len(result.tool_calls) != 1 or len(terminal_calls) != 1):
                raise ToolConversationError("terminal artifact tool must be the only requested tool")
            messages.append(AssistantToolMessage(result.tool_calls, result.text))
            for call in result.tool_calls:
                if _is_cancelled(cancellation):
                    raise GenerationCancelled("generation was cancelled")
                _validate_call(call)
                encoded_args = _canonical_arguments(call.arguments)
                prior = executed.get(call.id)
                if prior is not None:
                    old_name, old_args, output = prior
                    if old_name != call.name or old_args != encoded_args:
                        raise ToolCallChanged("a repeated tool call ID changed its request")
                    _progress(on_progress, "tool_reused", call)
                else:
                    output, ran = self._invoke(
                        call, registry=registry, scope=scope, ledger=ledger,
                        operation_id=operation_id, workspace_key=workspace_key,
                        scope_fingerprint=scope_fingerprint, on_progress=on_progress)
                    executed[call.id] = (call.name, encoded_args, output)
                    tool_count += ran
                if call.name in terminal_tool_names:
                    return GenerationResult(text=output, finish_reason="stop",
                        usage=_aggregate_usage(usages), request_id=result.request_id, model=result.model)
                messages.append(ToolResultMessage(call.id, call.name, output))
        raise ToolCallLimitExceeded("the model did not return a final answer within the call limit")

    def _invoke(self, call: ToolCall, *, registry, scope, ledger, operation_id, workspace_key,
                scope_fingerprint, on_progress) -> tuple[str, bool]:
        """Run one tool call: (output, whether the tool actually ran rather than being replayed)."""
        _progress(on_progress, "tool_started", call)
        claim: ToolClaim | None = None
        if ledger is not None:
            outcome = ledger.claim_or_replay(operation_id=operation_id,
                workspace_key=workspace_key, call_id=call.id, tool_id=call.name,
                arguments=call.arguments, scope_fingerprint=scope_fingerprint)
            if outcome.replayed:
                if outcome.replay_json is None:
                    raise ToolConversationError("completed tool result is unavailable")
                _progress(on_progress, "tool_replayed", call)
                return outcome.replay_json, False
            claim = outcome.claim
        try:
            try:
                output = _tool_output(registry.invoke(call.name, call.arguments, scope),
                                      self.max_result_bytes)
            except (UnknownTool, ToolPermissionDenied, ToolInputError) as exc:
                # Rejected before the tool ran. The same scope and arguments are
                # rejected again, so the safe refusal is a replayable result the
                # model can correct rather than an indeterminate execution.
                output = _tool_output({"error": str(exc)}, self.max_result_bytes)
            if claim is not None:
                ledger.complete(claim, output)
        except Exception as exc:
            if claim is not None:
                ledger.mark_unknown(claim)
            if isinstance(exc, (ToolExecutionInProgress, ToolExecutionIndeterminate,
                                ToolCallChanged, ToolLedgerError, GenerationCancelled)):
                raise
            if ledger is not None:
                raise ToolConversationError("tool execution failed; retry is blocked") from None
            output = _tool_output({"error": "tool execution failed"}, self.max_result_bytes)
        _progress(on_progress, "tool_completed", call)
        return output, True


def _generate(provider, prepared: GenerationRequest, on_provisional_text) -> GenerationResult:
    """One provider call, streamed when the caller wants provisional text and the provider can."""
    stream = getattr(provider, "stream", None) if on_provisional_text is not None else None
    if prepared.tools and not getattr(provider, "stream_supports_tools", True):
        result = provider.generate(prepared)
        if on_provisional_text is not None and result.text and not result.tool_calls:
            on_provisional_text(result.text)
        return result
    if not callable(stream):
        return provider.generate(prepared)
    return consume_stream(stream(prepared), on_provisional_text)


def _attempt(provider, prepared: GenerationRequest, operation, *, on_attempt, on_provisional_text,
             on_provisional_reset, before_provider_call=None) -> GenerationResult:
    """Call the provider and always account and audit the attempt before surfacing any failure."""
    result = error = None
    try:
        if before_provider_call is not None:
            before_provider_call()
        result = _generate(provider, prepared, on_provisional_text)
    except Exception as exc:  # noqa: BLE001
        error = exc
    callback_error = None
    if on_attempt is not None:
        try:
            on_attempt(prepared, result, error)
        except Exception as exc:  # noqa: BLE001
            callback_error = exc
    try:
        operation.account(result, error)
    finally:
        if result is not None and result.tool_calls and on_provisional_reset is not None:
            on_provisional_reset()
    if callback_error is not None:
        raise callback_error
    if error is not None:
        raise error
    return result


def _validate_call(call: ToolCall) -> None:
    if not call.id or len(call.id) > 256 or not call.name or len(call.name) > 128:
        raise ToolConversationError("provider returned an invalid tool call")
    if not isinstance(call.arguments, Mapping):
        raise ToolConversationError("provider returned invalid tool arguments")
    if len(_canonical_arguments(call.arguments).encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
        raise ToolConversationError("tool arguments exceed the size limit")


def _canonical_arguments(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"),
                          sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ToolConversationError("provider returned invalid tool arguments") from None


def _is_cancelled(cancellation) -> bool:
    if cancellation is None:
        return False
    return cancellation.is_set() if hasattr(cancellation, "is_set") else bool(cancellation())


def _tool_output(value: Any, max_bytes: int) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        output = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        output = json.dumps({"error": "tool returned an unsupported value"})
    if len(output.encode("utf-8")) > max_bytes:
        raise ToolConversationError("tool result exceeded the size limit")
    return output


def _aggregate_usage(usages: list[Usage]) -> Usage:
    if not usages or any(u.source != "provider" or u.prompt_tokens is None
                         or u.completion_tokens is None for u in usages):
        return Usage(source="unknown")
    prompt = sum(u.prompt_tokens or 0 for u in usages)
    completion = sum(u.completion_tokens or 0 for u in usages)
    return Usage(prompt, completion, source="provider")


def _progress(callback, event: str, call: ToolCall) -> None:
    if callback is not None:
        callback(event, {"call_id": call.id, "name": call.name})
