"""Verify v2 on the copy first, then atomically upgrade/rebuild the original.

Stored vectors only. No model loads, LLM calls or inferred prerequisites.
The original path requires successful copy evidence and a fresh recovery backup.
"""

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.db import migrate, queries
from app.db.connection import close_pools, connect
from app.ops.backup import _referenced_files, create_backup
from app.retrieval.config import load_retrieval_config
from app.simgraph.build import _normalize, _to_array, build_simgraph
from app.simgraph.view import subgraph_view
from app.simgraph.walk import recommend


def main() -> None:
    import pgserver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    copy = root / "data/schema-consolidated-20261009-verified"
    evidence = root / "docs/evidence"
    stage = "original" if args.original else "copy"
    path = evidence / f"department-graph-{stage}-2026-10-09.json"
    if path.exists():
        raise RuntimeError("dated verification already exists; do not rebuild it silently")
    server = None
    if args.original:
        proof = json.loads((evidence / "department-graph-copy-2026-10-09.json").read_text())
        if not proof["non_graph_data_unchanged"] or proof["course_similarity_links"] != 0:
            raise RuntimeError("copy verification must pass before upgrading the original")
        data_dir = root / "data"
        port = (data_dir / "pgserver/postmaster.pid").read_text().splitlines()[3]
        dsn = f"postgresql://postgres:@127.0.0.1:{port}/postgres"
    else:
        if not json.loads((copy / "consolidation-verification.json").read_text())[
                "row_fingerprints_match"]:
            raise RuntimeError("expected verified copy is missing")
        data_dir = copy
        server = pgserver.get_server(copy / "pgserver", cleanup_mode="stop")
        dsn = server.get_uri()
    conn = connect(dsn)
    try:
        backup = None
        if args.original:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = data_dir / "backups" / f"before-department-graph-{stamp}.zip"
            manifest = create_backup(conn, dsn=dsn, data_dir=data_dir, destination=backup,
                                     maintenance_confirmed=True)
            print(f"Recovery backup saved: {backup.name}; {len(manifest['files'])} files", flush=True)
            conn.commit()
        with conn.transaction():
            conn.execute(queries.get("schema_prepare_non_graph_fingerprints"), {})
            before = conn.execute(queries.get("schema_non_graph_fingerprints")).fetchall()
            files_before = _referenced_files(conn, data_dir)
            collections = conn.execute(queries.get("list_collections")).fetchall()
            prior = {
                row["collection_id"]: conn.execute(queries.get("get_sim_build_before_upgrade"), {
                    "collection_id": row["collection_id"],
                }).fetchone() for row in collections
            }
            applied = migrate.apply_migrations(conn)
            config = load_retrieval_config()
            results, probes, vectors = [], [], {}
            for collection in collections:
                old = prior[collection["collection_id"]]
                if old is None:
                    continue
                result = build_simgraph(conn, collection_id=collection["collection_id"],
                                        model=old["model"], config_version=config.version,
                                        simgraph=config.simgraph)
                clusters = conn.execute(queries.get("list_current_clusters"), {
                    "collection_id": collection["collection_id"],
                }).fetchall()
                catalog_root = next(r for r in clusters if r["kind"] == "root")
                nodes, edges = subgraph_view(conn, cluster_id=catalog_root["cluster_id"])
                by_id = {n["id"]: n for n in nodes}
                courses = [n for n in nodes if n["kind"] == "course"]
                if len(by_id) != len(nodes) or len(courses) != result.courses:
                    raise RuntimeError("course leaves are duplicated or missing")
                if len(edges) != len(nodes) - 1 or len(edges) != result.edges:
                    raise RuntimeError("containment is not a tree")
                for edge in edges:
                    parent, child = by_id[edge["parentId"]], by_id[edge["childId"]]
                    if parent["kind"] == "course" or "weight" in edge:
                        raise RuntimeError("unexpected course relationship edge")
                    if parent["kind"] != "root" and parent["departmentKey"] != child["departmentKey"]:
                        raise RuntimeError("learned topic crosses a fixed department")
                incoming = Counter(e["childId"] for e in edges)
                if any(incoming[c["id"]] != 1 for c in courses):
                    raise RuntimeError("each course must have exactly one parent")
                rows = conn.execute(queries.get("list_courses_with_vectors"), {
                    "collection_id": collection["collection_id"], "model": old["model"],
                }).fetchall()
                for row in rows:
                    if row["code"] not in {"ECON 112", "ECON 201", "MATH 351", "CS 377"}:
                        continue
                    vector = _normalize(_to_array(row["mean_vector"]))
                    vectors[row["code"]] = vector
                    recs = recommend(conn, collection_id=collection["collection_id"],
                                     query_vec=vector, simgraph=config.simgraph)
                    if not recs:
                        raise RuntimeError("stored course vector no longer routes to any group")
                    probes.append({"proxy_query": row["code"], "title": row["title"],
                                   "groups": [{"path": r.path, "courses": [c for _, c, _ in r.courses]}
                                              for r in recs]})
                results.append({**asdict(result), "collection": collection["name"],
                                "node_kinds": dict(Counter(n["kind"] for n in nodes)),
                                "topic_levels": dict(Counter(r["level"] for r in clusters)),
                                "root_id": str(catalog_root["cluster_id"])})
            after = conn.execute(queries.get("schema_non_graph_fingerprints")).fetchall()
            if before != after:
                raise RuntimeError("non-graph source, course, retrieval or prerequisite rows changed")
            conn.execute(queries.get("schema_foreign_key_violations"), {})
            files_after = _referenced_files(conn, data_dir)
            if [(f["relative_path"], f["sha256"]) for f in files_before] != [
                    (f["relative_path"], f["sha256"]) for f in files_after]:
                raise RuntimeError("referenced source/artifact bytes changed")
            conn.execute(queries.get("schema_prepare_fingerprints"), {})
            tables = conn.execute(queries.get("schema_table_fingerprints")).fetchall()
            if any(t["table_name"] == "sim_edges" for t in tables):
                raise RuntimeError("obsolete pair table survived the migration")
            migrate.verify_release_history(conn.execute(queries.get("list_applied_migrations")).fetchall())
        conn.commit()

        class ProxyEmbedder:
            def embed_query(self, text):
                return vectors[text]

        settings = replace(load_settings(), database_url=dsn, data_dir=data_dir,
                           default_collection=collections[0]["name"], auth_mode="development",
                           office_enabled=False, tools_enabled=False, routing_enabled=False,
                           db_pool_max_size=1)
        app = create_app(settings=settings, embedder=ProxyEmbedder(), provider=object(),
                         allow_test_client=True)
        with TestClient(app) as client:
            if client.get("/ready").status_code != 200:
                raise RuntimeError("upgraded API readiness failed")
            for result in results:
                response = client.get(f"/api/subgraph/{result['root_id']}")
                if response.status_code != 200 or len(response.json()["edges"]) != result["edges"]:
                    raise RuntimeError("HTTP containment view failed")
            for code in vectors:
                response = client.get("/api/recommendations", params={"query": code})
                if response.status_code != 200 or not response.json():
                    raise RuntimeError("HTTP goal exploration failed")
        report = {"stage": stage, "applied_migrations": applied, "builds": results,
                  "simgraph_config": asdict(config.simgraph), "config_version": config.version,
                  "course_similarity_links": 0, "non_graph_data_unchanged": True,
                  "non_graph_tables_verified": len(before), "non_graph_fingerprints": before,
                  "source_lifecycle_note": "Only migration-032 origin/current metadata excluded",
                  "referenced_file_hashes_verified": len(files_after), "foreign_keys_valid": True,
                  "api_ready_status": 200, "http_tree_and_recommendations_verified": True,
                  "application_rows_after": sum(t["row_count"] for t in tables),
                  "application_tables_after": len(tables), "stored_vector_probes": probes,
                  "backup": str(backup) if backup else None,
                  "backup_sha256": hashlib.file_digest(backup.open("rb"), "sha256").hexdigest()
                  if backup else None,
                  "model_loads": 0, "llm_calls": 0,
                  "limitations": ["Proxy vectors are not actual career query embeddings",
                                  "Human topic coherence/relevance review remains open",
                                  "Unmapped subjects are fixed units, not verified institutional departments"]}
        path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "non_graph_fingerprints"},
                         indent=2, default=str), flush=True)
    finally:
        close_pools()
        conn.close()
        if server is not None:
            server.cleanup()


if __name__ == "__main__":
    main()
