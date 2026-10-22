"""Native SDK adapters. Imported only by app.llm.factory."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.llm.base import (
    AuthenticationError,
    GenerationRequest,
    GenerationResult,
    InvalidRequestError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    StreamEvent,
    ToolCall,
    TransientProviderError,
    Usage,
    normalize_message,
)


def _usage(prompt: Any, completion: Any) -> Usage:
    if prompt is None or completion is None:
        return Usage(source="unknown")
    return Usage(int(prompt), int(completion), source="provider")


def _chat_usage(raw: Any) -> Usage:
    """Chat Completions usage, keeping a reported prompt-cache hit count."""
    usage = _usage(_get(raw, "prompt_tokens"), _get(raw, "completion_tokens"))
    cached = _get(_get(raw, "prompt_tokens_details"), "cached_tokens")
    if (usage.source == "provider" and isinstance(cached, int) and not isinstance(cached, bool)
            and 0 <= cached <= usage.prompt_tokens):
        return replace(usage, cached_prompt_tokens=cached)
    return usage


def _usage_from_tokens(raw: Any) -> Usage:
    """Usage from an SDK usage object or mapping with input/output/total token counts."""
    prompt, completion = _get(raw, "input_tokens"), _get(raw, "output_tokens")
    if prompt is None or completion is None:
        return Usage(source="unknown")
    total = _get(raw, "total_tokens")
    return Usage(int(prompt), int(completion), int(total) if total is not None else None, "provider")


def _error(provider: str, exc: Exception) -> ProviderError:
    error: ProviderError
    if "timeout" in type(exc).__name__.lower() or isinstance(exc, TimeoutError):
        error = ProviderTimeoutError(f"{provider} request timed out", provider=provider,
                                     request_id=getattr(exc, "request_id", None))
        error.usage = _error_usage(exc)
        return error
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    request_id = getattr(exc, "request_id", None)
    # Keep provider message only when it is a bounded public API message. SDK
    # exception strings can include request data, so never retain them verbatim.
    message = f"{provider} request failed (HTTP {status})" if status else f"{provider} request failed"
    if status == 401 or status == 403:
        error = AuthenticationError(message, provider=provider, request_id=request_id)
    elif status == 429:
        retry = getattr(exc, "response", None)
        headers = getattr(retry, "headers", {}) if retry else {}
        error = RateLimitError(message, provider=provider,
                               retry_after=_retry_after(headers), request_id=request_id)
    elif status in (408, 409, 425) or (isinstance(status, int) and status >= 500):
        error = TransientProviderError(message, provider=provider, request_id=request_id)
    elif isinstance(status, int) and 400 <= status < 500:
        error = InvalidRequestError(message, provider=provider, request_id=request_id)
    else:
        error = TransientProviderError(message, provider=provider, request_id=request_id)
    error.usage = _error_usage(exc)
    return error


def _error_usage(exc: Exception) -> Usage | None:
    raw = getattr(exc, "usage", None)
    if isinstance(raw, Usage):
        return raw
    if raw is None:
        return None
    prompt = getattr(raw, "prompt_tokens", None) or getattr(raw, "input_tokens", None)
    completion = getattr(raw, "completion_tokens", None) or getattr(raw, "output_tokens", None)
    if isinstance(raw, Mapping):
        prompt = raw.get("prompt_tokens", raw.get("input_tokens", prompt))
        completion = raw.get("completion_tokens", raw.get("output_tokens", completion))
    if prompt is None and completion is None:
        return None
    try:
        prompt = int(prompt) if prompt is not None else None
        completion = int(completion) if completion is not None else None
        return Usage(prompt_tokens=prompt, completion_tokens=completion,
                     source="provider" if prompt is not None and completion is not None else "unknown")
    except (TypeError, ValueError):
        return None


def _retry_after(headers: Mapping[str, Any]) -> float | None:
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _split_messages(messages):
    normalized = [normalize_message(m) for m in messages]
    system = "\n".join(m.get("content", "") for m in normalized
                        if m.get("role") in ("system", "developer"))
    rest = [m for m in normalized if m.get("role") not in ("system", "developer")]
    return system, rest


def _responses_input(messages):
    output = []
    for item in (normalize_message(m) for m in messages):
        role = item.get("role")
        calls = item.get("tool_calls") or ()
        if role == "tool":
            output.append({"type": "function_call_output", "call_id": item["tool_call_id"],
                           "output": item.get("content", "")})
            continue
        if role not in {"system", "developer", "user", "assistant"}:
            raise InvalidRequestError("unsupported message role", provider="openai")
        content = item.get("content")
        if isinstance(content, list):
            output.append({"role": role, "content": [
                {"type": "input_text", "text": part.get("text", "")}
                if part.get("type") == "text" else
                {"type": "input_image",
                 "image_url": f"data:{part['mime_type']};base64,{part['data']}"}
                for part in content
            ]})
        elif content:
            output.append({"role": role, "content": content})
        for call in calls:
            output.append({"type": "function_call", "call_id": call["id"],
                           "name": call["name"],
                           "arguments": json.dumps(call["arguments"], separators=(",", ":"),
                                                    ensure_ascii=False)})
    return output


def _anthropic_messages(messages):
    normalized = [normalize_message(m) for m in messages]
    converted = []
    for item in normalized:
        role = item.get("role")
        if role in {"system", "developer"}:
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": item["tool_call_id"],
                     "content": item.get("content", "")}
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if role not in {"assistant", "user"}:
            raise InvalidRequestError("unsupported message role", provider="anthropic")
        content = []
        raw_content = item.get("content")
        if isinstance(raw_content, list):
            for part in raw_content:
                if part.get("type") == "text":
                    content.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image":
                    content.append({"type": "image", "source": {
                        "type": "base64", "media_type": part["mime_type"],
                        "data": part["data"],
                    }})
        elif raw_content:
            content.append({"type": "text", "text": raw_content})
        if role == "assistant":
            content.extend({"type": "tool_use", "id": call["id"], "name": call["name"],
                            "input": call["arguments"]} for call in item.get("tool_calls", ()))
        converted.append({"role": role, "content": content or ""})
    return converted


def _google_contents(messages):
    contents = []
    for item in (normalize_message(m) for m in messages):
        role = item.get("role")
        if role in {"system", "developer"}:
            continue
        native_role = "model" if role == "assistant" else "user"
        parts = []
        if role == "tool":
            try:
                response = json.loads(item.get("content", ""))
            except json.JSONDecodeError:
                response = {"result": item.get("content", "")}
            parts.append({"function_response": {"id": item["tool_call_id"],
                           "name": item.get("name", ""), "response": response}})
        else:
            if role not in {"assistant", "user"}:
                raise InvalidRequestError("unsupported message role", provider="google")
            raw_content = item.get("content")
            if isinstance(raw_content, list):
                for part in raw_content:
                    if part.get("type") == "text":
                        parts.append({"text": part.get("text", "")})
                    elif part.get("type") == "image":
                        parts.append({"inline_data": {
                            "mime_type": part["mime_type"],
                            "data": base64.b64decode(part["data"], validate=True),
                        }})
            elif raw_content:
                parts.append({"text": raw_content})
            if role == "assistant":
                parts.extend({"function_call": {"id": c["id"], "name": c["name"],
                                                  "args": c["arguments"]}}
                             for c in item.get("tool_calls", ()))
        contents.append({"role": native_role, "parts": parts})
    return contents


def _chat_messages(messages):
    converted = []
    for item in (normalize_message(m) for m in messages):
        role = item.get("role")
        if role == "tool":
            converted.append({"role": "tool", "tool_call_id": item["tool_call_id"],
                              "content": item.get("content", "")})
            continue
        raw_content = item.get("content")
        if isinstance(raw_content, list):
            content = []
            for part in raw_content:
                if part.get("type") == "text":
                    content.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image":
                    content.append({"type": "image_url", "image_url": {
                        "url": f"data:{part['mime_type']};base64,{part['data']}",
                    }})
            message = {"role": role, "content": content}
        else:
            message = {"role": role, "content": raw_content or None}
        if item.get("tool_calls"):
            message["tool_calls"] = [{"id": call["id"], "type": "function",
                "function": {"name": call["name"], "arguments": json.dumps(
                    call["arguments"], separators=(",", ":"), ensure_ascii=False)}}
                for call in item["tool_calls"]]
        converted.append(message)
    return converted


def _tools(request: GenerationRequest):
    return [{"type": "function", "name": t.name, "description": t.description,
             "parameters": dict(t.input_schema), "strict": True}
            for t in request.tools]


def _openai_text_format(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"format": {"type": "json_schema", "name": "response", "strict": True,
                       "schema": dict(schema)}}


def _anthropic_tools(tools) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description,
             "input_schema": dict(t.input_schema)} for t in tools]


def _anthropic_output_config(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"format": {"type": "json_schema", "schema": dict(schema)}}


def _google_tools(tools, types) -> list:
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(name=t.name, description=t.description,
                                  parameters=dict(t.input_schema)) for t in tools])]


def _google_tool_config(tool_choice: str, types):
    if tool_choice == "required":
        tool_choice = "any"
    return types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
        mode=tool_choice.upper()))


class _SDKProvider:
    """Shared construction and the `chat()` convenience over `generate()`."""

    # Current SDK stream decoders handle text, not incremental tool arguments.
    # The controller must use complete typed results for tool conversations.
    stream_supports_tools = False

    def __init__(self, client, model: str):
        self.client, self.model = client, model


class OpenAIProvider(_SDKProvider):
    def generate(self, request: GenerationRequest) -> GenerationResult:
        payload: dict[str, Any] = {"model": request.model or self.model,
                                   "input": _responses_input(request.messages), "store": False}
        _apply_responses_sampling(payload, request)
        if request.tools:
            payload["tools"] = _tools(request)
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.response_schema:
            payload["text"] = _openai_text_format(request.response_schema)
        try:
            response = self.client.responses.create(**payload, **_timeout(request))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("openai", exc) from None
        calls, refusal, texts = [], None, []
        for item in getattr(response, "output", ()) or ():
            typ = _get(item, "type")
            if typ == "function_call":
                raw = _get(item, "arguments", "{}")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    raise InvalidRequestError("provider returned malformed tool arguments", provider="openai") from None
                if not isinstance(args, Mapping):
                    raise InvalidRequestError("provider returned non-object tool arguments", provider="openai") from None
                calls.append(ToolCall(_get(item, "call_id", _get(item, "id", "")),
                                      _get(item, "name", ""), args))
            elif typ == "message":
                for part in _get(item, "content", ()) or ():
                    if _get(part, "type") == "refusal":
                        refusal = _get(part, "refusal")
                    elif _get(part, "type") in ("output_text", "text"):
                        texts.append(_get(part, "text", ""))
        use = getattr(response, "usage", None)
        return GenerationResult("\n".join(texts), tuple(calls),
            _get(response, "status", "unknown"),
            _usage(_get(use, "input_tokens"), _get(use, "output_tokens")), refusal,
            _get(response, "_request_id"), _get(response, "model"), response)

    def stream(self, request: GenerationRequest):
        payload = {"model": request.model or self.model, "input": _responses_input(request.messages),
                   "store": False, "stream": True}
        _apply_responses_sampling(payload, request)
        try:
            parts = []
            final = None
            for event in self.client.responses.create(**payload, **_timeout(request)):
                if _get(event, "type") == "response.output_text.delta":
                    delta = _get(event, "delta", "")
                    parts.append(delta)
                    yield StreamEvent("text_delta", text=delta)
                elif _get(event, "type") == "response.completed":
                    final = _get(event, "response")
                elif _get(event, "type") in {"response.incomplete", "response.failed"}:
                    raise RuntimeError(
                        f"provider stream ended without completion: {_get(event, 'type')}"
                    )
            if final is None:
                raise RuntimeError("provider stream ended without a terminal response")
            if _get(final, "status", "unknown") != "completed":
                raise RuntimeError(
                    f"provider stream terminated with status {_get(final, 'status', 'unknown')}"
                )
            usage = _get(final, "usage")
            yield StreamEvent("completed", result=GenerationResult(
                "".join(parts), finish_reason=_get(final, "status", "unknown"),
                usage=_usage(_get(usage, "input_tokens"), _get(usage, "output_tokens")),
                request_id=_get(final, "_request_id"), model=_get(final, "model")))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("openai", exc) from None

class AnthropicProvider(_SDKProvider):
    def generate(self, request: GenerationRequest) -> GenerationResult:
        _reject_reasoning_effort(request, "anthropic")
        system, _ = _split_messages(request.messages)
        messages = _anthropic_messages(request.messages)
        payload: dict[str, Any] = {"model": request.model or self.model,
                                   "max_tokens": request.max_output_tokens or 1024,
                                   "messages": messages}
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = _anthropic_tools(request.tools)
        if request.tool_choice:
            payload["tool_choice"] = _anthropic_tool_choice(request.tool_choice)
        if request.response_schema:
            payload["output_config"] = _anthropic_output_config(request.response_schema)
        try:
            response = self.client.messages.create(**payload, **_timeout(request))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("anthropic", exc) from None
        calls, texts, refusal = [], [], None
        for block in getattr(response, "content", ()) or ():
            if _get(block, "type") == "tool_use":
                calls.append(ToolCall(_get(block, "id", ""), _get(block, "name", ""),
                                      _get(block, "input", {})))
            elif _get(block, "type") == "text":
                texts.append(_get(block, "text", ""))
            elif _get(block, "type") == "refusal":
                refusal = _get(block, "refusal", _get(block, "text", ""))
        use = getattr(response, "usage", None)
        return GenerationResult("\n".join(texts), tuple(calls), _get(response, "stop_reason", "unknown"),
            _usage(_get(use, "input_tokens"), _get(use, "output_tokens")), refusal,
            _get(response, "_request_id"), _get(response, "model"), response)

    def stream(self, request: GenerationRequest):
        _reject_reasoning_effort(request, "anthropic")
        system, _ = _split_messages(request.messages)
        messages = _anthropic_messages(request.messages)
        payload = {"model": request.model or self.model,
                   "max_tokens": request.max_output_tokens or 1024,
                   "messages": messages, "stream": True}
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        try:
            parts = []
            prompt_tokens = completion_tokens = None
            stop_reason = request_id = model = None
            for event in self.client.messages.create(**payload, **_timeout(request)):
                event_type = _get(event, "type")
                if event_type == "message_start":
                    message = _get(event, "message")
                    usage = _get(message, "usage")
                    prompt_tokens = _get(usage, "input_tokens")
                    request_id, model = _get(message, "_request_id"), _get(message, "model")
                elif event_type == "content_block_delta":
                    delta = _get(event, "delta")
                    if _get(delta, "type") == "text_delta":
                        value = _get(delta, "text", "")
                        parts.append(value)
                        yield StreamEvent("text_delta", text=value)
                elif event_type == "message_delta":
                    stop_reason = _get(_get(event, "delta"), "stop_reason")
                    completion_tokens = _get(_get(event, "usage"), "output_tokens")
            yield StreamEvent("completed", result=GenerationResult(
                "".join(parts), finish_reason=stop_reason or "unknown",
                usage=_usage(prompt_tokens, completion_tokens), request_id=request_id, model=model))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("anthropic", exc) from None

class GoogleProvider(_SDKProvider):
    def generate(self, request: GenerationRequest) -> GenerationResult:
        _reject_reasoning_effort(request, "google")
        from google.genai import types
        contents = _google_contents(request.messages)
        system, _ = _split_messages(request.messages)
        config: dict[str, Any] = {}
        if system:
            config["system_instruction"] = system
        if request.max_output_tokens is not None:
            config["max_output_tokens"] = request.max_output_tokens
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.response_schema:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = dict(request.response_schema)
        if request.tools:
            config["tools"] = _google_tools(request.tools, types)
        if request.tool_choice:
            config["tool_config"] = _google_tool_config(request.tool_choice, types)
        if request.timeout_seconds is not None:
            config["http_options"] = types.HttpOptions(
                timeout=max(1, round(request.timeout_seconds * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1))
        try:
            response = self.client.models.generate_content(
                model=request.model or self.model, contents=contents,
                config=types.GenerateContentConfig(**config) if config else None)
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("google", exc) from None
        candidate = (getattr(response, "candidates", None) or [None])[0]
        calls, texts = [], []
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", ()) or ():
            if getattr(part, "function_call", None):
                fc = part.function_call
                calls.append(ToolCall(_get(fc, "id", _get(fc, "name", "")),
                                      _get(fc, "name", ""), _get(fc, "args", {})))
            elif getattr(part, "text", None):
                texts.append(part.text)
        use = getattr(response, "usage_metadata", None)
        return GenerationResult("\n".join(texts), tuple(calls),
            _enum_text(_get(candidate, "finish_reason", "unknown")),
            _usage(_get(use, "prompt_token_count"), _get(use, "candidates_token_count")),
            None, None, request.model or self.model, response)

    def stream(self, request: GenerationRequest):
        _reject_reasoning_effort(request, "google")
        from google.genai import types
        contents = _google_contents(request.messages)
        system, _ = _split_messages(request.messages)
        config = {}
        if system:
            config["system_instruction"] = system
        if request.max_output_tokens is not None:
            config["max_output_tokens"] = request.max_output_tokens
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.timeout_seconds is not None:
            config["http_options"] = types.HttpOptions(
                timeout=max(1, round(request.timeout_seconds * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1))
        try:
            parts = []
            usage = final_candidate = None
            stream = self.client.models.generate_content_stream(
                model=request.model or self.model, contents=contents,
                config=types.GenerateContentConfig(**config) if config else None)
            for chunk in stream:
                usage = getattr(chunk, "usage_metadata", None) or usage
                candidates = getattr(chunk, "candidates", None) or ()
                if candidates:
                    final_candidate = candidates[0]
                delta = getattr(chunk, "text", "") or ""
                if delta:
                    parts.append(delta)
                    yield StreamEvent("text_delta", text=delta)
            yield StreamEvent("completed", result=GenerationResult(
                "".join(parts), finish_reason=_enum_text(_get(final_candidate, "finish_reason", "unknown")),
                usage=_usage(_get(usage, "prompt_token_count"),
                             _get(usage, "candidates_token_count")),
                model=request.model or self.model))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("google", exc) from None

class OpenAICompatibleProvider(OpenAIProvider):
    """OpenAI Chat Completions-compatible service with an explicit base URL."""

    def __init__(self, client, model: str, *, thinking: str | None = None,
                 cache_key: bool = False):
        super().__init__(client, model)
        if thinking not in {None, "enabled", "disabled"}:
            raise ValueError("unsupported compatible thinking control")
        self.thinking = thinking
        self.cache_key = cache_key

    def _apply_extensions(self, payload, request: GenerationRequest):
        # Opt-in extensions for compatible services that accept these fields.
        # `prompt_cache_key` routes requests sharing a prefix to a server holding
        # that prefix's cache; without it LongCat scatters them and rarely hits.
        extra = {}
        if self.thinking is not None:
            extra["thinking"] = {"type": self.thinking}
        if self.cache_key and request.cache_key:
            extra["prompt_cache_key"] = request.cache_key
        if extra:
            payload["extra_body"] = extra

    def generate(self, request: GenerationRequest) -> GenerationResult:
        payload: dict[str, Any] = {"model": request.model or self.model,
                                   "messages": _chat_messages(request.messages)}
        _apply_chat_sampling(payload, request)
        self._apply_extensions(payload, request)
        if request.tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description,
                "parameters": dict(t.input_schema)}} for t in request.tools]
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.response_schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "response", "strict": True, "schema": dict(request.response_schema)}}
        try:
            response = self.client.chat.completions.create(**payload, **_timeout(request))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("openai-compatible", exc) from None
        choice = response.choices[0]
        msg = choice.message
        calls = tuple(ToolCall(_get(c, "id", ""), _get(_get(c, "function"), "name", ""),
                               _parse_args(_get(_get(c, "function"), "arguments", "{}")))
                      for c in (_get(msg, "tool_calls", ()) or ()))
        usage = getattr(response, "usage", None)
        return GenerationResult(_get(msg, "content", "") or "", calls,
            _get(choice, "finish_reason", "unknown"),
            _chat_usage(usage), _get(msg, "refusal"), _get(response, "_request_id"),
            _get(response, "model"), response)

    def stream(self, request: GenerationRequest):
        payload = {"model": request.model or self.model,
                   "messages": _chat_messages(request.messages), "stream": True,
                   # Ask for the terminal usage chunk so prompt/completion (including
                   # reasoning) tokens are accounted instead of left unknown.
                   "stream_options": {"include_usage": True}}
        _apply_chat_sampling(payload, request)
        self._apply_extensions(payload, request)
        try:
            parts: list[str] = []
            finish_reason = "unknown"
            usage = Usage(source="unknown")
            model = None
            for chunk in self.client.chat.completions.create(**payload, **_timeout(request)):
                observed = getattr(chunk, "usage", None)
                if observed is not None:
                    usage = _chat_usage(observed)
                model = _get(chunk, "model", None) or model
                choices = _get(chunk, "choices", ()) or ()
                if not choices:
                    continue
                choice = choices[0]
                # `reasoning_content` deltas are deliberately not answer text;
                # only the model's final `content` is streamed to the caller.
                reason = _get(choice, "finish_reason", None)
                if reason:
                    finish_reason = reason
                delta = _get(_get(choice, "delta"), "content", "") or ""
                if delta:
                    parts.append(delta)
                    yield StreamEvent("text_delta", text=delta)
            yield StreamEvent("completed", result=GenerationResult(
                "".join(parts), finish_reason=finish_reason, usage=usage, model=model))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _error("openai-compatible", exc) from None

def _reject_reasoning_effort(request: GenerationRequest, provider: str) -> None:
    if request.reasoning_effort is not None:
        raise InvalidRequestError("reasoning effort is unsupported by this provider", provider=provider)


def _apply_responses_sampling(payload: dict[str, Any], request: GenerationRequest) -> None:
    """Sampling fields for the OpenAI Responses API."""
    if request.max_output_tokens is not None:
        payload["max_output_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.reasoning_effort is not None:
        payload["reasoning"] = {"effort": request.reasoning_effort}


def _apply_chat_sampling(payload: dict[str, Any], request: GenerationRequest) -> None:
    """Sampling fields for OpenAI Chat Completions-compatible services."""
    if request.max_output_tokens is not None:
        payload["max_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.reasoning_effort is not None:
        payload["reasoning_effort"] = request.reasoning_effort


def _parse_args(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        raise InvalidRequestError("provider returned malformed tool arguments", provider="openai-compatible") from None
    if not isinstance(parsed, Mapping):
        raise InvalidRequestError("provider returned non-object tool arguments", provider="openai-compatible")
    return parsed


def _timeout(request: GenerationRequest) -> dict[str, float]:
    return {"timeout": request.timeout_seconds} if request.timeout_seconds is not None else {}


def _anthropic_tool_choice(value: str) -> Mapping[str, Any]:
    if value in {"auto", "none"}:
        return {"type": value}
    if value in {"required", "any"}:
        return {"type": "any"}
    if value.startswith("tool:") and value[5:]:
        return {"type": "tool", "name": value[5:]}
    raise InvalidRequestError("unsupported Anthropic tool choice", provider="anthropic")

def _get(value: Any, key: str, default=None):
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _enum_text(value: Any) -> str:
    return str(getattr(value, "name", getattr(value, "value", value))).lower()
