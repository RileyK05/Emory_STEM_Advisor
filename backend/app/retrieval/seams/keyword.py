"""Keyword seam: full-text OR-match over indexed chunks. No model."""

import re
from uuid import UUID

from psycopg import Connection

from app.db import queries
from app.retrieval.config import KeywordSeamConfig
from app.retrieval.types import Candidate, SeamResult

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(query: str, min_token_len: int) -> list[str]:
    seen: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if len(token) >= min_token_len and token not in seen:
            seen.append(token)
    return seen


def keyword_seam(
    conn: Connection,
    collection_id: UUID,
    query: str,
    config: KeywordSeamConfig,
) -> SeamResult:
    if not config.enabled:
        return SeamResult(
            name="keyword", enabled=False, state="disabled", reason="disabled in config"
        )

    tokens = _tokens(query, config.min_token_len)
    if not tokens:
        return SeamResult(
            name="keyword", enabled=True, state="dormant", reason="no searchable terms"
        )

    # Only [a-z0-9]+ tokens reach SQL, joined with OR. Hostile input cannot
    # break the tsquery because it is passed as a parameter.
    q = " | ".join(tokens)
    rows = conn.execute(
        queries.get("keyword_search"),
        {"q": q, "collection_id": collection_id, "limit": config.limit},
    ).fetchall()
    candidates = [
        Candidate(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            chunk_index=row["chunk_index"],
            seam="keyword",
            raw_score=float(row["score"]),
        )
        for row in rows
    ]
    return SeamResult(name="keyword", enabled=True, state="active", candidates=candidates)
