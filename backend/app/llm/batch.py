"""Provider-neutral asynchronous batch contracts and bounded JSONL helpers."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Literal, Protocol, get_args

from app.llm.base import GenerationRequest, GenerationResult, Usage

BatchState = Literal[
    "pending", "validating", "in_progress", "finalizing", "completed", "failed", "expired",
    "cancelling", "cancelled", "unknown",
]

BATCH_STATES = frozenset(get_args(BatchState))
TERMINAL_BATCH_STATES = frozenset({"completed", "failed", "expired", "cancelled"})

DEFAULT_MAX_BATCH_ITEMS = 50_000
DEFAULT_MAX_JSONL_BYTES = 200_000_000


@dataclass(frozen=True)
class BatchItem:
    correlation_id: str
    request: GenerationRequest


@dataclass(frozen=True)
class BatchSubmission:
    batch_id: str
    provider: str
    state: BatchState
    item_count: int
    input_file_id: str | None = None


@dataclass(frozen=True)
class BatchProgress:
    batch_id: str
    provider: str
    state: BatchState
    total_count: int | None = None
    completed_count: int | None = None
    failed_count: int | None = None
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class BatchItemResult:
    correlation_id: str
    result: GenerationResult | None = None
    error: str | None = None
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("batch item result requires a correlation id")
        if (self.result is None) == (self.error is None):
            raise ValueError("batch item result must contain exactly one of result or error")
        if self.result is not None and self.usage.source == "unknown":
            object.__setattr__(self, "usage", self.result.usage)


class BatchProvider(Protocol):
    def submit(self, items: Sequence[BatchItem]) -> BatchSubmission: ...
    def status(self, batch_id: str) -> BatchProgress: ...
    def results(self, batch_id: str, *, expected_ids: Sequence[str] | None = None) -> tuple[BatchItemResult, ...]: ...
    def cancel(self, batch_id: str) -> BatchProgress: ...


def validate_batch_items(items: Sequence[BatchItem], *, max_items: int = DEFAULT_MAX_BATCH_ITEMS) -> None:
    if not items:
        raise ValueError("a batch must contain at least one request")
    if len(items) > max_items:
        raise ValueError(f"batch exceeds the {max_items} item limit")
    ids = [item.correlation_id for item in items]
    if any(not value or len(value) > 256 for value in ids):
        raise ValueError("batch correlation ids must contain 1-256 characters")
    if len(ids) != len(set(ids)):
        raise ValueError("batch correlation ids must be unique")


def encode_jsonl(records: Sequence[Mapping[str, Any]], *, max_bytes: int = DEFAULT_MAX_JSONL_BYTES,
                 max_items: int = DEFAULT_MAX_BATCH_ITEMS) -> bytes:
    if len(records) > max_items:
        raise ValueError(f"JSONL exceeds the {max_items} record limit")
    chunks = []
    size = 0
    for record in records:
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False) + "\n").encode("utf-8")
        size += len(encoded)
        if size > max_bytes:
            raise ValueError(f"JSONL exceeds the {max_bytes} byte limit")
        chunks.append(encoded)
    return b"".join(chunks)


def _parse_record(raw: bytes | bytearray, line_number: int) -> Mapping[str, Any]:
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"invalid JSONL record at line {line_number}") from None
    if not isinstance(record, Mapping):
        raise TypeError(f"JSONL record at line {line_number} must be an object")
    return record


def iter_jsonl(data: bytes, *, max_bytes: int = DEFAULT_MAX_JSONL_BYTES,
               max_items: int = DEFAULT_MAX_BATCH_ITEMS) -> Iterator[Mapping[str, Any]]:
    """Parse already-bounded file bytes and reject malformed/non-object records."""
    if len(data) > max_bytes:
        raise ValueError(f"JSONL exceeds the {max_bytes} byte limit")
    count = 0
    for line_number, line in enumerate(BytesIO(data), 1):
        if not line.strip():
            continue
        count += 1
        if count > max_items:
            raise ValueError(f"JSONL exceeds the {max_items} record limit")
        yield _parse_record(line, line_number)


def read_bounded_jsonl(chunks: Iterator[bytes], *, max_bytes: int = DEFAULT_MAX_JSONL_BYTES,
                       max_items: int = DEFAULT_MAX_BATCH_ITEMS) -> tuple[Mapping[str, Any], ...]:
    """Collect a streamed remote JSONL file up to a strict byte limit."""
    pieces, size = [], 0
    for chunk in chunks:
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"remote JSONL exceeds the {max_bytes} byte limit")
        pieces.append(chunk)
    return tuple(iter_jsonl(b"".join(pieces), max_bytes=max_bytes, max_items=max_items))


def iter_jsonl_chunks(chunks: Iterator[bytes], *, max_bytes: int = DEFAULT_MAX_JSONL_BYTES,
                      max_items: int = DEFAULT_MAX_BATCH_ITEMS) -> Iterator[Mapping[str, Any]]:
    """Incrementally parse JSONL while enforcing byte and record caps before buffering."""
    size = count = line_number = 0
    pending = bytearray()

    def finish_line() -> Mapping[str, Any] | None:
        nonlocal count
        record = None
        if pending.strip():
            count += 1
            if count > max_items:
                raise ValueError(f"JSONL exceeds the {max_items} record limit")
            record = _parse_record(pending, line_number)
        pending.clear()
        return record

    for chunk in chunks:
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"remote JSONL exceeds the {max_bytes} byte limit")
        start = 0
        while True:
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline
            pending.extend(chunk[start:end])
            if newline < 0:
                break
            line_number += 1
            record = finish_line()
            if record is not None:
                yield record
            start = newline + 1
    line_number += 1
    record = finish_line()
    if record is not None:
        yield record
