"""Native asynchronous batch adapters. Concrete SDKs are instantiated in factory.py only."""

from __future__ import annotations

import io
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

from app.llm.adapters import _error as generation_error
from app.llm.adapters import _get, _usage_from_tokens
from app.llm.base import GenerationRequest, GenerationResult, InvalidRequestError
from app.llm.batch import (
    BATCH_STATES,
    DEFAULT_MAX_BATCH_ITEMS,
    DEFAULT_MAX_JSONL_BYTES,
    BatchItem,
    BatchItemResult,
    BatchProgress,
    BatchSubmission,
    encode_jsonl,
    iter_jsonl_chunks,
    read_bounded_jsonl,
    validate_batch_items,
)

# How many recently submitted batches keep their correlation ids in memory.
_MAX_REMEMBERED_BATCHES = 256


class _BatchAdapter:
    provider: str

    def __init__(self, client, model: str, *, max_items: int = DEFAULT_MAX_BATCH_ITEMS,
                 max_jsonl_bytes: int = DEFAULT_MAX_JSONL_BYTES):
        self.client, self.model = client, model
        self.max_items, self.max_jsonl_bytes = max_items, max_jsonl_bytes
        # A bounded LRU of correlation-id lists, so a long-lived process does
        # not retain every batch's ids forever.
        self._expected: OrderedDict[str, tuple[str, ...]] = OrderedDict()

    def _validate(self, items: Sequence[BatchItem]) -> None:
        validate_batch_items(items, max_items=self.max_items)
        if any(item.request.model not in (None, self.model) for item in items):
            raise ValueError("a provider batch must use one configured model")

    def _remember(self, batch_id: str, items: Sequence[BatchItem]) -> None:
        self._expected[batch_id] = tuple(item.correlation_id for item in items)
        self._expected.move_to_end(batch_id)
        while len(self._expected) > _MAX_REMEMBERED_BATCHES:
            self._expected.popitem(last=False)

    def _missing(self, expected):
        return tuple(BatchItemResult(key, error="provider returned no results yet") for key in expected) if expected else ()

    def _complete(self, results, expected):
        return _complete_results(results, expected, self.provider)


def _int_or_none(value):
    return int(value) if value is not None else None


def _complete_results(results: Sequence[BatchItemResult], expected_ids: Sequence[str] | None,
                      provider: str) -> tuple[BatchItemResult, ...]:
    expected = tuple(expected_ids) if expected_ids is not None else None
    if expected is not None and len(expected) != len(set(expected)):
        raise ValueError("expected batch correlation ids must be unique")
    by_id = {}
    for result in results:
        key = result.correlation_id
        if key in by_id:
            raise InvalidRequestError("provider returned duplicate batch correlation ids", provider=provider)
        if expected is not None and key not in expected:
            raise InvalidRequestError("provider returned an unknown batch correlation id", provider=provider)
        by_id[key] = result
    if expected is None:
        return tuple(results)
    for key in expected:
        by_id.setdefault(key, BatchItemResult(key, error="provider returned no result for this batch item"))
    return tuple(by_id[key] for key in expected)


def _status(value: str | None, provider: str) -> str:
    state = (value or "unknown").lower().replace("job_state_", "")
    if provider == "google":
        state = {"running": "in_progress", "succeeded": "completed", "cancelled": "cancelled"}.get(state, state)
    if state in BATCH_STATES - {"unknown"}:
        return state
    if provider == "anthropic":
        return {"ended": "completed", "canceling": "cancelling", "canceled": "cancelled"}.get(state, state)
    return "unknown"


def _openai_generation(body: Mapping[str, Any]) -> GenerationResult:
    outputs = _get(body, "output", ()) or ()
    text, calls = [], []
    refusal = None
    for item in outputs:
        kind = _get(item, "type")
        if kind == "message":
            for part in _get(item, "content", ()) or ():
                if _get(part, "type") == "refusal":
                    refusal = _get(part, "refusal")
                elif _get(part, "type") in {"output_text", "text"}:
                    text.append(_get(part, "text", ""))
        elif kind == "function_call":
            from app.llm.base import ToolCall
            raw = _get(item, "arguments", "{}")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                raise InvalidRequestError("provider batch returned malformed tool arguments", provider="openai") from None
            if isinstance(args, Mapping):
                calls.append(ToolCall(_get(item, "call_id", ""), _get(item, "name", ""), args))
    return GenerationResult("\n".join(text), tuple(calls), _get(body, "status", "unknown"),
                            _usage_from_tokens(_get(body, "usage")), refusal,
                            _get(body, "_request_id"), _get(body, "model"), body)


