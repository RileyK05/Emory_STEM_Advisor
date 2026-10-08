"""Ingest orchestration. Never holds a transaction across extract or embed."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid5

import numpy as np
from psycopg import Connection

from app.config import load_settings
from app.db import queries
from app.embeddings.base import Embedder
from app.ingest.chunker import chunk
from app.ingest.extract import extract
from app.retrieval.config import RetrievalConfig

# Called inside the transaction that inserts locators and chunks. Receives the
# connection, the source id, and the inserted chunk ids in chunk_index order.
OnChunkTransaction = Callable[[Connection, UUID, list[UUID]], None]

_NORM_TOLERANCE = 1e-3


class EmbeddingError(ValueError):
    """Embedder output was malformed (wrong dim or not unit-norm)."""


def _validate_vectors(vectors: np.ndarray, expected: int) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if expected == 0:
        return vectors
    if vectors.shape[0] != expected:
        raise EmbeddingError(
            f"expected {expected} vectors, got {vectors.shape[0]}"
        )
    if vectors.ndim != 2 or vectors.shape[1] == 0:
        raise EmbeddingError("vectors must be a non-empty (n, dim) array")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.all(np.abs(norms - 1.0) <= _NORM_TOLERANCE):
        raise EmbeddingError("vectors are not L2-normalized (tolerance 1e-3)")
    return vectors


def _source_id(collection_id: UUID, file_hash: str) -> UUID:
    return uuid5(collection_id, file_hash)


def _locator_id(source_id: UUID, ordinal: int) -> UUID:
    return uuid5(source_id, f"loc:{ordinal}")


def _chunk_id(source_id: UUID, chunker_version: str, chunk_index: int) -> UUID:
    return uuid5(source_id, f"{chunker_version}:{chunk_index}")


def _store_raw(collection_id: UUID, file_hash: str, raw: bytes) -> Path:
    directory = load_settings().data_dir / "raw" / str(collection_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_hash
    path.write_bytes(raw)
    return path


def ingest_file(
    conn: Connection,
    *,
    collection_id: UUID,
    path: Path,
    content_type: str,
    count_tokens: Callable[[str], int],
    embedder: Embedder,
    embed_model: str,
    config: RetrievalConfig,
    on_chunk_transaction: OnChunkTransaction | None = None,
) -> UUID:
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    source_id = _source_id(collection_id, file_hash)

    existing = conn.execute(
        queries.get("get_source_by_hash"),
        {"collection_id": collection_id, "file_hash": file_hash},
    ).fetchone()
    if existing is not None and existing["status"] == "indexed":
        return existing["source_id"]

    raw_path = _store_raw(collection_id, file_hash, raw)
    conn.execute(
        queries.get("upsert_source"),
        {
            "source_id": source_id,
            "collection_id": collection_id,
            "filename": path.name,
            "content_type": content_type,
            "file_hash": file_hash,
            "raw_path": str(raw_path),
        },
    )
    conn.commit()

    version = config.chunking.chunker_version
    try:
        extracted = extract(
            raw,
            content_type,
            lines_per_locator=config.chunking.text_lines_per_locator,
        )
        spans = chunk(
            extracted.text,
            extracted.locators,
            count_tokens=count_tokens,
            target_tokens=config.chunking.target_tokens,
            overlap_tokens=config.chunking.overlap_tokens,
        )
    except Exception as exc:
        conn.execute(
            queries.get("set_source_failed"),
            {"source_id": source_id, "error_message": f"{type(exc).__name__}: {exc}"},
        )
        conn.commit()
        raise

    with conn.transaction():
        conn.execute(
            queries.get("delete_chunks_for_source"), {"source_id": source_id}
        )
        conn.execute(
            queries.get("delete_locators_for_source"), {"source_id": source_id}
        )
        for ordinal, locator in enumerate(extracted.locators):
            conn.execute(
                queries.get("insert_locator"),
                {
                    "locator_id": _locator_id(source_id, ordinal),
                    "source_id": source_id,
                    "ordinal": ordinal,
                    "locator_type": locator.locator_type,
                    "label": locator.label,
                    "start_char": locator.start_char,
                    "end_char": locator.end_char,
                },
            )
        chunk_texts = [
            extracted.text[span.start_char : span.end_char] for span in spans
        ]
        chunk_ids = [
            _chunk_id(source_id, version, chunk_index)
            for chunk_index in range(len(spans))
        ]
        for chunk_index, span in enumerate(spans):
            conn.execute(
                queries.get("insert_chunk"),
                {
                    "chunk_id": chunk_ids[chunk_index],
                    "source_id": source_id,
                    "locator_id": _locator_id(source_id, span.locator_ordinal),
                    "chunk_index": chunk_index,
                    "start_char": span.start_char,
                    "end_char": span.end_char,
                    "token_count": span.token_count,
                    "text": chunk_texts[chunk_index],
                },
            )
        conn.execute(
            queries.get("set_source_chunked"),
            {"source_id": source_id, "chunker_version": version},
        )
        if on_chunk_transaction is not None:
            on_chunk_transaction(conn, source_id, chunk_ids)

    try:
        vectors = _validate_vectors(
            embedder.embed_documents(chunk_texts), len(chunk_texts)
        )
        with conn.transaction():
            for chunk_index, vector in enumerate(vectors):
                conn.execute(
                    queries.get("upsert_chunk_embedding"),
                    {
                        "chunk_id": _chunk_id(source_id, version, chunk_index),
                        "model": embed_model,
                        "dim": int(vector.shape[0]),
                        "embedding": vector,
                    },
                )
        conn.execute(queries.get("set_source_indexed"), {"source_id": source_id})
        conn.commit()
    except Exception as exc:
        conn.execute(
            queries.get("set_source_failed"),
            {"source_id": source_id, "error_message": f"{type(exc).__name__}: {exc}"},
        )
        conn.commit()
        raise
    return source_id
