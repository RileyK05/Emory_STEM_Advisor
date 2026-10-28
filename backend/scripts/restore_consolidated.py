"""Restore a verified backup into a separate, new baseline database and data directory.

Run from backend/: python -m scripts.restore_consolidated BACKUP --data-dir NEW_DIRECTORY
The original database is never opened. The new server is stopped after validation.
"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from app.db import migrate, queries
from app.db.connection import connect
from app.ops.backup import _load_and_verify, _pg_bin, _pg_env, _referenced_files, _run


def restore_consolidated(archive_path: Path, target_data: Path) -> dict:
    target_data = target_data.resolve()
    if target_data.exists():
        raise ValueError("replacement data directory must not already exist")
    target_data.parent.mkdir(parents=True, exist_ok=True)
    import pgserver

    with tempfile.TemporaryDirectory(prefix="baseline-restore-", dir=target_data.parent) as temp_name:
        manifest, dump = _load_and_verify(archive_path.resolve(), Path(temp_name) / "stage")
        target_data.mkdir()
        server = pgserver.get_server(target_data / "pgserver", cleanup_mode="stop")
        target = reference = None
        try:
            target = connect(server.get_uri())
            if not target.execute(queries.get("backup_target_empty")).fetchone()["is_empty"]:
                raise ValueError("replacement database must be empty before migrations")
            version = int(target.execute(queries.get("backup_server_version")).fetchone()[
                "server_version_num"])
            if version // 10000 != int(manifest["postgres_server_version_num"]) // 10000:
                raise ValueError("replacement PostgreSQL major version must match the backup")
            target.commit()
            target.autocommit = True
            target.execute(queries.get("schema_create_baseline_check_database"))
            target.autocommit = False
            reference_dsn = server.get_uri(database="baseline_check")
            restore_tool = str(_pg_bin("pg_restore"))
            _run([restore_tool, "--single-transaction", "--exit-on-error", "--no-owner",
                  "--no-acl", "--dbname=baseline_check", str(dump)], _pg_env(reference_dsn))
            reference = connect(reference_dsn)
            # Upgrade the isolated restored reference using the genuine legacy path.
            # This also carries data backfills into the consolidated copy exactly.
            migrate.apply_migrations(reference)
            reference.commit()
            reference.execute(queries.get("schema_prepare_fingerprints"), {})
            expected = reference.execute(queries.get("schema_table_fingerprints")).fetchall()
            lifecycle = reference.execute(queries.get("schema_source_lifecycle")).fetchall()
            migrate.apply_migrations(target)
            target.commit()
            target.execute(queries.get("schema_prepare_fingerprints"), {})
            if reference.execute(queries.get("schema_catalog")).fetchall() != target.execute(
                    queries.get("schema_catalog")).fetchall():
                raise ValueError("backup upgrade and consolidated schema differ")
            # Migrations seed these tables on a fresh install. The upgraded backup
            # supplies their complete contents, including any operator changes.
            target.execute(queries.get("schema_clear_fresh_feature_flags"))
            target.execute(queries.get("schema_clear_fresh_quota_policies"))
            target.commit()
            # Copy the upgraded reference, not the original pre-upgrade dump:
            # later migrations can drop tables or transform rows (e.g. v033).
            upgraded_data = Path(temp_name) / "upgraded-data.dump"
            _run([str(_pg_bin("pg_dump")), "--data-only", "--format=custom",
                  "--no-owner", "--no-acl", "--exclude-table=public.schema_migrations",
                  f"--file={upgraded_data}"], _pg_env(reference_dsn))
            # The target is empty and isolated. FK triggers are checked explicitly
            # afterwards because cyclic relationships cannot be COPY-ordered.
            _run([restore_tool, "--data-only", "--disable-triggers", "--single-transaction",
                  "--exit-on-error", "--no-owner", "--no-acl", "--dbname=postgres",
                  str(upgraded_data)], _pg_env(server.get_uri()))
            with target.transaction():
                target.execute(queries.get("schema_retire_copy_sources"))
                for row in lifecycle:
                    target.execute(queries.get("schema_restore_source_lifecycle"), row)
                target.execute(queries.get("schema_foreign_key_violations"), {})
                actual = target.execute(queries.get("schema_table_fingerprints")).fetchall()
                if expected != actual:
                    raise ValueError("replacement rows differ from the restored backup")
                if target.execute(queries.get("schema_source_lifecycle")).fetchall() != lifecycle:
                    raise ValueError("replacement source lifecycle differs from the upgrade path")
                migrate.verify_release_history(target.execute(
                    queries.get("list_applied_migrations")).fetchall())
                for item in manifest.get("files", []):
                    rel = item["path"]
                    final = target_data / rel
                    final.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(Path(temp_name) / "stage" / "files" / rel, final)
                    for source_id in item.get("source_ids", []):
                        target.execute(queries.get("backup_restore_source_path"), {
                            "source_id": UUID(source_id), "raw_path": str(final),
                        })
                    for artifact_id in item.get("artifact_ids", []):
                        target.execute(queries.get("backup_restore_artifact_path"), {
                            "artifact_id": UUID(artifact_id), "original_path": str(final),
                        })
                    for ref in item.get("capture_file_refs", []):
                        target.execute(queries.get("backup_restore_atlas_capture_path"), {
                            "source_id": UUID(ref["source_id"]),
                            "capture_set_hash": ref["capture_set_hash"],
                            "capture_hash": ref["capture_hash"], "raw_path": str(final),
                        })
                    for ref in item.get("hold_capture_refs", []):
                        target.execute(queries.get("backup_restore_requirement_hold_capture_path"), {
                            "hold_id": UUID(ref["hold_id"]), "capture_hash": ref["capture_hash"],
                            "raw_path": str(final),
                        })
                copied_files = _referenced_files(target, target_data)
                if len(copied_files) != len(manifest["files"]):
                    raise ValueError("replacement file references differ from the verified archive")
            target.commit()
            reference.close()
            reference = None
            target.autocommit = True
            target.execute(queries.get("schema_drop_baseline_check_database"))
            target.autocommit = False
            report = {"data_dir": str(target_data), "baseline": migrate._BASELINE_PATH.name,
                      "tables_verified": len(actual), "referenced_files": len(manifest["files"]),
                      "rows_verified": sum(row["row_count"] for row in actual),
                      "row_fingerprints_match": True, "foreign_keys_valid": True,
                      "source_lifecycle_matches_upgrade": True, "original_database_opened": False}
            (target_data / "consolidation-verification.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return report
        finally:
            if reference is not None:
                reference.close()
            if target is not None:
                target.close()
            server.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(restore_consolidated(args.backup, args.data_dir), indent=2))


if __name__ == "__main__":
    main()
