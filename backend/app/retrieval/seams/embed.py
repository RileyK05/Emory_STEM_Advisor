"""Embedding seam: chunks close in meaning to the query.

Embeds the query itself, inside the guarded seam: if the embedder raises, the
seam reports `failed` rather than aborting retrieval. Rows of a different
dimension or model are excluded by SQL, never truncated.

A test may pass a precomputed vector instead of an embedder (`query_vec`), so
the seam's SQL/ordering can be exercised without a model.
"""

from uuid import UUID

import numpy as np
from psycopg import Connection

from app.db import queries
from app.embeddings.base import Embedder
from app.retrieval.config import EmbedSeamConfig
from app.retrieval.types import Candidate, SeamResult


def embed_seam(
    conn: Connection,
    collection_id: UUID,
    query: str,
    model: str,
    config: EmbedSeamConfig,
    *,
    embedder: Embedder | None = None,
    query_vec: np.ndarray | None = None,
) -> SeamResult:
    if not config.enabled:
        return SeamResult(
            name="embed", enabled=False, state="disabled", reason="disabled in config"
        )

    if query_vec is None:
        if embedder is None:
            raise ValueError("embed_seam needs an embedder or a query_vec")
        query_vec = embedder.embed_query(query)

    dim = len(query_vec)
    rows = conn.execute(
        queries.get("embed_search"),
        {
            "q": query_vec,
            "model": model,
            "dim": dim,
            "collection_id": collection_id,
            "limit": config.limit,
            "min_similarity": config.min_similarity,
        },
    ).fetchall()
    if not rows:
        return SeamResult(
            name="embed",
            enabled=True,
            state="dormant",
            reason="no chunks above the similarity floor",
        )

    candidates = [
        Candidate(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            chunk_index=row["chunk_index"],
            seam="embed",
            raw_score=float(row["dot"]),
        )
        for row in rows
    ]
    return SeamResult(name="embed", enabled=True, state="active", candidates=candidates)
