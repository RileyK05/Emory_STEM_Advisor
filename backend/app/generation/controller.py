"""Bounded provider-neutral tool conversations over the typed application registry."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
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
    ProviderTimeoutError,
    RateLimitError,
    TextMessage,
    ToolCall,
    ToolResultMessage,
    TransientProviderError,
    Usage,
)
from app.tools.executions import (
    ToolCallChanged,
    ToolCallLimitReached,
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
_TOOL_BUDGET_REFUSAL = json.dumps({
    "error": "The tool budget for this operation is spent. Answer now from the numbered "
             "context and the tool results above; do not request more tools."})
_CONTINUE_AFTER_OUTPUT_LIMIT = (
    "Your last response hit the output token limit before calling the required tool. "
    "Do not apologize, recap, or repeat yourself. Call the required tool now with the "
    "content you already have, and keep any hidden reasoning brief."
)
_TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "incomplete"})


class ToolConversationError(RuntimeError):
    """Sanitized failure for malformed or unsafe tool conversations."""


class ToolCallLimitExceeded(ToolConversationError):
    pass


class GenerationCancelled(ToolConversationError):
    pass


class ToolConversationController:
    def __init__(self, *, max_tool_calls: int = 4, max_provider_calls: int = 8,
                 max_result_bytes: int = MAX_TOOL_RESULT_BYTES,
                 max_output_continuations: int = 0,
                 max_repair_calls: int | None = None,
                 max_transient_retries: int = 2):
        if max_tool_calls < 0 or max_provider_calls < 1 or max_result_bytes < 1:
            raise ValueError("tool conversation limits must be positive")
        if max_output_continuations < 0 or max_transient_retries < 0:
            raise ValueError("output continuations and retries must not be negative")
        self.max_tool_calls = max_tool_calls
        self.max_provider_calls = max_provider_calls
        self.max_result_bytes = max_result_bytes
        self.max_output_continuations = max_output_continuations
        self.max_transient_retries = max_transient_retries
        self.max_repair_calls = (max_provider_calls if max_repair_calls is None
                                 else max_repair_calls)
        if not 1 <= self.max_repair_calls <= max_provider_calls:
            raise ValueError("repair call limit must fit inside the provider call limit")

    def run(self, provider, request: GenerationRequest, *, registry: ToolRegistry,
            scope: ToolScope, operation, ledger: ToolExecutionLedger | None = None,
            operation_id: UUID | None = None, workspace_key: str | None = None,
            scope_fingerprint: str | None = None,
            terminal_tool_names: frozenset[str] = frozenset(),
            cancellation: threading.Event | Callable[[], bool] | None = None,
            on_progress: Callable[[str, Mapping[str, Any]], None] | None = None,
            on_attempt: Callable[[GenerationRequest, GenerationResult | None, Exception | None], None] | None = None,
            before_provider_call: Callable[[], None] | None = None,
            ) -> GenerationResult:
        messages: list[Message] = list(request.messages)
        request_with_tools = replace(request, tools=registry.definitions)
        executed: dict[str, tuple[str, str, str]] = {}
        usages: list[Usage] = []
        tool_count = provider_count = truncated_count = repair_calls = 0
        transient_retries = 0
        continuing = False
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
            active_request = replace(request_with_tools, messages=tuple(messages))
            if not continuing:
                repair_calls += 1
                if repair_calls > self.max_repair_calls:
                    raise ToolCallLimitExceeded(
                        "the model did not return a final answer within the call limit")
            if provider_count == self.max_provider_calls - 1 and active_request.tools:
                # The last allowed call must finish: answer from the results
                # already gathered or call a terminal tool. Any other tool
                # request could never be followed up.
                if executed and not terminal_tool_names:
                    messages.append(TextMessage("user", _FINAL_CALL_NOTE))
                active_request = replace(active_request, messages=tuple(messages), tools=tuple(
                    tool for tool in active_request.tools if tool.name in terminal_tool_names))
            prepared = operation.prepare(active_request)
            provider_count += 1
            continuing = False
            try:
                result = _attempt(provider, prepared, operation, on_attempt=on_attempt,
                                  before_provider_call=before_provider_call)
            except (RateLimitError, TransientProviderError) as exc:
                # A dropped connection or rate limit is not a stage failure. Retry
                # the same request within the operation fence before surfacing it.
                if isinstance(exc, ProviderTimeoutError) and exc.request_sent:
                    raise
                transient_retries += 1
                if (transient_retries > self.max_transient_retries
                        or provider_count >= self.max_provider_calls):
                    raise
                retry_after = getattr(exc, "retry_after", None)
                delay = (min(float(retry_after), 8.0)
                         if retry_after is not None and retry_after > 0
                         else min(2.0 ** (transient_retries - 1), 8.0))
                time.sleep(delay)
                continuing = True
                continue
            transient_retries = 0
            usages.append(result.usage)
            finish_reason = (result.finish_reason or "").lower()
            if result.refusal:
                return replace(result, usage=_aggregate_usage(usages))
            if finish_reason in _TRUNCATED_FINISH_REASONS:
                # A truncated response is resumable: keep the text it produced
                # and ask it to finish the tool call instead of treating the
                # call as a failure. Opencode's loop does the same with a
                # synthetic user turn, bounded so a stuck model cannot run on.
                if truncated_count < self.max_output_continuations:
                    truncated_count += 1
                    if result.text:
                        messages.append(TextMessage("assistant", result.text))
                    messages.append(TextMessage("user", _CONTINUE_AFTER_OUTPUT_LIMIT))
                    continuing = True
                    continue
                if result.tool_calls:
                    raise ToolConversationError("provider did not complete the tool request")
                return replace(result, usage=_aggregate_usage(usages))
            if result.tool_calls and finish_reason in {
                "content_filter", "error", "failed",
            }:
                raise ToolConversationError("provider did not complete the tool request")
            if not result.tool_calls:
                return replace(result, usage=_aggregate_usage(usages))
            if tool_count + len(result.tool_calls) > self.max_tool_calls:
                # The operation's tool budget is spent. The fence holds: none of
                # these calls run. The model is told so and asked to answer from
                # the context and the tool results it already has, the same way
                # the last allowed provider call is handled.
                messages.append(AssistantToolMessage(result.tool_calls, result.text))
                for call in result.tool_calls:
                    _validate_call(call)
                    messages.append(ToolResultMessage(call.id, call.name, _TOOL_BUDGET_REFUSAL))
                continue
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
                    try:
                        output, ran = self._invoke(
                            call, registry=registry, scope=scope, ledger=ledger,
                            operation_id=operation_id, workspace_key=workspace_key,
                            scope_fingerprint=scope_fingerprint, on_progress=on_progress)
                    except ToolCallLimitReached:
                        # The operation's durable tool budget is spent, which
                        # an earlier provider attempt on the same request may
                        # have spent. The fence holds: the call does not run.
                        # The model is told and asked to answer from what it
                        # already has.
                        messages.append(ToolResultMessage(call.id, call.name,
                                                         _TOOL_BUDGET_REFUSAL))
                        continue
                    executed[call.id] = (call.name, encoded_args, output)
                    tool_count += ran
                if call.name in terminal_tool_names and not _is_refusal(output):
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


_DEFAULT_RETRY_DELAY_SECONDS = 1.0
_MAX_RETRY_DELAY_SECONDS = 30.0
_RETRY_JITTER_FRACTION = 0.25


def retry_transient(call, *, remaining_calls: int, remaining_seconds: float, check_cancelled,
                    max_retries: int = 1, clock=None, jitter: Callable[[], float] | None = None):
    """Re-run a rate-limited or transient provider call inside the operation fence.

    Backs off exponentially with jitter, honours ``Retry-After`` when it fits in
    the remaining operation time, fails at once when ``Retry-After`` exceeds the
    remaining time, and never replays a post-send timeout whose outcome is unknown.
    """
    now = (lambda: float(clock().timestamp())) if clock is not None else time.monotonic
    sleep = (
        (clock.sleep if callable(getattr(clock, "sleep", None)) else clock.advance)
        if clock is not None
        else time.sleep
    )
    sample_jitter = jitter if jitter is not None else random.random
    started = now()
    spent = 0.0
    retries = 0
    while True:
        try:
            return call()
        except (RateLimitError, TransientProviderError) as exc:
            if isinstance(exc, ProviderTimeoutError) and exc.request_sent:
                raise
            retries += 1
            left = remaining_seconds - max(spent, now() - started)
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None and retry_after > 0:
                delay = float(retry_after)
                if isinstance(exc, RateLimitError) and delay >= left:
                    raise
                if not isinstance(exc, RateLimitError):
                    delay = min(delay, _MAX_RETRY_DELAY_SECONDS)
            else:
                base = min(_DEFAULT_RETRY_DELAY_SECONDS * (2 ** (retries - 1)),
                           _MAX_RETRY_DELAY_SECONDS)
                delay = min(base * (1.0 + _RETRY_JITTER_FRACTION * float(sample_jitter())),
                            _MAX_RETRY_DELAY_SECONDS)
            if retries > max_retries or remaining_calls - 1 < retries or left <= delay:
                raise
            check_cancelled()
            sleep(delay)
            spent = max(spent + delay, now() - started)


def _attempt(provider, prepared: GenerationRequest, operation, *, on_attempt,
             before_provider_call=None) -> GenerationResult:
    """Call the provider and always account and audit the attempt before surfacing any failure."""
    result = error = None
    try:
        if before_provider_call is not None:
            before_provider_call()
        result = provider.generate(prepared)
    except Exception as exc:  # noqa: BLE001
        error = exc
    callback_error = None
    if on_attempt is not None:
        try:
            on_attempt(prepared, result, error)
        except Exception as exc:  # noqa: BLE001
            callback_error = exc
    operation.account(result, error)
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


def _is_refusal(output: str) -> bool:
    """A rejected call's result: the model is told why and may call again."""
    try:
        value = json.loads(output)
    except ValueError:
        return False
    return isinstance(value, dict) and set(value) == {"error"}


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
    prompt = sum(u.prompt_tokens for u in usages)
    completion = sum(u.completion_tokens for u in usages)
    # Cached tokens are known only if every round reported them; otherwise the
    # whole prompt is priced as uncached.
    cached = (None if any(u.cached_prompt_tokens is None for u in usages)
              else sum(u.cached_prompt_tokens for u in usages))
    return Usage(prompt, completion, prompt + completion, source="provider",
                 cached_prompt_tokens=cached)


def _progress(callback, event: str, call: ToolCall) -> None:
    if callback is not None:
        callback(event, {"call_id": call.id, "name": call.name})
