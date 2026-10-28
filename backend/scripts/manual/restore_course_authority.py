"""Restore previously stored course authority values from a reviewed comparison manifest.

Manual maintenance only. Defaults to a transaction rollback; --apply commits.
Use only after independently stopping writers and verifying a restorable backup.
"""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.db import queries
from app.db.connection import connect


class _DryRunRollback(Exception):
    pass


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load_checked(path: Path, digest: str) -> dict:
    if _hash(path) != digest.lower():
        raise ValueError(f"checksum mismatch: {path.name}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--comparison-sha256", required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--snapshot-manifest-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--source-revision", type=int, required=True)
    parser.add_argument("--system-identifier", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    comparison = _load_checked(args.comparison, args.comparison_sha256)
    snapshot = _load_checked(args.snapshot_manifest, args.snapshot_manifest_sha256)
    snapshot_dir = Path(snapshot["summary"]["copy"]).resolve()
    expected_files = snapshot["files"]
    actual_files = {str(p.relative_to(snapshot_dir)) for p in snapshot_dir.rglob("*") if p.is_file()}
    if actual_files != set(expected_files):
        raise ValueError("preserved snapshot file set differs from its manifest")
    for relative, expected in expected_files.items():
        path = snapshot_dir / relative
        if path.is_symlink() or path.stat().st_size != expected["bytes"] or _hash(path) != expected["sha256"]:
            raise ValueError("preserved snapshot failed checksum verification")

    rows = comparison["rows"]
    if len(rows) != comparison["count"] or not rows:
        raise ValueError("comparison count does not match its rows")
    ids = [UUID(row["course_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("comparison has duplicate course IDs")
    if any(row["expected_current"] != "atlas" or row["restore_previous"] != "catalog" for row in rows):
        raise ValueError("comparison contains an unexpected authority transition")
    source_ids = {UUID(row["source_id"]) for row in rows}
    collections = {UUID(row["collection_id"]) for row in rows}
    if len(source_ids) != 1 or len(collections) != 1:
        raise ValueError("comparison spans multiple sources or collections")
    source_id, collection_id = next(iter(source_ids)), next(iter(collections))

    with connect(args.database_url) as conn:
        conn.rollback()
        try:
            with conn.transaction():
                conn.execute(queries.get("authority_restore_serializable"))
                identity = conn.execute(queries.get("authority_restore_database_identity")).fetchone()
                if identity["system_identifier"] != args.system_identifier:
                    raise ValueError("database system identifier does not match")
                source = conn.execute(queries.get("authority_restore_lock_source"), {
                    "source_id": source_id,
                }).fetchone()
                if (source is None or source["collection_id"] != collection_id
                        or source["file_hash"] != args.source_sha256.lower()
                        or source["index_revision"] != args.source_revision
                        or source["status"] != "indexed" or not source["is_current"]
                        or source["content_type"] != "application/x-course-atlas+jsonl"):
                    raise ValueError("current source identity or revision does not match")
                locked = conn.execute(queries.get("authority_restore_lock_courses"), {
                    "course_ids": ids,
                }).fetchall()
                by_id = {str(row["course_id"]): row for row in locked}
                if len(by_id) != len(rows):
                    raise ValueError("course identities no longer match")
                for item in rows:
                    current = by_id[item["course_id"]]
                    for key in ("collection_id", "source_id", "locator_id", "code", "admin_revision", "lifecycle"):
                        if str(current[key]) != str(item[key]):
                            raise ValueError(f"course identity or revision changed: {item['code']}")
                    if current["authority"] != item["expected_current"]:
                        raise ValueError(f"course authority changed: {item['code']}")
                for item in rows:
                    params = dict(item)
                    for key in ("course_id", "collection_id", "source_id", "locator_id"):
                        params[key] = UUID(item[key])
                    if conn.execute(queries.get("authority_restore_update_course"), params).fetchone() is None:
                        raise ValueError(f"course update guard failed: {item['code']}")
                new_revision = conn.execute(queries.get("advance_atlas_parser_revision"), {
                    "source_id": source_id,
                }).fetchone()["index_revision"]
                if not args.apply:
                    raise _DryRunRollback
        except _DryRunRollback:
            print(f"Validated {len(rows)} authority restorations; source revision {args.source_revision} -> {new_revision}; "
                  "rolled back dry run")
        else:
            print(f"Restored {len(rows)} authority values; source revision {args.source_revision} -> {new_revision}; "
                  "committed")


if __name__ == "__main__":
    main()
