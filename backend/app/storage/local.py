"""Local filesystem storage adapter with atomic publication and checksums."""

import json
from pathlib import Path
from uuid import uuid4

from app.storage.base import (
    StorageIntegrityError,
    StorageNotFoundError,
    StorageObject,
    StorageProtocol,
    compute_sha256,
)

_META_SUFFIX = ".meta.json"


class LocalStorageAdapter(StorageProtocol):
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.root_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        clean_key = key.lstrip("/\\")
        path = (self.root_dir / clean_key).resolve()
        if not path.is_relative_to(self.root_dir):
            raise ValueError(f"storage key path traversal attempted: {key}")
        return path

    @staticmethod
    def _meta_path(path: Path) -> Path:
        return path.with_name(path.name + _META_SUFFIX)

    def _read_meta(self, path: Path) -> dict:
        meta = self._meta_path(path)
        if not meta.is_file():
            return {}
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StorageObject:
        sha256 = compute_sha256(data)
        dest = self._resolve_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)

        tmp_file = self.tmp_dir / f"{uuid4().hex}.tmp"
        try:
            with open(tmp_file, "wb") as f:
                f.write(data)

            # Atomic rename / replace
            tmp_file.replace(dest)
            meta_tmp = self.tmp_dir / f"{uuid4().hex}.meta"
            meta_tmp.write_text(json.dumps({
                "sha256": sha256,
                "size_bytes": len(data),
                "content_type": content_type,
            }), encoding="utf-8")
            meta_tmp.replace(self._meta_path(dest))
        finally:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)

        return StorageObject(
            key=key,
            size_bytes=len(data),
            sha256=sha256,
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        path = self._resolve_path(key)
        if not path.is_file():
            raise StorageNotFoundError(f"storage key not found: {key}")

        data = path.read_bytes()
        meta = self._read_meta(path)
        expected = meta.get("sha256")
        expected_size = meta.get("size_bytes")
        if expected and compute_sha256(data) != expected:
            raise StorageIntegrityError(f"storage checksum mismatch for key: {key}")
        if isinstance(expected_size, int) and len(data) != expected_size:
            raise StorageIntegrityError(f"storage size mismatch for key: {key}")
        return data

    def exists(self, key: str) -> bool:
        try:
            path = self._resolve_path(key)
            return path.is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> bool:
        try:
            path = self._resolve_path(key)
            if path.is_file():
                path.unlink()
                self._meta_path(path).unlink(missing_ok=True)
                return True
            return False
        except ValueError:
            return False

    def list(self, prefix: str = "") -> list[StorageObject]:
        results = []
        clean_prefix = prefix.lstrip("/\\")
        for p in self.root_dir.rglob("*"):
            if p.is_file() and not p.is_relative_to(self.tmp_dir):
                if p.name.endswith(_META_SUFFIX):
                    continue
                rel_key = p.relative_to(self.root_dir).as_posix()
                if rel_key.startswith(clean_prefix):
                    data = p.read_bytes()
                    meta = self._read_meta(p)
                    results.append(StorageObject(
                        key=rel_key,
                        size_bytes=len(data),
                        sha256=meta.get("sha256") or compute_sha256(data),
                        content_type=meta.get("content_type"),
                    ))
        return results
