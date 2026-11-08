"""Record stored objects in the ``storage_objects`` catalog.

The catalog gives a backfill and an operator one checksummed index of every
object written through the storage seam. File bytes remain the source of
truth; the catalog row is written in the caller's transaction.
"""

from __future__ import annotations

from app.db import queries
from app.storage.base import StorageObject, StorageProtocol


def record_storage_object(conn, obj: StorageObject, storage: StorageProtocol) -> None:
    conn.execute(
        queries.get("upsert_storage_object"),
        {
            "storage_key": obj.key,
            "size_bytes": obj.size_bytes,
            "sha256": obj.sha256,
            "content_type": obj.content_type,
            "backend": getattr(storage, "backend_name", type(storage).__name__),
        },
    )
