"""Move legacy absolute file references onto storage-seam object keys.

From backend/, with the API and worker stopped:

    python -m scripts.backfill_storage [--limit 500] [--dry-run]

Every object is read, checked against the SHA-256 the database already holds,
written through the storage seam, and recorded in ``storage_objects``. The row
is committed one at a time, so an interrupted run resumes where it stopped:
migrated rows no longer match the legacy-path queries. Legacy files are left
in place; this tool never deletes bytes.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from app.config import load_settings
from app.db import queries
from app.ops.backup import BackupError
from app.storage.catalog import record_storage_object
from app.storage.factory import build_storage
from app.storage.keys import (
    artifact_original_key,
    is_storage_key,
    raw_source_key,
    stored_path,
)
from scripts.backup import _backup_connection


def _read_checked(reference: str, expected: str, data_dir: Path, kind: str) -> bytes:
    path = stored_path(reference, data_dir)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise BackupError(f"{kind} file is unavailable: {reference}") from exc
    if hashlib.sha256(data).hexdigest() != expected:
        raise BackupError(f"{kind} file failed SHA-256 verification: {reference}")
    return data


def backfill(
    conn,
    *,
    data_dir: Path,
    limit: int = 500,
    dry_run: bool = False,
) -> dict:
    storage = build_storage(load_settings())
    moved_sources = moved_artifacts = 0
    for row in conn.execute(
        queries.get("list_sources_for_storage_backfill"), {"limit": limit}
    ).fetchall():
        reference = row["raw_path"]
        if is_storage_key(reference):
            continue
        data = _read_checked(reference, row["file_hash"], data_dir, "source")
        key = raw_source_key(
            str(row["collection_id"]), row["file_hash"], row["workspace_key"]
        )
        if dry_run:
            print(f"source {row['source_id']}: {reference} -> {key}")
            moved_sources += 1
            continue
        obj = storage.put(key, data, content_type=row["content_type"])
        if obj.sha256 != row["file_hash"]:
            raise BackupError("stored source failed SHA-256 verification")
        record_storage_object(conn, obj, storage)
        conn.execute(
            queries.get("update_source_storage_path"),
            {"source_id": row["source_id"], "raw_path": key},
        )
        conn.commit()
        moved_sources += 1
    for row in conn.execute(
        queries.get("list_artifacts_for_storage_backfill"), {"limit": limit}
    ).fetchall():
        reference = row["original_path"]
        if is_storage_key(reference):
            continue
        digest = row["original_sha256"]
        if not digest:
            raise BackupError(f"artifact has no stored checksum: {row['artifact_id']}")
        data = _read_checked(reference, digest, data_dir, "artifact")
        key = artifact_original_key(row["workspace_key"], digest)
        if dry_run:
            print(f"artifact {row['artifact_id']}: {reference} -> {key}")
            moved_artifacts += 1
            continue
        obj = storage.put(key, data)
        if obj.sha256 != digest:
            raise BackupError("stored artifact original failed SHA-256 verification")
        record_storage_object(conn, obj, storage)
        conn.execute(
            queries.get("update_artifact_storage_reference"),
            {"artifact_id": row["artifact_id"], "storage_key": key},
        )
        conn.commit()
        moved_artifacts += 1
    return {"sources": moved_sources, "artifacts": moved_artifacts, "dryRun": dry_run}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.exit(2, "--limit must be positive\n")
    settings = load_settings()
    with _backup_connection(settings, None) as (conn, _dsn):
        report = backfill(
            conn,
            data_dir=settings.data_dir.resolve(),
            limit=args.limit,
            dry_run=args.dry_run,
        )
    print(
        f"moved {report['sources']} source files and {report['artifacts']} artifact "
        f"originals{' (dry run)' if report['dryRun'] else ''}; rerun to continue"
    )


if __name__ == "__main__":
    main()
