"""Storage factory supporting local, blob, and dual-read transition."""

from pathlib import Path

from app.config import Settings
from app.storage.base import StorageProtocol
from app.storage.local import LocalStorageAdapter


def build_storage(
    settings: Settings | None = None,
    *,
    data_dir: Path | None = None,
) -> StorageProtocol:
    root = (
        Path(data_dir)
        if data_dir is not None
        else Path(settings.data_dir) if settings and hasattr(settings, "data_dir")
        else Path("data")
    )
    # Keys are relative to DATA_DIR, so `raw/...` and `artifacts/originals/...`
    # land where the existing layout and the backup archive expect them.
    local_adapter = LocalStorageAdapter(root)

    backend = getattr(settings, "storage_backend", "local").lower() if settings else "local"

    if backend in {"blob", "dual"}:
        # BlobStorageAdapter is still the in-process dictionary used by tests.
        # Selecting it in production would report success while losing every
        # write on restart, so the release gate must be explicit rather than
        # silently substituting volatile storage.
        raise RuntimeError(
            "remote blob storage is not configured; use STORAGE_BACKEND=local "
            "until an Azure Blob provider is installed"
        )
    return local_adapter

