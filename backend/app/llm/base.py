"""Provider-neutral typed LLM contracts. SDK types never cross this seam."""

from __future__ import annotations

import contextlib
import math
import re
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

_CACHE_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")

UsageSource = Literal["provider", "estimated", "unknown"]
MessageRole = Literal["system", "developer", "user", "assistant"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class TextMessage:
    """Typed text turn; mapping-based {role, content} messages remain accepted."""

    role: MessageRole
    content: str


@dataclass(frozen=True)
class ImageMessage:
    """A user turn carrying one screenshot for a vision-capable model."""

    role: Literal["user"] = "user"
    content: str = ""
    mime_type: str = "image/png"
    data: str = ""  # base64, never a URL or filesystem path

    def __post_init__(self) -> None:
        if self.role != "user":
            raise ValueError("image messages must use the user role")
        if self.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("image message mime type is unsupported")
        if not self.data or len(self.data) > 12_000_000:
            raise ValueError("image message data is empty or too large")


@dataclass(frozen=True)
class AssistantToolMessage:
    """Assistant message that requests one or more provider-neutral tools."""

    tool_calls: tuple[ToolCall, ...]
    content: str = ""


@dataclass(frozen=True)
class ToolResultMessage:
    """Tool output tied to the exact call identifier issued by the model."""

    tool_call_id: str
    name: str
    content: str


Message = TextMessage | ImageMessage | AssistantToolMessage | ToolResultMessage | dict[str, Any]


def normalize_message(message: Message) -> dict[str, Any]:
    """Return an SDK-neutral mapping with typed tool envelopes serialized."""
    if isinstance(message, TextMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, ImageMessage):
        content = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        content.append({
            "type": "image",
            "mime_type": message.mime_type,
            "data": message.data,
        })
        return {"role": message.role, "content": content}
    if isinstance(message, AssistantToolMessage):
        return {"role": "assistant", "content": message.content,
                "tool_calls": [{"id": c.id, "name": c.name,
                                "arguments": dict(c.arguments)} for c in message.tool_calls]}
    if isinstance(message, ToolResultMessage):
        return {"role": "tool", "tool_call_id": message.tool_call_id,
                "name": message.name, "content": message.content}
    if isinstance(message, dict):
        return dict(message)
    raise TypeError("generation messages must use a supported typed or mapping envelope")


@dataclass(frozen=True)
class GenerationRequest:
    messages: Sequence[Message]
    model: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: ReasoningEffort | None = None
    response_schema: Mapping[str, Any] | None = None
    tools: Sequence[ToolDefinition] = ()
    tool_choice: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    # Provider-neutral prompt-cache routing key for requests that share a long
    # stable prefix. Adapters map it to a verified control or ignore it; it never
    # changes the prompt, and callers derive it from the prefix content so
    # different scopes cannot share a key.
    cache_key: str | None = None
    # Remaining operation time budget used to bound local concurrency-slot waits.
    deadline_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("generation timeout_seconds must be positive and finite")
        if self.deadline_seconds is not None and (
            not math.isfinite(self.deadline_seconds) or self.deadline_seconds <= 0
        ):
            raise ValueError("generation deadline_seconds must be positive and finite")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max"
        }:
            raise ValueError("reasoning_effort is unsupported")
        if self.cache_key is not None and not _CACHE_KEY.fullmatch(self.cache_key):
            raise ValueError("cache_key must be 1-64 letters, digits, '-' or '_'")
        if self.reasoning_effort not in (None, "none") and self.temperature is not None:
            raise ValueError("temperature cannot be combined with reasoning effort")


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    source: UsageSource = "unknown"
    # Prompt tokens the provider reports serving from its prompt cache; part of
    # prompt_tokens, never in addition. None means not reported, not zero.
    cached_prompt_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.source not in {"provider", "estimated", "unknown"}:
            raise ValueError("usage source is invalid")
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.source == "provider" and (self.prompt_tokens is None or self.completion_tokens is None):
            raise ValueError("provider usage must include prompt and completion token counts")
        if self.cached_prompt_tokens is not None and (
                self.prompt_tokens is None or self.cached_prompt_tokens > self.prompt_tokens):
            raise ValueError("cached prompt tokens must be part of the prompt token count")
        if self.total_tokens is None and self.prompt_tokens is not None and self.completion_tokens is not None:
            object.__setattr__(self, "total_tokens", self.prompt_tokens + self.completion_tokens)


@dataclass(frozen=True)
class GenerationResult:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "unknown"
    usage: Usage = field(default_factory=Usage)
    refusal: str | None = None
    request_id: str | None = None
    model: str | None = None
    raw: Any = field(default=None, repr=False, compare=False)


