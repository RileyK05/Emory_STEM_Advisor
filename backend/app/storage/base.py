"""Application storage Protocol, object metadata, and error types (Item 26)."""

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StorageObject:
    key: str
    size_bytes: int
    sha256: str
    content_type: str | None = None


class StorageError(Exception):
    """Base error for application storage operations."""


class StorageNotFoundError(StorageError):
    """Raised when an object key does not exist."""


class StorageIntegrityError(StorageError):
    """Raised when an object fails SHA-256 or size integrity verification."""


class StorageProtocol(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StorageObject:
        """Atomically store data under key; returns verified metadata."""
        ...

    def get(self, key: str) -> bytes:
        """Retrieve complete bytes for key with integrity verification."""
        ...

    def exists(self, key: str) -> bool:
        """Check whether key exists in storage."""
        ...

    def delete(self, key: str) -> bool:
        """Delete key; returns True if deleted, False if not found."""
        ...

    def list(self, prefix: str = "") -> list[StorageObject]:
        """List stored objects matching prefix."""
        ...


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
