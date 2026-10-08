"""Trace writing. Every retrieve() call commits exactly one trace."""

import re
from uuid import UUID, uuid4

from psycopg import Connection

from app.db import queries
from app.retrieval.fusion import FusedChunk
from app.retrieval.types import SeamResult


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", query.lower())).strip()


def write_trace(
    conn: Connection,
    *,
    collection_id: UUID,
    query: str,
    config_version: int,
    embed_model: str | None,
    refused: bool,
    seams: list[SeamResult],
    contributions: list[FusedChunk],
) -> UUID:
    trace_id = uuid4()
    with conn.transaction():
        conn.execute(
            queries.get("insert_trace"),
            {
                "trace_id": trace_id,
                "collection_id": collection_id,
                "query": query,
                "normalized_query": normalize_query(query),
                "config_version": config_version,
                "embed_model": embed_model,
                "refused": refused,
            },
        )
        for seam in seams:
            conn.execute(
                queries.get("insert_trace_seam"),
                {
                    "trace_id": trace_id,
                    "seam": seam.name,
                    "enabled": seam.enabled,
                    "state": seam.state,
                    "reason": seam.reason,
                    "candidate_count": len(seam.candidates),
                },
            )
        for contribution in contributions:
            conn.execute(
                queries.get("insert_trace_contribution"),
                {
                    "trace_id": trace_id,
                    "chunk_id": contribution.chunk_id,
                    "seams": list(contribution.seams),
                    "normalized_score": contribution.normalized_score,
                    "source_slot": contribution.source_slot,
                    "rank": contribution.rank,
                },
            )
    return trace_id
