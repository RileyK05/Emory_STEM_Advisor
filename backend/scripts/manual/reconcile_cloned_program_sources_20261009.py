"""Reconcile recognized legacy program versions on the verified local copy only."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from app.db import migrate, queries
from app.db.connection import connect
from scripts.reconcile_source_origins import provenance_origin_key, reconcile_origins


def main() -> None:
    import pgserver

    root = Path(__file__).resolve().parents[3]
    target = root / "data" / "schema-consolidated-20261009-verified"
    verification = json.loads((target / "consolidation-verification.json").read_text())
    if not verification.get("row_fingerprints_match") or not verification.get("foreign_keys_valid"):
        raise RuntimeError("replacement copy is not verified")
    server = pgserver.get_server(target / "pgserver", cleanup_mode="stop")
    conn = connect(server.get_uri())
    try:
        migrate.verify_release_history(conn.execute(
            queries.get("list_applied_migrations")).fetchall())
        conn.execute(queries.get("schema_prepare_fingerprints"), {})
        before = [row for row in conn.execute(queries.get("schema_table_fingerprints")).fetchall()
                  if row["table_name"] != "sources"]
        source_before = conn.execute(queries.get("schema_source_content_fingerprint")).fetchone()
        groups = defaultdict(lambda: defaultdict(list))
        unrecognized = 0
        for row in conn.execute(queries.get("schema_legacy_program_sources")).fetchall():
            raw = Path(row["raw_path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["file_hash"]:
                raise RuntimeError("legacy source raw SHA-256 mismatch")
            origin = provenance_origin_key(raw)
            if origin is None:
                unrecognized += 1
                continue
            groups[row["collection_id"]][origin].append(row)
        count = retired = origins = 0
        with conn.transaction():
            for collection_id, collection_groups in groups.items():
                manifest_groups = []
                for origin, rows in sorted(collection_groups.items()):
                    # The named query orders by acquisition-row creation and UUID;
                    # all rows were indexed and their immutable raw bytes verified.
                    manifest_groups.append({
                        "origin_key": origin,
                        "source_sha256s": [row["file_hash"] for row in rows],
                        "current_sha256": rows[-1]["file_hash"],
                    })
                    retired += len(rows) - 1
                    origins += 1
                manifest_path = target / "legacy-origin-reconciliation" / f"{collection_id}.json"
                manifest_path.parent.mkdir(exist_ok=True)
                manifest_path.write_text(json.dumps({"origins": manifest_groups}, indent=2) + "\n",
                                         encoding="utf-8")
                count += reconcile_origins(conn, collection_id, manifest_groups)
            conn.execute(queries.get("schema_foreign_key_violations"), {})
            after = [row for row in conn.execute(queries.get("schema_table_fingerprints")).fetchall()
                     if row["table_name"] != "sources"]
            if before != after or source_before != conn.execute(
                    queries.get("schema_source_content_fingerprint")).fetchone():
                raise RuntimeError("reconciliation changed retained application data")
        conn.commit()
        report = {"origins_reconciled": origins, "sources_verified": count,
                  "superseded_versions_retired": retired, "unrecognized_sources_untouched": unrecognized,
                  "historical_data_preserved": True, "foreign_keys_valid": True,
                  "allowed_source_metadata_changes": ["origin_key", "is_current", "updated_at"]}
        (target / "source-reconciliation-verification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        conn.close()
        server.cleanup()


if __name__ == "__main__":
    main()
