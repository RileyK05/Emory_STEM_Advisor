"""Provider-neutral typed LLM contracts. SDK types never cross this seam."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
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

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("generation timeout_seconds must be positive and finite")
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
class StreamEvent:
    type: Literal["text_delta", "tool_call_delta", "completed", "error"]
    text: str = ""
    result: GenerationResult | None = None
    error: ProviderError | None = None


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


def observed_usage(value) -> Usage:
    """Coerce an untrusted model-call outcome to a safely accountable Usage.

    Anything malformed becomes `Usage(source="unknown")`, which reserves rather
    than releases budget capacity.
    """
    if not isinstance(value, Usage):
        return Usage(source="unknown")
    if value.source not in {"provider", "estimated", "unknown"}:
        return Usage(source="unknown")
    counts = (value.prompt_tokens, value.completion_tokens)
    if (any(item is not None and (isinstance(item, bool) or not isinstance(item, int) or item < 0)
            for item in counts) or (counts[0] is None) != (counts[1] is None)):
        return Usage(source="unknown")
    if value.source == "provider" and counts[0] is None:
        return Usage(source="unknown")
    return value


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


class ProviderUnavailableError(ProviderError):
    retryable = True


class Provider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]: ...


def consume_stream(events, on_text) -> GenerationResult:
    """Drain a provider stream: forward text deltas, return the completed result.

    Anything but a well-formed stream ending in a completion is a provider error.
    """
    for event in iter(events):
        if not isinstance(event, StreamEvent):
            raise ProviderError("provider returned an invalid stream event", provider="unknown")
        if event.type == "text_delta" and event.text:
            on_text(event.text)
        elif event.type == "error":
            if event.error is not None:
                raise event.error
            raise ProviderError("provider stream failed", provider="unknown")
        elif event.type == "completed":
            if not isinstance(event.result, GenerationResult):
                raise ProviderError("provider returned an invalid completion", provider="unknown")
            return event.result  # Completion is terminal; preserve its measured usage.
    raise ProviderError("provider stream ended without a completed result", provider="unknown")
