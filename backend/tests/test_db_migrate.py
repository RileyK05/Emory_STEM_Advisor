"""Migration runner tests. These run against a real Postgres."""

import hashlib
from contextlib import contextmanager
from uuid import uuid4

import numpy as np
import psycopg
import pytest
from psycopg import conninfo, sql

from app.db import migrate, queries
from app.db.connection import close_pools, connect, pooled_connect
from app.db.migrate import MigrationChanged, MigrationError, apply_migrations


@contextmanager
def isolated_database(dsn):
    name = "migration_test_" + uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    conn = connect(conninfo.make_conninfo(dsn, dbname=name))
    try:
        yield conn
    finally:
        conn.close()
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


def test_old_raw_crlf_hash_is_normalized_without_reapplying(migrated_dsn, db,
                                                          tmp_path, monkeypatch):
    path = tmp_path / "001_custom.sql"
    original = b"CREATE TABLE preserved (value text);\r\n"
    path.write_bytes(original.replace(b"\r\n", b"\n"))
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    with isolated_database(migrated_dsn) as conn:
        migrate._ensure_table(conn)
        conn.execute(original.decode())
        conn.execute("INSERT INTO preserved VALUES ('still here')")
        conn.execute(queries.get("record_applied_migration"), {
            "version": 1, "name": path.name, "sha": hashlib.sha256(original).hexdigest(),
        })
        conn.commit()
        before = conn.execute("SELECT applied_at FROM schema_migrations").fetchone()
        assert apply_migrations(conn) == []
        after = conn.execute("SELECT sha256, applied_at FROM schema_migrations").fetchone()
        assert after["sha256"] == migrate._sha256(path)
        assert after["applied_at"] == before["applied_at"]
        assert conn.execute("SELECT value FROM preserved").fetchone()["value"] == "still here"
        path.write_text("CREATE TABLE preserved (changed text);\n", encoding="utf-8")
        with pytest.raises(MigrationChanged):
            apply_migrations(conn)


@pytest.mark.parametrize("history", ["missing", "unknown", "mixed"])
def test_incompatible_history_is_rejected_before_schema_changes(db, history):
    manifest = migrate.fresh_manifest()
    rows = [{"version": v, "name": name, "sha256": migrate._sha256(path)}
            for v, name, path in manifest]
    if history == "missing":
        rows = []
    elif history == "unknown":
        rows.append({"version": 999, "name": "999_unknown.sql", "sha256": "0" * 64})
    else:
        version, name, path = migrate._discover()[0]
        rows.insert(0, {"version": version, "name": name, "sha256": migrate._sha256(path)})
    with pytest.raises(MigrationError):
        migrate.verify_release_history(rows)


def test_missing_baseline_or_archive_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "_BASELINE_PATH", tmp_path / "missing.sql")
    with pytest.raises(MigrationError, match="baseline is missing"):
        migrate.fresh_manifest()
    monkeypatch.setattr(migrate, "_LEGACY_MIGRATIONS_DIR", tmp_path / "missing_archive")
    with pytest.raises(MigrationError, match="archive is missing"):
        migrate.fresh_manifest()


@pytest.mark.parametrize(("files", "message"), [
    ({"001_a.sql": "SELECT 1;\n", "003_c.sql": "SELECT 3;\n"}, "gap"),
    ({"001_a.sql": "SELECT 1;\n", "001_b.sql": "SELECT 2;\n"}, "duplicate migration number"),
    ({"not_a_migration.sql": "SELECT 1;\n"}, "malformed migration filename"),
])
def test_discover_rejects_gaps_duplicates_and_malformed_names(tmp_path, monkeypatch, files, message):
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    with pytest.raises(MigrationError, match=message):
        migrate._discover()


@pytest.mark.parametrize("pooled", [False, True])  # the API bootstraps through a pool borrow
def test_fresh_database_applies_baseline_once_and_binds_vector_on_the_same_connection(
    migrated_dsn, pooled,
):
    with isolated_database(migrated_dsn) as plain:
        conn = (pooled_connect(plain.info.dsn, max_size=1, timeout=5.0, pool_name="migrate-test")
                if pooled else plain)
        try:
            applied = apply_migrations(conn)
            conn.commit()
            assert applied[0] == migrate._BASELINE_PATH.name
            assert apply_migrations(conn) == []  # idempotent
            conn.execute("CREATE TABLE vec_probe (id int PRIMARY KEY, v vector(3))")
            conn.execute(
                "INSERT INTO vec_probe (id, v) VALUES (%(id)s, %(v)s)",
                {"id": 1, "v": np.array([0.6, 0.8, 0.0], dtype=np.float32)},
            )
            conn.commit()
            row = conn.execute("SELECT v FROM vec_probe WHERE id = 1").fetchone()
            assert row["v"].to_list() == pytest.approx([0.6, 0.8, 0.0], abs=1e-6)
        finally:
            if pooled:
                conn.close()
                close_pools()  # release pooled sessions before the database is dropped


def _custom_migration_history(conn, tmp_path, monkeypatch, body: bytes):
    """A disposable database whose recorded history is one custom migration file."""
    apply_migrations(conn)
    conn.execute("TRUNCATE schema_migrations")
    conn.commit()
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    path = tmp_path / "001_custom.sql"
    path.write_bytes(body)
    assert apply_migrations(conn) == ["001_custom.sql"]
    return path


def test_changed_applied_migration_raises(migrated_dsn, tmp_path, monkeypatch):
    with isolated_database(migrated_dsn) as conn:
        path = _custom_migration_history(
            conn, tmp_path, monkeypatch, b"CREATE TABLE _migtest (x int);\n")
        path.write_bytes(b"CREATE TABLE _migtest (x int, y int);\n")
        with pytest.raises(MigrationChanged):
            apply_migrations(conn)


def test_line_ending_change_is_not_a_migration_change(migrated_dsn, tmp_path, monkeypatch):
    with isolated_database(migrated_dsn) as conn:
        path = _custom_migration_history(
            conn, tmp_path, monkeypatch, b"CREATE TABLE _migtest (x int);\n")
        path.write_bytes(b"CREATE TABLE _migtest (x int);\r\n")
        assert apply_migrations(conn) == []
