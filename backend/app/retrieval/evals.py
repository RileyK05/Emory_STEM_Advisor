"""Eval runner: recall@k per seam and fused, on exact (filename, label)."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from psycopg import Connection

from app.db import queries
from app.embeddings.base import Embedder
from app.retrieval.config import RetrievalConfig
from app.retrieval.retrieve import retrieve
from app.retrieval.seams.embed import embed_seam
from app.retrieval.seams.keyword import keyword_seam
from app.retrieval.seams.prereq import prereq_seam
from app.retrieval.types import SeamResult


@dataclass(frozen=True)
class EvalCase:
    id: str
    collection: str
    query: str
    expected: tuple[tuple[str, str], ...]


@dataclass
class EvalSummary:
    total: int = 0
    resolved: int = 0
    unresolved: list[str] = field(default_factory=list)
    seam_recall: dict[str, float] = field(default_factory=dict)
    fused_recall: float = 0.0
    fusion_beats_best_single: bool = False


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        cases.append(
            EvalCase(
                id=record["id"],
                collection=record["collection"],
                query=record["query"],
                expected=tuple(
                    (item["filename"], item["locator_label"])
                    for item in record["expected"]
                ),
            )
        )
    return cases


def _collection_id(conn: Connection, name: str) -> UUID | None:
    row = conn.execute(
        queries.get("get_collection_by_name"), {"name": name}
    ).fetchone()
    return row["collection_id"] if row else None


def _labels_for_chunks(conn: Connection, chunk_ids: list[UUID]) -> set[tuple[str, str]]:
    if not chunk_ids:
        return set()
    rows = conn.execute(
        queries.get("chunk_labels"), {"chunk_ids": chunk_ids}
    ).fetchall()
    return {(row["source_name"], row["locator_label"]) for row in rows}


def _recall(found: set[tuple[str, str]], expected: set[tuple[str, str]]) -> float:
    if not expected:
        return 0.0
    return len(found & expected) / len(expected)


def _expected_exist(conn: Connection, collection_id: UUID, expected) -> bool:
    for filename, label in expected:
        row = conn.execute(
            queries.get("exists_chunk_label"),
            {"collection_id": collection_id, "filename": filename, "locator_label": label},
        ).fetchone()
        if row is None:
            return False
    return True


def run_eval(
    conn: Connection,
    cases: list[EvalCase],
    *,
    embedder: Embedder,
    embed_model: str,
    config: RetrievalConfig,
) -> EvalSummary:
    summary = EvalSummary(total=len(cases))
    k = config.fusion.cited_set_size
    seam_recall_sum: dict[str, float] = {}
    fused_recall_sum = 0.0

    for case in cases:
        collection_id = _collection_id(conn, case.collection)
        if collection_id is None or not _expected_exist(
            conn, collection_id, case.expected
        ):
            summary.unresolved.append(case.id)
            continue
        summary.resolved += 1
        expected = set(case.expected)

        seam_results: list[SeamResult] = [
            keyword_seam(conn, collection_id, case.query, config.seams.keyword),
            prereq_seam(conn, collection_id, case.query, config.seams.prereq),
        ]
        if config.seams.embed.enabled:
            seam_results.append(
                embed_seam(
                    conn,
                    collection_id,
                    case.query,
                    embed_model,
                    config.seams.embed,
                    embedder=embedder,
                )
            )

        # Seams and fusion are compared at the same k.
        for result in seam_results:
            top = result.candidates[:k]
            found = _labels_for_chunks(conn, [c.chunk_id for c in top])
            seam_recall_sum[result.name] = seam_recall_sum.get(result.name, 0.0) + _recall(
                found, expected
            )

        outcome = retrieve(
            conn,
            collection_id=collection_id,
            query=case.query,
            embedder=embedder,
            embed_model=embed_model,
            config=config,
        )
        if hasattr(outcome, "chunks"):
            found = {(c.source_name, c.locator_label) for c in outcome.chunks}
            fused_recall_sum += _recall(found, expected)

    denominator = summary.resolved or 1
    summary.seam_recall = {
        name: total / denominator for name, total in seam_recall_sum.items()
    }
    summary.fused_recall = fused_recall_sum / denominator
    best_single = max(summary.seam_recall.values(), default=0.0)
    summary.fusion_beats_best_single = summary.fused_recall > best_single
    return summary
