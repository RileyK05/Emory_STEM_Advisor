"""Build a copy of the advising data that is safe to hand to teammates.

From backend/, with the API and worker stopped:
    python -m scripts.team_snapshot --backup ../data/team/advisor-YYYYMMDD.zip --maintenance-confirmed

The snapshot holds public advising data only. Users, conversations, academic records,
traces and every future table stay out until they are named in SHARED_TABLES. The file
is kept only after a full restore of it succeeds.

A teammate restores it on a fresh clone, where ../data does not exist yet:
    python -m scripts.restore_consolidated SNAPSHOT.zip --data-dir ../data
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from app.config import load_settings
from app.db import queries
from app.ops.backup import BackupError, create_backup
from scripts.backup import _backup_connection
from scripts.restore_consolidated import restore_consolidated

SHARED_TABLES = frozenset({
    # Sources and their indexed text.
    "collections", "sources", "locators", "chunks", "chunk_embeddings",
    "source_provenance", "source_revision_evidence",
    # Courses and the deterministic prerequisite graph.
    "courses", "course_schools", "course_topic_offerings", "course_campus_equivalents",
    "course_changes", "prereq_nodes", "prereq_mentions",
    # Programs. A published rule release names its reviewer in app_users, so sharing
    # one makes the verifying restore fail until that link is dealt with.
    "programs", "program_items", "program_item_evidence", "program_field_evidence",
    "program_course_mentions", "program_rule_releases",
    # Atlas term offerings and curator holds.
    "atlas_offering_snapshots", "atlas_offering_rows", "atlas_offering_capture_files",
    "atlas_requirement_holds",
    # The similarity graph.
    "sim_builds", "sim_clusters", "sim_cluster_members", "sim_courses",
    # Settings a working install needs.
    "quota_policies", "model_price_schedules", "schema_migrations",
})
SHARED_FILES = ("eval/retrieval.jsonl", "programs/programs.jsonl", "atlas/atlas-primary.jsonl")


def build_team_snapshot(conn, *, dsn: str, data_dir: Path, destination: Path) -> dict:
    private = conn.execute(queries.get("team_snapshot_private_sources")).fetchall()
    conn.rollback()
    if private:
        names = ", ".join(row["filename"] for row in private)
        raise BackupError(f"sources without verified provenance would be shared: {names}")
    return create_backup(
        conn, dsn=dsn, data_dir=data_dir, destination=destination, maintenance_confirmed=True,
        shared_tables=SHARED_TABLES, extras=SHARED_FILES,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a shareable copy of the advising data")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument(
        "--maintenance-confirmed", action="store_true",
        help="confirm the API and worker are stopped",
    )
    args = parser.parse_args()
    if not args.maintenance_confirmed:
        parser.exit(2, "team_snapshot requires --maintenance-confirmed after stopping writers\n")
    settings = load_settings()
    destination = args.backup.resolve()
    try:
        with _backup_connection(settings, args.database_url) as (conn, dsn):
            manifest = build_team_snapshot(
                conn, dsn=dsn, data_dir=settings.data_dir.resolve(), destination=destination,
            )
        print("Verifying with a full restore...")
        with tempfile.TemporaryDirectory(
            prefix="snap-", dir=destination.parent, ignore_cleanup_errors=True
        ) as temp:
            report = restore_consolidated(destination, Path(temp) / "restored")
    except BackupError as exc:
        parser.exit(2, f"snapshot error: {exc}\n")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    size = destination.stat().st_size / 1024 / 1024
    print(json.dumps({
        "snapshot": str(destination), "megabytes": round(size, 1),
        "database_mb": round(manifest["database_dump"]["size"] / 1024 / 1024, 1),
        "referenced_files": report["referenced_files"], "rows_verified": report["rows_verified"],
    }, indent=2))


if __name__ == "__main__":
    main()
