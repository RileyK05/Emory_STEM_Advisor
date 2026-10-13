"""Model-free API/file checks on the verified replacement, never the original."""

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.db import queries
from app.db.connection import close_pools, connect
from app.ops.backup import _referenced_files


def main() -> None:
    import pgserver

    root = Path(__file__).resolve().parents[3]
    target = root / "data" / "schema-consolidated-20261009-verified"
    verification = json.loads((target / "consolidation-verification.json").read_text())
    if not verification.get("row_fingerprints_match"):
        raise RuntimeError("copy verification is missing")
    server = pgserver.get_server(target / "pgserver", cleanup_mode="stop")
    conn = connect(server.get_uri())
    try:
        files = _referenced_files(conn, target)
        collections = conn.execute(queries.get("list_collections")).fetchall()
        settings = replace(load_settings(), database_url=server.get_uri(), data_dir=target,
                           default_collection=collections[0]["name"], auth_mode="development",
                           office_enabled=False, tools_enabled=False, routing_enabled=False,
                           db_pool_max_size=1)
        app = create_app(settings=settings, embedder=object(), provider=object(),
                         allow_test_client=True)
        checked = listed = 0
        with TestClient(app) as client:
            ready = client.get("/ready")
            if ready.status_code != 200 or ready.json() != {"status": "ready"}:
                raise RuntimeError("replacement API readiness failed")
            if client.get("/api/collections").status_code != 200:
                raise RuntimeError("replacement collection reads failed")
            for collection in collections:
                response = client.get("/api/sources", params={"collection": collection["name"]})
                if response.status_code != 200:
                    raise RuntimeError("replacement source listing failed")
                expected = conn.execute(queries.get("list_sources_for_workspace"), {
                    "collection_id": collection["collection_id"], "workspace_key": "development",
                }).fetchall()
                if {row["id"] for row in response.json()} != {
                        str(row["source_id"]) for row in expected}:
                    raise RuntimeError("replacement API source visibility differs from the contract")
                checked += 1
                listed += len(expected)
        report = {"ready_http_status": 200, "collections_checked": checked,
                  "visible_sources_checked": listed, "copied_file_hashes_verified": len(files),
                  "pool_max_size": 1, "provider_calls": 0, "model_loads": 0,
                  "original_database_opened": False}
        (target / "api-verification.json").write_text(json.dumps(report, indent=2) + "\n",
                                                       encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        close_pools()
        conn.close()
        server.cleanup()


if __name__ == "__main__":
    main()
