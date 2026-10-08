"""Retrieval value types shared by every seam and by fusion.

Field names mirror RetrievalTrace / SeamStatus / Contribution in
frontend/src/api/types.ts.
"""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class Candidate:
    chunk_id: UUID
    source_id: UUID
    chunk_index: int
    seam: str
    raw_score: float


@dataclass(frozen=True)
class SeamResult:
    name: str
    enabled: bool
    state: str  # active | dormant | disabled | failed
    candidates: list[Candidate] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class CitedChunk:
    chunk_id: UUID
    source_id: UUID
    chunk_index: int
    source_name: str
    locator_label: str
    text: str
    seams: tuple[str, ...]
    normalized_score: float
    source_slot: int
    rank: int


@dataclass(frozen=True)
class RetrievalTrace:
    trace_id: UUID
    collection_id: UUID
    query: str
    normalized_query: str
    config_version: int
    embed_model: str | None
    refused: bool
    seams: tuple[SeamResult, ...]


@dataclass(frozen=True)
class Retrieved:
    chunks: list[CitedChunk]
    trace: RetrievalTrace


@dataclass(frozen=True)
class Refusal:
    reason: str
    trace: RetrievalTrace
