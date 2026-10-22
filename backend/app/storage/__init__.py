"""Application storage package (Item 26)."""

from app.storage.base import (
    StorageError,
    StorageIntegrityError,
    StorageNotFoundError,
    StorageObject,
    StorageProtocol,
    compute_sha256,
)
from app.storage.blob import BlobStorageAdapter
from app.storage.dual import DualReadStorageAdapter
from app.storage.factory import build_storage
from app.storage.local import LocalStorageAdapter

__all__ = [
    "BlobStorageAdapter",
    "DualReadStorageAdapter",
    "LocalStorageAdapter",
    "StorageError",
    "StorageIntegrityError",
    "StorageNotFoundError",
    "StorageObject",
    "StorageProtocol",
    "build_storage",
    "compute_sha256",
]

