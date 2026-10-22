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
    root = data_dir or (Path(settings.data_dir) if settings and hasattr(settings, "data_dir") else Path("data"))
    local_adapter = LocalStorageAdapter(root / "storage")

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

