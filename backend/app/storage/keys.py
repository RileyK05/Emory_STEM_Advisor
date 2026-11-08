"""Object-key helpers shared by the storage seam and its callers.

Keys are backend-neutral, POSIX-style references relative to ``DATA_DIR``.
Legacy rows still hold absolute paths, so every reader resolves a reference
through :func:`stored_path`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.util.localpath import io_path


def workspace_directory(workspace_key: str) -> str:
    """A stable, opaque directory for one workspace's private objects."""
    return hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()


def artifact_original_key(workspace_key: str, digest: str) -> str:
    return f"artifacts/originals/{workspace_directory(workspace_key)}/{digest}"


def raw_source_key(
    collection_id: str,
    file_hash: str,
    workspace_key: str | None = None,
) -> str:
    key = f"raw/{collection_id}"
    if workspace_key is not None:
        key = f"{key}/private/{workspace_directory(workspace_key)}"
    return f"{key}/{file_hash}"


def is_storage_key(reference: str) -> bool:
    """True for a backend-neutral key; False for a legacy absolute path."""
    return not Path(reference).is_absolute()


def stored_path(reference: str, data_dir: Path) -> Path:
    """Resolve a database file reference to the local path that holds its bytes."""
    path = Path(reference)
    if path.is_absolute():
        return io_path(path)
    return io_path(Path(data_dir) / path)
