"""Explicit legacy provenance and raw-hash reconciliation against Postgres."""

import hashlib
from pathlib import Path

import pytest

from app.db import queries
from app.ingest.pipeline import ingest_file
from scripts.reconcile_source_origins import (
    OriginReconciliationError,
    provenance_origin_key,
    reconcile_origins,
)
from scripts.scrape_programs import department_origin_key
from tests.fakes import FakeEmbedder, whitespace_tokens
from tests.helpers import lenient_config


def _ingest_legacy(db, collection, tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/markdown",
        count_tokens=whitespace_tokens,
        embedder=FakeEmbedder(dim=8),
        embed_model="test-embed",
        config=lenient_config(),
    )


def test_reconcile_verified_legacy_program_versions_keeps_history(db, collection, tmp_path):
    url = "https://chemistry.emory.edu/undergraduate/requirements.html"
    old = (
        "# Program requirements: Chemistry BS (CHEMBS)\n\n"
        f"Source: {url}\nRetrieved: 2026-10-07\n"
        "Programs: Chemistry BS (CHEMBS)\n\nCopied content.\n"
    ).encode()
    new = old.replace(b"2026-10-07", b"2026-10-08")
    old_id = _ingest_legacy(db, collection, tmp_path, "old.md", old)
    new_id = _ingest_legacy(db, collection, tmp_path, "new.md", new)
    old_hash, new_hash = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    origin = department_origin_key(url)

    changed = reconcile_origins(db, collection, [{
        "origin_key": origin,
        "source_sha256s": [old_hash, new_hash],
        "current_sha256": new_hash,
    }])

    assert changed == 2
    rows = db.execute(
        "SELECT source_id, origin_key, is_current FROM sources "
        "WHERE source_id = ANY(%(ids)s::uuid[]) ORDER BY source_id",
        {"ids": [old_id, new_id]},
    ).fetchall()
    by_id = {row["source_id"]: row for row in rows}
    assert by_id[old_id]["origin_key"] == origin
    assert by_id[old_id]["is_current"] is False
    assert by_id[new_id]["origin_key"] == origin
    assert by_id[new_id]["is_current"] is True
    assert db.execute(
        queries.get("list_chunks_for_source"), {"source_id": old_id}
    ).fetchall()
    assert [row["source_id"] for row in db.execute(
        queries.get("list_source_fences_for_workspace"), {
            "collection_id": collection, "workspace_key": "development",
        }
    ).fetchall()] == [new_id]


def test_reconcile_rejects_hash_or_header_mismatch_without_mutation(db, collection, tmp_path):
    url = "https://chemistry.emory.edu/undergraduate/requirements.html"
    raw = ("# Program requirements: Chemistry BS (CHEMBS)\n\n"
           f"Source: {url}\nRetrieved: 2026-10-08\n\nRequirements.\n").encode()
    source_id = _ingest_legacy(db, collection, tmp_path, "legacy.md", raw)
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(OriginReconciliationError, match="header does not match"):
        reconcile_origins(db, collection, [{
            "origin_key": "programs:department:wrong-origin",
            "source_sha256s": [digest],
            "current_sha256": digest,
        }])
    state = db.execute("SELECT origin_key, is_current FROM sources WHERE source_id=%s",
                        (source_id,)).fetchone()
    assert state["origin_key"] is None
    assert state["is_current"] is True


def test_reconcile_checks_raw_bytes_against_stored_sha256(db, collection, tmp_path):
    url = "https://chemistry.emory.edu/undergraduate/requirements.html"
    raw = ("# Program requirements: Chemistry BS (CHEMBS)\n\n"
           f"Source: {url}\nRetrieved: 2026-10-08\n\nRequirements.\n").encode()
    source_id = _ingest_legacy(db, collection, tmp_path, "legacy.md", raw)
    row = db.execute("SELECT file_hash, raw_path FROM sources WHERE source_id=%s",
                     (source_id,)).fetchone()
    Path(row["raw_path"]).write_bytes(raw + b"tampered")
    with pytest.raises(OriginReconciliationError, match="failed SHA-256"):
        reconcile_origins(db, collection, [{
            "origin_key": department_origin_key(url),
            "source_sha256s": [row["file_hash"]],
            "current_sha256": row["file_hash"],
        }])
    state = db.execute("SELECT origin_key, is_current FROM sources WHERE source_id=%s",
                        (source_id,)).fetchone()
    assert state["origin_key"] is None
    assert state["is_current"] is True


@pytest.mark.parametrize("provenance", [
    b"URL: https://signed.example.invalid/download?token=rotating\nRetrieved: 2026-10-08\n",
    b"URL: https://college.catalog.emory.edu/\nCatalog ID: FUoE83Jq1SyDx5rJvewc\n",  # current scraper
])
def test_catalog_header_maps_to_stable_title_origin(provenance):
    raw = (
        b"# Biology BS\n\n"
        b"Source: Emory College of Arts and Sciences Catalog 2026-2027, "
        b"official catalog PDF, pages 81-83\n" + provenance + b"\nRequirements.\n"
    )
    assert provenance_origin_key(raw) == "programs:catalog:biology-bs"
