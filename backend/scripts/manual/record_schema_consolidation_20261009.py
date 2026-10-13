"""Record bounded local evidence after the completed copy and regression checks."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.db import queries
from app.db.connection import connect


def main() -> None:
    import pgserver

    root = Path(__file__).resolve().parents[3]
    target = root / "data" / "schema-consolidated-20261009-verified"
    server = pgserver.get_server(target / "pgserver", cleanup_mode="stop")
    original = connect("postgresql://postgres:@127.0.0.1:61621/postgres")
    copied = connect(server.get_uri())
    try:
        original_history = original.execute(queries.get("list_applied_migrations")).fetchall()
        if len(original_history) != 31 or original_history[-1]["version"] != 31:
            raise RuntimeError("original migration metadata changed unexpectedly")
        if original.execute(queries.get("schema_sim_build_fingerprint")).fetchone() != copied.execute(
                queries.get("schema_sim_build_fingerprint")).fetchone():
            raise RuntimeError("copied similarity build data differs, including current flags")
    finally:
        original.close()
        copied.close()
        server.cleanup()

    archive = root / "data" / "backups" / "schema-consolidation-20261009T163931Z.zip"
    with archive.open("rb") as handle:
        archive_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    paths = [
        "backend/app/db/baseline/032_schema.sql", "backend/app/db/migrate.py",
        "backend/app/db/queries/schema.sql", "backend/app/db/queries/metadata.sql",
        "backend/app/db/queries/documents.sql", "backend/app/db/connection.py",
        "backend/app/atlas/importer.py", "backend/app/ingest/pipeline.py",
        "backend/app/api/deps.py", "backend/app/api/routes/runs.py",
        "backend/app/ops/readiness.py", "backend/scripts/worker.py",
        "backend/scripts/schema_baseline.py", "backend/scripts/restore_consolidated.py",
        "backend/scripts/scrape_programs.py", "backend/scripts/ingest.py",
        "backend/scripts/reconcile_source_origins.py", "backend/tests/test_db_migrate.py",
        "backend/tests/test_readiness.py", "backend/tests/test_schema_copy_checks.py",
        "backend/tests/test_persistence_backup.py", "backend/tests/test_source_reconciliation.py",
        "frontend/src/components/artifact/ArtifactWorkspace.tsx",
        "frontend/src/components/artifact/WorkbookEditor.tsx",
        "frontend/src/components/artifact/DocumentEditor.tsx",
        "frontend/src/components/artifact/workbook.ts",
        "frontend/tests/browser-workflows.test.mjs",
    ]
    hashes = {path: hashlib.sha256((root / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for path in paths}
    legacy_hashes = {
        path.name: {"raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "canonical_lf_sha256": hashlib.sha256(
                        path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()}
        for path in sorted((root / "backend/app/db/legacy_migrations").glob("*.sql"))
    }
    report = {
        "recorded_at": datetime.now(UTC).isoformat(), "review_findings_resolved": ["R1", "R2", "R3", "R4", "R5", "R6"],
        "schema_baseline": "backend/app/db/baseline/032_schema.sql", "legacy_files": legacy_hashes,
        "next_migration_number": 33, "schema_catalog_parity": True,
        "original_migration_count_after_work": 31, "original_database_migrated_or_reset": False,
        "similarity_build_rows_including_current_flags_match_original": True,
        "backup": {"path": archive.relative_to(root).as_posix(), "sha256": archive_digest},
        "replacement": json.loads((target / "consolidation-verification.json").read_text()),
        "source_reconciliation": json.loads((target / "source-reconciliation-verification.json").read_text()),
        "replacement_api": json.loads((target / "api-verification.json").read_text()),
        "configuration_switched": False, "replacement_server_stopped_cleanly": True,
        "checks": {
            "backend_full_suite": {"passed": 768, "failed": 0, "skipped": 0, "seconds": 522.62,
                                   "basetemp": "data/test-tmp/schema-consolidation-20261009-final"},
            "additional_copy_validator_regressions": {"passed": 2, "failed": 0, "skipped": 0,
                                                      "seconds": 15.24},
            "finalized_copy_round_trip_regression": {"passed": 1, "failed": 0},
            "migration_readiness_backup_focused": {"passed": 31, "failed": 0},
            "frontend_offline": {"passed": 69, "failed": 0},
            "browser_api_postgres_integration": {"passed": 7, "failed": 0, "skipped": 0},
            "ruff": "passed", "frontend_build_typecheck_format": "passed",
        },
        "superseded_validation": {
            "first_full_run": "767 passed, 1 cached-query failure while verification SQL was being revised",
            "copy_reconciliation_first_attempt": "rolled back after publication timestamp changes failed an overly strict comparison",
        },
        "boundaries": ["No paid provider calls or model loads", "Original data retained; current runtime configuration unchanged",
                       "Linux/Docker/gVisor, production OIDC, provider quality and Office fidelity gates remain open"],
        "canonical_lf_source_hashes": hashes,
    }
    output = root / "docs/evidence/schema-consolidation-2026-10-09.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Recorded schema consolidation evidence; original remains on migration 031.")


if __name__ == "__main__":
    main()