class ProviderError(RuntimeError):
    """Sanitized base error. Do not include request headers or credentials."""

    retryable = False

    def __init__(self, message: str, *, provider: str, request_id: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.request_id = request_id
        # Streaming SDKs can fail after reporting billable usage. Preserve only
        # validated provider-neutral counters for accounting; never SDK payloads.
        self.usage: Usage | None = None


class AuthenticationError(ProviderError):
    pass


class InvalidRequestError(ProviderError):
    pass


class RateLimitError(ProviderError):
    retryable = True

    def __init__(self, message: str, *, provider: str, retry_after: float | None = None,
                 request_id: str | None = None):
        super().__init__(message, provider=provider, request_id=request_id)
        self.retry_after = retry_after


class TransientProviderError(ProviderError):
    retryable = True


class ProviderTimeoutError(TransientProviderError):
    """A single bounded provider request exceeded its timeout."""

    def __init__(self, message: str, *, provider: str, request_id: str | None = None,
                 request_sent: bool = True):
        super().__init__(message, provider=provider, request_id=request_id)
        self.request_sent = request_sent


class ProviderGate:
    """Per-process cap on model calls in flight for one provider."""

    def __init__(self, limit: int, *, provider: str = "unknown", clock=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("provider concurrency limit must be a positive integer")
        self._limit = limit
        self._provider = provider
        self._clock = clock
        self._in_flight = 0
        self._waiting = 0
        self._cond = threading.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        with self._cond:
            return self._in_flight

    @property
    def waiting(self) -> int:
        with self._cond:
            return self._waiting

    def set_limit(self, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("provider concurrency limit must be a positive integer")
        with self._cond:
            self._limit = limit
            self._cond.notify_all()

    def wait_for_waiters(self, count: int, *, timeout: float = 2.0) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._waiting >= count, timeout=timeout)

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock().timestamp())
        return time.monotonic()

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    @contextlib.contextmanager
    def acquire(self, request: GenerationRequest) -> Iterator[GenerationRequest]:
        wait_budget = (
            request.deadline_seconds
            if request.deadline_seconds is not None
            else request.timeout_seconds
        )
        unsubscribe = None
        with self._cond:
            if self._in_flight < self._limit:
                self._in_flight += 1
                effective = request
            else:
                started = self._now()
                deadline = None if wait_budget is None else started + wait_budget
                subscribe = getattr(self._clock, "subscribe", None)
                if callable(subscribe):
                    unsubscribe = subscribe(self._wake)
                self._waiting += 1
                self._cond.notify_all()
                try:
                    while self._in_flight >= self._limit:
                        if deadline is not None:
                            remaining = deadline - self._now()
                            if remaining <= 0:
                                raise ProviderTimeoutError(
                                    f"{self._provider} concurrency wait timed out",
                                    provider=self._provider,
                                    request_sent=False,
                                )
                        if self._clock is not None:
                            self._cond.wait(timeout=5.0)
                        else:
                            self._cond.wait(timeout=remaining if deadline is not None else None)
                    if deadline is not None and self._now() >= deadline:
                        raise ProviderTimeoutError(
                            f"{self._provider} concurrency wait timed out",
                            provider=self._provider,
                            request_sent=False,
                        )
                    self._in_flight += 1
                    if wait_budget is not None:
                        remaining_budget = wait_budget - max(0.0, self._now() - started)
                        timeout = (
                            remaining_budget
                            if request.timeout_seconds is None
                            else min(request.timeout_seconds, remaining_budget)
                        )
                        effective = replace(
                            request,
                            timeout_seconds=timeout,
                            deadline_seconds=(
                                remaining_budget if request.deadline_seconds is not None else None
                            ),
                        )
                    else:
                        effective = request
                finally:
                    self._waiting -= 1
                    if unsubscribe is not None:
                        unsubscribe()
        try:
            yield effective
        finally:
            with self._cond:
                self._in_flight -= 1
                self._cond.notify_all()


class ProviderUnavailableError(ProviderError):
    retryable = True


class Provider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


def provider_failure_detail(exc: ProviderError, subject: str) -> str:
    """A 503 detail that blames settings only when a retry would not help."""
    if isinstance(exc, RateLimitError):
        retry_after = getattr(exc, "retry_after", None)
        return (f"{subject} is rate-limited"
                + (f"; retry in {round(retry_after)}s." if retry_after else "; retry shortly."))
    if isinstance(exc, TransientProviderError) or exc.retryable:
        return f"{subject} did not respond in time or dropped the connection; retry shortly."
    return f"{subject} connection failed; check provider settings."