def _anthropic_generation(message: Any) -> GenerationResult:
    from app.llm.base import ToolCall
    text, calls = [], []
    refusal = None
    for block in _get(message, "content", ()) or ():
        kind = _get(block, "type")
        if kind == "text":
            text.append(_get(block, "text", ""))
        elif kind == "tool_use":
            calls.append(ToolCall(_get(block, "id", ""), _get(block, "name", ""),
                                  _get(block, "input", {})))
        elif kind == "refusal":
            refusal = _get(block, "refusal", _get(block, "text", ""))
    use = _get(message, "usage")
    usage = _usage_from_tokens({"input_tokens": _get(use, "input_tokens"),
                               "output_tokens": _get(use, "output_tokens")})
    return GenerationResult("\n".join(text), tuple(calls), _get(message, "stop_reason", "unknown"),
                            usage, refusal, _get(message, "_request_id"), _get(message, "model"), message)


def _google_generation(response: Any, model: str) -> GenerationResult:
    from app.llm.base import ToolCall
    candidate = (_get(response, "candidates", ()) or (None,))[0]
    content = _get(candidate, "content")
    texts, calls = [], []
    for part in _get(content, "parts", ()) or ():
        if _get(part, "text"):
            texts.append(_get(part, "text"))
        function = _get(part, "function_call")
        if function:
            calls.append(ToolCall(_get(function, "id", _get(function, "name", "")),
                                  _get(function, "name", ""), _get(function, "args", {})))
    usage = _get(response, "usage_metadata", _get(response, "usageMetadata"))
    gen_usage = _usage_from_tokens({
        "input_tokens": _get(usage, "prompt_token_count", _get(usage, "promptTokenCount")),
        "output_tokens": _get(usage, "candidates_token_count", _get(usage, "candidatesTokenCount")),
        "total_tokens": _get(usage, "total_token_count", _get(usage, "totalTokenCount")),
    })
    return GenerationResult("\n".join(texts), tuple(calls),
        str(getattr(_get(candidate, "finish_reason", "unknown"), "name", getattr(_get(candidate, "finish_reason", "unknown"), "value", _get(candidate, "finish_reason", "unknown")))).lower(), gen_usage,
        model=_get(response, "model_version", model), raw=response)


