"""Keyword seam: full-text OR-match over indexed chunks. No model."""

import re
from uuid import UUID

from psycopg import Connection

from app.db import queries
from app.retrieval.config import KeywordSeamConfig
from app.retrieval.types import Candidate, SeamResult

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMERIC_REF_RE = re.compile(r"\b\d+(?:[./-]\d+)+\b")
# Conversational scaffolding otherwise lets repeated catalog boilerplate
# outrank the actual subject. Numbers and department/course codes are retained.
QUERY_SCAFFOLD = frozenset(
    ["course", "courses", "catalog", "current", "take", "taking", "explore", "interested", "want", "wants", "recommend", "recommends", "recommendation", "recommendations", "suggest", "relevant", "help", "helps", "supported", "descriptions", "described", "teaches", "teach", "find", "here", "which", "what", "should", "would", "could", "can", "need", "know", "study", "studying", "career", "provide", "information", "based", "context", "available", "list", "listed"]
)


def _tokens(query: str, min_token_len: int) -> list[str]:
    seen: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if ((len(token) >= min_token_len or token.isdigit())
                and token not in seen and token not in QUERY_SCAFFOLD):
            seen.append(token)
    return seen


def keyword_seam(
    conn: Connection,
    collection_id: UUID,
    query: str,
    config: KeywordSeamConfig,
    *,
    source_ids: list[UUID] | None = None,
    workspace_key: str | None = None,
) -> SeamResult:
    if not config.enabled:
        return SeamResult(
            name="keyword", enabled=False, state="disabled", reason="disabled in config"
        )

    if source_ids == []:
        return SeamResult(
            name="keyword", enabled=True, state="dormant", reason="explicitly empty source scope"
        )

    tokens = _tokens(query, config.min_token_len)
    if not tokens:
        return SeamResult(
            name="keyword", enabled=True, state="dormant", reason="no searchable terms"
        )

    # Only [a-z0-9]+ tokens reach SQL, joined with OR. Hostile input cannot
    # break the tsquery because it is passed as a parameter.
    q = " | ".join(tokens)
    numeric_refs = _NUMERIC_REF_RE.findall(query)
    numeric_pattern = (
        r"(^|[^0-9])(" + "|".join(re.escape(ref) for ref in numeric_refs) + r")([^0-9]|$)"
        if numeric_refs else None
    )
    rows = conn.execute(
        queries.get("keyword_search"),
        {"q": q, "collection_id": collection_id, "limit": config.limit,
         "source_ids": source_ids, "workspace_key": workspace_key,
         "numeric_pattern": numeric_pattern},
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
