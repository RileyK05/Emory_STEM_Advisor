"""Migration runner tests. These run against a real Postgres."""

import numpy as np
import psycopg
import pytest
from psycopg import conninfo

from app.db import migrate
from app.db.connection import connect
from app.db.migrate import MigrationChanged, MigrationError, apply_migrations

pytestmark = pytest.mark.db


def test_discover_rejects_gap(tmp_path, monkeypatch):
    (tmp_path / "001_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "003_c.sql").write_text("SELECT 3;\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    with pytest.raises(MigrationError, match="gap"):
        migrate._discover()


def test_discover_rejects_duplicate_number(tmp_path, monkeypatch):
    (tmp_path / "001_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "001_b.sql").write_text("SELECT 2;\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    with pytest.raises(MigrationError, match="duplicate migration number"):
        migrate._discover()


def test_discover_rejects_malformed_filename(tmp_path, monkeypatch):
    (tmp_path / "not_a_migration.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    with pytest.raises(MigrationError, match="malformed migration filename"):
        migrate._discover()


def test_apply_then_bind_vector_on_same_connection(migrated_dsn):
    admin = psycopg.connect(migrated_dsn, autocommit=True)
    admin.execute("CREATE DATABASE vectorbind")
    admin.close()
    dsn = conninfo.make_conninfo(migrated_dsn, dbname="vectorbind")

    conn = connect(dsn)
    try:
        applied = apply_migrations(conn)
        assert "001_base.sql" in applied
        conn.execute(
            "CREATE TABLE vec_probe (id int PRIMARY KEY, v vector(3))"
        )
        conn.execute(
            "INSERT INTO vec_probe (id, v) VALUES (%(id)s, %(v)s)",
            {"id": 1, "v": np.array([0.6, 0.8, 0.0], dtype=np.float32)},
        )
        conn.commit()
        row = conn.execute("SELECT v FROM vec_probe WHERE id = 1").fetchone()
        assert row["v"].to_list() == pytest.approx([0.6, 0.8, 0.0], abs=1e-6)
    finally:
        conn.close()


def test_migrations_apply_on_empty_db(db):
    db.execute("DROP SCHEMA public CASCADE")
    db.execute("CREATE SCHEMA public")
    db.commit()
    applied = apply_migrations(db)
    db.commit()
    assert applied[0] == "001_base.sql"
    assert "002_documents.sql" in applied
    assert apply_migrations(db) == []


def test_migrations_are_idempotent(db):
    assert apply_migrations(db) == []


def test_vector_extension_is_present(db):
    row = db.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    ).fetchone()
    assert row is not None
    assert row["extversion"]


def test_changed_applied_migration_raises(db, tmp_path, monkeypatch):
    saved = db.execute("SELECT version, name, sha256 FROM schema_migrations").fetchall()
    try:
        db.execute("TRUNCATE schema_migrations")
        db.commit()
        monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)

        path = tmp_path / "001_custom.sql"
        path.write_text("CREATE TABLE _migtest (x int);\n", encoding="utf-8")
        assert apply_migrations(db) == ["001_custom.sql"]

        path.write_text("CREATE TABLE _migtest (x int, y int);\n", encoding="utf-8")
        with pytest.raises(MigrationChanged):
            apply_migrations(db)
    finally:
        db.execute("DROP TABLE IF EXISTS _migtest")
        db.execute("TRUNCATE schema_migrations")
        for row in saved:
            db.execute(
                "INSERT INTO schema_migrations (version, name, sha256)"
                " VALUES (%(version)s, %(name)s, %(sha)s)",
                {"version": row["version"], "name": row["name"], "sha": row["sha256"]},
            )
        db.commit()
