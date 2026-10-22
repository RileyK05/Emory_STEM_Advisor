"""Copy indexed chunk text so a later reindex cannot erase that revision."""

from __future__ import annotations

import hashlib
from uuid import UUID

from app.db import queries


def preserve_indexed_source_evidence(conn, source_id: UUID) -> int:
    """Store the chunks that belong to the source's last successful index.

    A second call does not rewrite a revision that was already stored. Chunks
    from a newer index stay under their own revision.
    """
    rows = conn.execute(
        queries.get("list_chunks_at_indexed_revision"), {"source_id": source_id},
    ).fetchall()
    if not rows:
        return 0
    payload = [{
        "source_id": source_id,
        "index_revision": row["indexed_content_revision"],
        "chunk_id": row["chunk_id"],
        "chunk_index": row["chunk_index"],
        "locator_label": row["label"],
        "text": row["text"],
        "text_digest": hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
    } for row in rows]
    with conn.cursor() as cursor:
        cursor.executemany(queries.get("insert_source_revision_evidence"), payload)
    return len(payload)
