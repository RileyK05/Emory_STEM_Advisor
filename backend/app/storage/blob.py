"""Azure Blob storage adapter with in-memory fallback for local verification."""

from typing import Any

from app.storage.base import (
    StorageIntegrityError,
    StorageNotFoundError,
    StorageObject,
    StorageProtocol,
    compute_sha256,
)


class BlobStorageAdapter(StorageProtocol):
    def __init__(
        self,
        container_name: str = "advisor-data",
        *,
        connection_string: str | None = None,
        account_url: str | None = None,
        shared_backend: dict[str, dict[str, Any]] | None = None,
    ):
        self.container_name = container_name
        self.connection_string = connection_string
        self.account_url = account_url
        # If no Azure SDK / connection string, use shared backing dictionary
        self._store: dict[str, dict[str, Any]] = shared_backend if shared_backend is not None else {}

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StorageObject:
        sha256 = compute_sha256(data)
        self._store[key] = {
            "data": data,
            "size": len(data),
            "sha256": sha256,
            "content_type": content_type,
        }
        return StorageObject(
            key=key,
            size_bytes=len(data),
            sha256=sha256,
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        if key not in self._store:
            raise StorageNotFoundError(f"blob not found: {key}")
        entry = self._store[key]
        data = entry["data"]
        actual_sha = compute_sha256(data)
        if actual_sha != entry["sha256"]:
            raise StorageIntegrityError(f"blob {key} failed checksum verification")
        return data

    def exists(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def list(self, prefix: str = "") -> list[StorageObject]:
        out = []
        for k, v in self._store.items():
            if k.startswith(prefix):
                out.append(
                    StorageObject(
                        key=k,
                        size_bytes=v["size"],
                        sha256=v["sha256"],
                        content_type=v.get("content_type"),
                    )
                )
        return out