def _openai_body(request: GenerationRequest, model: str) -> dict[str, Any]:
    from app.llm.adapters import _openai_text_format, _responses_input, _tools
    body: dict[str, Any] = {"model": model, "input": _responses_input(request.messages), "store": False}
    if request.max_output_tokens is not None:
        body["max_output_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.tools:
        body["tools"] = _tools(request)
    if request.tool_choice:
        body["tool_choice"] = request.tool_choice
    if request.response_schema:
        body["text"] = _openai_text_format(request.response_schema)
    return body


def _anthropic_body(request: GenerationRequest, model: str) -> dict[str, Any]:
    from app.llm.adapters import (
        _anthropic_messages,
        _anthropic_output_config,
        _anthropic_tool_choice,
        _anthropic_tools,
        _split_messages,
    )
    system, _ = _split_messages(request.messages)
    body: dict[str, Any] = {"model": model, "max_tokens": request.max_output_tokens or 1024,
                            "messages": _anthropic_messages(request.messages)}
    if system:
        body["system"] = system
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.tools:
        body["tools"] = _anthropic_tools(request.tools)
    if request.tool_choice:
        body["tool_choice"] = _anthropic_tool_choice(request.tool_choice)
    if request.response_schema:
        body["output_config"] = _anthropic_output_config(request.response_schema)
    return body


def _google_body(request: GenerationRequest) -> dict[str, Any]:
    from google.genai import types

    from app.llm.adapters import (
        _google_contents,
        _google_tool_config,
        _google_tools,
        _split_messages,
    )
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
    return {"contents": contents, "config": config}


class OpenAIBatchProvider(_BatchAdapter):
    provider = "openai"
    endpoint = "/v1/responses"

    def __init__(self, client, model: str, *, max_items: int = 50_000,
                 max_jsonl_bytes: int = DEFAULT_MAX_JSONL_BYTES):
        super().__init__(client, model, max_items=max_items, max_jsonl_bytes=max_jsonl_bytes)

    def submit(self, items: Sequence[BatchItem]) -> BatchSubmission:
        self._validate(items)
        content = encode_jsonl([{"custom_id": x.correlation_id, "method": "POST", "url": self.endpoint,
                                 "body": _openai_body(x.request, self.model)} for x in items],
                               max_bytes=self.max_jsonl_bytes, max_items=self.max_items)
        try:
            file = self.client.files.create(file=("batch.jsonl", content, "application/jsonl"), purpose="batch")
            job = self.client.batches.create(input_file_id=file.id, endpoint=self.endpoint,
                                             completion_window="24h")
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        batch_id = _get(job, "id")
        self._remember(batch_id, items)
        return BatchSubmission(batch_id, self.provider, _status(_get(job, "status"), self.provider),
                               len(items), _get(file, "id"))

    def status(self, batch_id: str) -> BatchProgress:
        try:
            job = self.client.batches.retrieve(batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        counts = _get(job, "request_counts")
        usage = _get(job, "usage")
        tokens = _usage_from_tokens({"input_tokens": _get(usage, "input_tokens"),
                                   "output_tokens": _get(usage, "output_tokens"),
                                   "total_tokens": _get(usage, "total_tokens")})
        return BatchProgress(batch_id, self.provider, _status(_get(job, "status"), self.provider),
            _int_or_none(_get(counts, "total")), _int_or_none(_get(counts, "completed")),
            _int_or_none(_get(counts, "failed")), tokens)

    def cancel(self, batch_id: str) -> BatchProgress:
        try:
            self.client.batches.cancel(batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        return self.status(batch_id)

    def results(self, batch_id: str, *, expected_ids: Sequence[str] | None = None):
        expected = expected_ids if expected_ids is not None else self._expected.get(batch_id)
        try:
            job = self.client.batches.retrieve(batch_id)
            output_file_id = _get(job, "output_file_id")
            error_file_id = _get(job, "error_file_id")
            if not output_file_id and not error_file_id:
                return self._missing(expected)
            records = []
            for file_id in (output_file_id, error_file_id):
                if not file_id:
                    continue
                with self.client.files.with_streaming_response.content(file_id) as response:
                    records.extend(read_bounded_jsonl(response.iter_bytes(),
                        max_bytes=self.max_jsonl_bytes, max_items=self.max_items))
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        results = []
        for record in records:
            correlation = _get(record, "custom_id")
            response = _get(record, "response")
            body = _get(response, "body")
            if not correlation:
                raise InvalidRequestError("provider batch result omitted its correlation id", provider=self.provider)
            if _get(record, "error") or body is None or (_get(response, "status_code") or 200) >= 400:
                results.append(BatchItemResult(correlation, error="provider batch item failed"))
            else:
                parsed = _openai_generation(body)
                results.append(BatchItemResult(correlation, result=parsed))
        return self._complete(results, expected)


class AnthropicBatchProvider(_BatchAdapter):
    provider = "anthropic"

    def submit(self, items: Sequence[BatchItem]) -> BatchSubmission:
        self._validate(items)
        payload = [{"custom_id": x.correlation_id, "params": _anthropic_body(x.request, self.model)} for x in items]
        try:
            job = self.client.messages.batches.create(requests=payload)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        batch_id = _get(job, "id")
        self._remember(batch_id, items)
        return BatchSubmission(batch_id, self.provider,
            _status(_get(job, "processing_status"), self.provider), len(items))

    def status(self, batch_id: str) -> BatchProgress:
        try:
            job = self.client.messages.batches.retrieve(batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        counts = _get(job, "request_counts")
        return BatchProgress(batch_id, self.provider, _status(_get(job, "processing_status"), self.provider),
            sum(int(_get(counts, k, 0) or 0) for k in
                ("processing", "succeeded", "errored", "canceled", "expired")),
            _int_or_none(_get(counts, "succeeded")),
            sum(int(_get(counts, k, 0) or 0) for k in ("errored", "canceled", "expired")))

    def cancel(self, batch_id: str) -> BatchProgress:
        try:
            self.client.messages.batches.cancel(batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        return self.status(batch_id)

    def results(self, batch_id: str, *, expected_ids: Sequence[str] | None = None):
        expected = expected_ids if expected_ids is not None else self._expected.get(batch_id)
        try:
            stream = self.client.messages.batches.results(batch_id)
            results = []
            total_bytes = 0
            for row in stream:
                if len(results) >= self.max_items:
                    raise ValueError("Anthropic batch results exceed item limit")
                encoded_row = (row.model_dump_json() if callable(getattr(row, "model_dump_json", None))
                               else json.dumps(row, default=str))
                total_bytes += len(encoded_row.encode("utf-8")) + 1
                if total_bytes > self.max_jsonl_bytes:
                    raise ValueError("Anthropic batch results exceed JSONL byte limit")
                key = _get(row, "custom_id")
                wrapped = _get(row, "result")
                kind = _get(wrapped, "type")
                if kind == "succeeded":
                    result = _anthropic_generation(_get(wrapped, "message"))
                    results.append(BatchItemResult(key, result=result))
                else:
                    results.append(BatchItemResult(key, error="provider batch item failed"))
        except Exception as exc:
            if isinstance(exc, (ValueError, InvalidRequestError)):
                raise
            raise generation_error(self.provider, exc) from None
        return self._complete(results, expected)


class GoogleBatchProvider(_BatchAdapter):
    provider = "google"
    max_inline_bytes = 20_000_000
    max_file_bytes = 2_000_000_000

    def __init__(self, client, model: str, *, file_downloader=None, **kwargs):
        super().__init__(client, model, **kwargs)
        self.file_downloader = file_downloader

    def submit(self, items: Sequence[BatchItem]) -> BatchSubmission:
        self._validate(items)
        requests = []
        file_records = []
        for item in items:
            body = _google_body(item.request)
            requests.append({**body, "metadata": {"custom_id": item.correlation_id}})
            file_records.append({"key": item.correlation_id, "request": body})
        # Google permits inline inputs under 20 MB and input files up to 2 GB.
        inline_content = encode_jsonl(requests,
            max_bytes=min(self.max_jsonl_bytes, self.max_file_bytes), max_items=self.max_items)
        input_file_id = None
        try:
            if len(inline_content) <= self.max_inline_bytes:
                job = self.client.batches.create(model=self.model, src=requests)
            else:
                content = encode_jsonl(file_records, max_bytes=min(self.max_jsonl_bytes, self.max_file_bytes),
                                       max_items=self.max_items)
                from google.genai import types
                source = io.BytesIO(content)
                source.name = "batch-requests.jsonl"
                uploaded = self.client.files.upload(file=source, config=types.UploadFileConfig(
                    display_name="batch-requests", mime_type="jsonl"))
                input_file_id = _get(uploaded, "name")
                job = self.client.batches.create(model=self.model, src=input_file_id)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        batch_id = _get(job, "name")
        self._remember(batch_id, items)
        return BatchSubmission(batch_id, self.provider, _status(_enum(_get(job, "state")), self.provider),
                               len(items), input_file_id)

    def status(self, batch_id: str) -> BatchProgress:
        try:
            job = self.client.batches.get(name=batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        stats = _get(job, "completion_stats")
        success = _int_or_none(_get(stats, "successful_count"))
        failed = _int_or_none(_get(stats, "failed_count"))
        incomplete = _int_or_none(_get(stats, "incomplete_count"))
        total = None if success is None and failed is None else (success or 0) + (failed or 0) + max(incomplete or 0, 0)
        return BatchProgress(batch_id, self.provider, _status(_enum(_get(job, "state")), self.provider),
            total, success, failed)

    def cancel(self, batch_id: str) -> BatchProgress:
        try:
            self.client.batches.cancel(name=batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK errors
            raise generation_error(self.provider, exc) from None
        return self.status(batch_id)

    def results(self, batch_id: str, *, expected_ids: Sequence[str] | None = None):
        expected = expected_ids if expected_ids is not None else self._expected.get(batch_id)
        try:
            job = self.client.batches.get(name=batch_id)
            destination = _get(job, "dest")
            inline = _get(destination, "inlined_responses")
            if inline is None:
                file_name = _get(destination, "file_name")
                if not file_name:
                    return self._missing(expected)
                if self.file_downloader is None:
                    raise RuntimeError("Google file results require a bounded file downloader")
                max_bytes = min(self.max_jsonl_bytes, self.max_file_bytes)
                with self.file_downloader(file_name, max_bytes=max_bytes) as chunks:
                    records = iter_jsonl_chunks(iter(chunks), max_bytes=max_bytes,
                                                max_items=self.max_items)
                    return self._parse_file(records, expected)
            if len(inline) > self.max_items:
                raise ValueError("Google batch results exceed item limit")
            results = []
            for row in inline:
                metadata = _get(row, "metadata", {}) or {}
                correlation = _get(metadata, "custom_id")
                if correlation is None:
                    # Positional fallback could match an out-of-order row to the
                    # wrong request and publish the wrong generation.
                    raise InvalidRequestError(
                        "Google batch result omitted its correlation id", provider=self.provider)
                if _get(row, "error"):
                    results.append(BatchItemResult(correlation, error="provider batch item failed"))
                elif _get(row, "response"):
                    results.append(BatchItemResult(correlation,
                        result=_google_generation(_get(row, "response"), self.model)))
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise generation_error(self.provider, exc) from None
        return self._complete(results, expected)

    def _parse_file(self, records, expected):
        results = []
        for row in records:
            correlation = _get(row, "key")
            if not correlation:
                raise InvalidRequestError("Google batch result omitted its correlation key", provider=self.provider)
            if _get(row, "error"):
                results.append(BatchItemResult(correlation, error="provider batch item failed"))
            else:
                result = _get(row, "response") or row
                results.append(BatchItemResult(correlation, result=_google_generation(result, self.model)))
        return self._complete(results, expected)


def _enum(value):
    return getattr(value, "name", value)
