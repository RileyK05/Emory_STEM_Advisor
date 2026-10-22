"""Ingest orchestration. Never holds a transaction across extract or embed."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import numpy as np
from psycopg import Connection
from psycopg.types.json import Jsonb

from app.config import load_settings
from app.db import queries
from app.embeddings.base import Embedder
from app.ingest.chunker import chunk
from app.ingest.evidence import preserve_indexed_source_evidence
from app.ingest.extract import extract
from app.retrieval.config import RetrievalConfig
from app.util.localpath import io_path
from app.util.workspace import is_valid_workspace_key

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


def _source_id(collection_id: UUID, file_hash: str,
               workspace_key: str | None = None) -> UUID:
    if workspace_key is None:
        # Preserve historical IDs for shared official sources and atlas imports.
        return uuid5(collection_id, file_hash)
    workspace_namespace = uuid5(collection_id, f"workspace:{workspace_key}")
    return uuid5(workspace_namespace, file_hash)


def _locator_id(source_id: UUID, ordinal: int) -> UUID:
    return uuid5(source_id, f"loc:{ordinal}")


def _chunk_id(source_id: UUID, chunker_version: str, chunk_index: int) -> UUID:
    return uuid5(source_id, f"{chunker_version}:{chunk_index}")


def _store_raw(collection_id: UUID, file_hash: str, raw: bytes,
               workspace_key: str | None = None) -> Path:
    """Store content-addressed bytes; private uploads get a per-workspace directory."""
    directory = load_settings().data_dir / "raw" / str(collection_id)
    if workspace_key is not None:
        workspace_dir = hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()
        directory = directory / "private" / workspace_dir
    path = directory / file_hash
    target = io_path(path)
    if not target.exists():
        io_path(directory).mkdir(parents=True, exist_ok=True)
        temporary = io_path(directory / f"{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(raw)
            # Atomic; a concurrent writer of the same hash wrote identical bytes.
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha256(target.read_bytes()).hexdigest() != file_hash:
        raise ValueError("stored raw source failed integrity check")
    return path


def _mark_failed(conn: Connection, source_id: UUID, index_revision: int, exc: Exception) -> None:
    conn.execute(
        queries.get("set_source_failed"),
        {"source_id": source_id, "index_revision": index_revision,
         "error_message": f"{type(exc).__name__}: {exc}"},
    )
    conn.commit()


def _replace_index(conn: Connection, source_id: UUID, index_revision: int, version: str,
                   extracted, spans, chunk_texts: list[str], chunk_ids: list[UUID],
                   vectors: np.ndarray, embed_model: str,
                   on_chunk_transaction: OnChunkTransaction | None,
                   origin_key: str | None, collection_id: UUID,
                   workspace_key: str | None, provenance=None,
                   file_hash: str | None = None) -> None:
    """Swap a source's locators, chunks, and vectors in one transaction.

    A concurrent reindex that claimed a newer revision wins: this attempt writes nothing.
    """
    with conn.transaction():
        publication = None
        if origin_key is not None:
            publication = {
                "collection_id": collection_id,
                "workspace_key": workspace_key,
                "origin_key": origin_key,
                "source_id": source_id,
            }
            # Serialize the full publication transaction, including atlas graph
            # replacement, for all versions of one logical source.
            conn.execute(queries.get("lock_source_origin_publication"), publication)
        current = conn.execute(queries.get("lock_source_index_revision"), {
            "source_id": source_id,
        }).fetchone()
        if current is None or current["index_revision"] != index_revision:
            return
        # The rows about to be deleted still belong to the last successful revision.
        preserve_indexed_source_evidence(conn, source_id)
        conn.execute(queries.get("delete_chunks_for_source"), {"source_id": source_id})
        conn.execute(queries.get("delete_locators_for_source"), {"source_id": source_id})
        with conn.cursor() as cursor:
            cursor.executemany(queries.get("insert_locator"), [{
                "locator_id": _locator_id(source_id, ordinal), "source_id": source_id,
                "ordinal": ordinal, "locator_type": locator.locator_type,
                "label": locator.label, "start_char": locator.start_char,
                "end_char": locator.end_char,
            } for ordinal, locator in enumerate(extracted.locators)])
            cursor.executemany(queries.get("insert_chunk"), [{
                "chunk_id": chunk_ids[chunk_index],
                "source_id": source_id,
                "locator_id": _locator_id(source_id, span.locator_ordinal),
                "chunk_index": chunk_index,
                "start_char": span.start_char,
                "end_char": span.end_char,
                "token_count": span.token_count,
                "text": chunk_texts[chunk_index],
            } for chunk_index, span in enumerate(spans)])
        conn.execute(queries.get("set_source_warnings"), {
            "source_id": source_id, "warnings": Jsonb(list(extracted.warnings)),
            "index_revision": index_revision,
        })
        conn.execute(queries.get("set_source_chunked"), {
            "source_id": source_id, "chunker_version": version,
            "index_revision": index_revision,
        })
        if on_chunk_transaction is not None:
            on_chunk_transaction(conn, source_id, chunk_ids)
        with conn.cursor() as cursor:
            cursor.executemany(queries.get("upsert_chunk_embedding"), [{
                "chunk_id": chunk_ids[chunk_index], "model": embed_model,
                "dim": int(vector.shape[0]), "embedding": vector,
            } for chunk_index, vector in enumerate(vectors)])
        conn.execute(queries.get("set_source_indexed"), {
            "source_id": source_id, "index_revision": index_revision,
        })
        preserve_indexed_source_evidence(conn, source_id)
        if provenance is not None:
            # Verified classification publishes with the indexed version it describes.
            conn.execute(queries.get("upsert_source_provenance"),
                         provenance.row(source_id, file_hash))
        if origin_key is not None:
            conn.execute(queries.get("supersede_source_origin"), publication)
            conn.execute(queries.get("activate_source_origin"), publication)


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
    workspace_key: str | None = None,
    origin_key: str | None = None,
    on_chunk_transaction: OnChunkTransaction | None = None,
    force: bool = False,
    expected_index_revision: int | None = None,
    provenance=None,
) -> UUID:
    """Index one file. `provenance` (`app.ingest.provenance.SourceProvenance`) is a
    verified importer's classification; only trusted import paths pass one."""
    if workspace_key is not None and not is_valid_workspace_key(workspace_key):
        raise ValueError("workspace_key must contain 1-200 characters")
    if origin_key is not None and (not origin_key.strip() or len(origin_key) > 500):
        raise ValueError("origin_key must contain 1-500 non-whitespace characters")
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    source_id = _source_id(collection_id, file_hash, workspace_key)

    existing = conn.execute(
        queries.get("get_source_by_hash"),
        {"collection_id": collection_id, "file_hash": file_hash,
         "workspace_key": workspace_key},
    ).fetchone()
    if (existing is not None and origin_key is not None
            and existing["origin_key"] not in (None, origin_key)):
        raise ValueError("source content is already assigned to a different origin_key")
    if (existing is not None and existing["status"] == "indexed" and existing["is_current"]
            and not force and (origin_key is None or existing["origin_key"] == origin_key)):
        if provenance is not None:
            # Same published content; record (or refresh) its verified metadata.
            with conn.transaction():
                conn.execute(queries.get("upsert_source_provenance"),
                             provenance.row(existing["source_id"], file_hash))
            conn.commit()
        return existing["source_id"]

    raw_path = _store_raw(collection_id, file_hash, raw, workspace_key)
    conn.execute(
        queries.get("upsert_source"),
        {
            "source_id": source_id,
            "collection_id": collection_id,
            "workspace_key": workspace_key,
            "filename": existing["filename"] if force and existing else path.name,
            "content_type": content_type,
            "file_hash": file_hash,
            "raw_path": str(raw_path),
            "origin_key": origin_key,
        },
    )
    conn.commit()
    claim = conn.execute(
        queries.get("claim_source_index_attempt"), {
            "source_id": source_id,
            "expected_index_revision": expected_index_revision,
        }
    ).fetchone()
    conn.commit()
    if claim is None:
        raise ValueError("source index revision changed; reload before reindexing")
    index_revision = claim["index_revision"]

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
        _mark_failed(conn, source_id, index_revision, exc)
        raise

    chunk_texts = [extracted.text[span.start_char : span.end_char] for span in spans]
    chunk_ids = [_chunk_id(source_id, version, chunk_index) for chunk_index in range(len(spans))]
    try:
        # Finish external embedding work before replacing any prior rows, so the
        # previous index stays intact if embedding fails.
        vectors = _validate_vectors(embedder.embed_documents(chunk_texts), len(chunk_texts))
        _replace_index(conn, source_id, index_revision, version, extracted, spans, chunk_texts,
                       chunk_ids, vectors, embed_model, on_chunk_transaction, origin_key,
                       collection_id, workspace_key, provenance, file_hash)
    except Exception as exc:
        _mark_failed(conn, source_id, index_revision, exc)
        raise
    return source_id
