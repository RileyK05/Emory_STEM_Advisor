import logging

from app.storage.base import (
    StorageError,
    StorageNotFoundError,
    StorageObject,
    StorageProtocol,
)

logger = logging.getLogger(__name__)


class DualReadStorageAdapter(StorageProtocol):
    def __init__(
        self,
        primary: StorageProtocol,
        secondary: StorageProtocol,
        *,
        write_both: bool = True,
    ):
        self.primary = primary
        self.secondary = secondary
        self.write_both = write_both

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StorageObject:
        obj = self.primary.put(key, data, content_type=content_type)
        if self.write_both:
            try:
                self.secondary.put(key, data, content_type=content_type)
            except StorageError as exc:
                # A dual write is only successful when every configured durable
                # copy succeeds. Reporting success here loses the only copy when
                # the primary is an in-memory/test adapter.
                logger.error("Dual-read secondary write failed for key %s: %s", key, exc)
                raise
        return obj

    def get(self, key: str) -> bytes:
        try:
            return self.primary.get(key)
        except StorageNotFoundError:
            # Dual-read fallback: read from secondary and backfill into primary
            data = self.secondary.get(key)
            try:
                self.primary.put(key, data)
            except StorageError as exc:
                logger.warning("Dual-read primary backfill failed for key %s: %s", key, exc)
            return data

    def exists(self, key: str) -> bool:
        return self.primary.exists(key) or self.secondary.exists(key)

    def delete(self, key: str) -> bool:
        deleted_primary = self.primary.delete(key)
        deleted_secondary = self.secondary.delete(key)
        return deleted_primary or deleted_secondary

    def list(self, prefix: str = "") -> list[StorageObject]:
        primary_objects = {obj.key: obj for obj in self.primary.list(prefix)}
        secondary_objects = self.secondary.list(prefix)
        for obj in secondary_objects:
            if obj.key not in primary_objects:
                primary_objects[obj.key] = obj
        return list(primary_objects.values())
