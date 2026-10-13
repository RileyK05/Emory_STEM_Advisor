"""Independent checks for copy fingerprints and trigger-disabled restore validation."""

from uuid import uuid4

import psycopg
import pytest

from app.db import queries


def test_fingerprints_include_existing_current_flags_outside_sources(db, collection):
    build_id = uuid4()
    db.execute(queries.get("insert_sim_build"), {
        "build_id": build_id, "collection_id": collection, "model": "copy-check",
        "config_version": 1, "nudges_hash": "a" * 64, "content_hash": None,
    })
    db.execute(queries.get("schema_prepare_fingerprints"), {})
    before = {row["table_name"]: row for row in db.execute(
        queries.get("schema_table_fingerprints")).fetchall()}
    db.execute(queries.get("set_current_build"), {"build_id": build_id})
    after = {row["table_name"]: row for row in db.execute(
        queries.get("schema_table_fingerprints")).fetchall()}
    assert before["sim_builds"]["row_count"] == after["sim_builds"]["row_count"] == 1
    assert before["sim_builds"]["fingerprint"] != after["sim_builds"]["fingerprint"]


def test_foreign_key_validation_catches_an_orphan_loaded_with_triggers_disabled(db):
    with (
        pytest.raises(psycopg.errors.RaiseException, match="foreign key data failed"),
        db.transaction(),
    ):
        db.execute("ALTER TABLE sources DISABLE TRIGGER ALL")
        db.execute(queries.get("upsert_source"), {
            "source_id": uuid4(), "collection_id": uuid4(), "workspace_key": None,
            "filename": "orphan.txt", "content_type": "text/plain", "file_hash": "a" * 64,
            "raw_path": "not-read-in-this-test", "origin_key": None,
        })
        db.execute("ALTER TABLE sources ENABLE TRIGGER ALL")
        db.execute(queries.get("schema_foreign_key_violations"), {})
    assert db.execute("SELECT count(*) AS n FROM sources").fetchone()["n"] == 0
