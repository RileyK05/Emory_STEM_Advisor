"""Retrieval orchestration: run seams, fuse, contract-check, trace, refuse."""

from dataclasses import replace
from uuid import UUID

from psycopg import Connection

from app.db import queries
from app.embeddings.base import Embedder
from app.retrieval.config import RetrievalConfig
from app.retrieval.fusion import FusedChunk, fuse
from app.retrieval.seams.embed import embed_seam
from app.retrieval.seams.keyword import keyword_seam
from app.retrieval.seams.prereq import prereq_seam
from app.retrieval.trace import normalize_query, write_trace
from app.retrieval.types import (
    CitedChunk,
    Refusal,
    RetrievalTrace,
    Retrieved,
    SeamResult,
)


class ContractViolation(RuntimeError):
    """A fused chunk lacked a locator/grounding. This is a bug, not data."""


def _run(name: str, func) -> SeamResult:
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - a failing seam must not abort others
        return SeamResult(
            name=name,
            enabled=True,
            state="failed",
            reason=f"{type(exc).__name__}: {exc}",
        )


def retrieve(
    conn: Connection,
    *,
    collection_id: UUID,
    query: str,
    embedder: Embedder,
    embed_model: str,
    config: RetrievalConfig,
) -> Retrieved | Refusal:
    # The embed seam is always run (guarded), so it appears in the trace even
    # when disabled. The query is embedded inside the seam, so an embedder
    # failure is isolated to that seam rather than aborting retrieval.
    results: list[SeamResult] = [
        _run(
            "keyword",
            lambda: keyword_seam(conn, collection_id, query, config.seams.keyword),
        ),
        _run(
            "embed",
            lambda: embed_seam(
                conn,
                collection_id,
                query,
                embed_model,
                config.seams.embed,
                embedder=embedder,
            ),
        ),
        _run(
            "prereq",
            lambda: prereq_seam(conn, collection_id, query, config.seams.prereq),
        ),
    ]

    fused = fuse(
        results,
        cited_set_size=config.fusion.cited_set_size,
        embed_only_quota=config.seams.embed.only_quota,
    )
    if config.fusion.min_score > 0.0:
        fused = [item for item in fused if item.normalized_score >= config.fusion.min_score]

    cited: list[CitedChunk] = []
    contributions: list[FusedChunk] = []
    seen_text: set[str] = set()
    for item in fused:
        row = conn.execute(
            queries.get("cited_chunk_details"), {"chunk_id": item.chunk_id}
        ).fetchone()
        # Contract: every emitted chunk has a locator and a seam, and its
        # source is indexed. A violation is a pipeline bug.
        if (
            row is None
            or row["source_status"] != "indexed"
            or not item.seams
            or not row["locator_label"]
        ):
            raise ContractViolation(f"chunk {item.chunk_id} failed the contract check")
        # Duplicate passages (repeated boilerplate, overlapping chunks) are
        # cited once; the list is already score-ordered, so the first wins.
        if config.fusion.dedupe:
            fingerprint = row["text"].strip()
            if fingerprint in seen_text:
                continue
            seen_text.add(fingerprint)
        contributions.append(
            replace(item, rank=len(cited) + 1)
        )
        cited.append(
            CitedChunk(
                chunk_id=item.chunk_id,
                source_id=item.source_id,
                chunk_index=item.chunk_index,
                source_name=row["source_name"],
                locator_label=row["locator_label"],
                text=row["text"],
                seams=item.seams,
                normalized_score=item.normalized_score,
                source_slot=item.source_slot,
                rank=len(cited) + 1,
            )
        )

    refused = not cited
    trace_id = write_trace(
        conn,
        collection_id=collection_id,
        query=query,
        config_version=config.version,
        embed_model=embed_model,
        refused=refused,
        seams=results,
        contributions=contributions,
    )
    trace = RetrievalTrace(
        trace_id=trace_id,
        collection_id=collection_id,
        query=query,
        normalized_query=normalize_query(query),
        config_version=config.version,
        embed_model=embed_model,
        refused=refused,
        seams=tuple(results),
    )
    if refused:
        return Refusal(reason="no relevant material found", trace=trace)
    return Retrieved(chunks=cited, trace=trace)
