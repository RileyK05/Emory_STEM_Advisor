"""Readiness is a bounded read-only connectivity/schema probe."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import migrate, queries
from app.ops.readiness import ReadinessProbe


def _migration_rows():
    return [
        {"version": version, "name": name, "sha256": migrate._sha256(path)}
        for version, name, path in migrate.fresh_manifest()
    ]


def test_readiness_accepts_complete_legacy_history_and_rejects_pending_upgrade():
    rows = [{"version": version, "name": name, "sha256": migrate._sha256(path)}
            for version, name, path in migrate._discover()]
    auth = SimpleNamespace(mode="development")
    assert ReadinessProbe(connect_fn=lambda _dsn: FakeConnection(rows)).check(
        dsn="postgresql://safe/db", auth_service=auth, auth_mode="development").ready
    assert ReadinessProbe(connect_fn=lambda _dsn: FakeConnection(rows[:-1])).check(
        dsn="postgresql://safe/db", auth_service=auth,
        auth_mode="development").reasons == ("schema_mismatch",)


def test_readiness_accepts_legacy_crlf_checksums_like_apply_migrations():
    # A legacy CRLF checkout's digest: the LF-normalized content re-expanded to
    # CRLF, which differs from the canonical (LF) digest readiness now accepts.
    rows = [
        {"version": version, "name": name,
         "sha256": hashlib.sha256(
             path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")).hexdigest()}
        for version, name, path in migrate._discover()
    ]
    auth = SimpleNamespace(mode="development")
    probe = ReadinessProbe(connect_fn=lambda _dsn: FakeConnection(rows))
    assert probe.check(dsn="postgresql://safe/db", auth_service=auth,
                       auth_mode="development").ready


class FakeConnection:
    def __init__(self, rows=None, error=None):
        self.rows = _migration_rows() if rows is None else rows
        self.error = error
        self.closed = False
        self.rolled_back = False
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        if self.error:
            raise self.error
        class Result:
            def __init__(self, rows):
                self.rows = rows
            def fetchall(self):
                return self.rows
        return Result(self.rows)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.rolled_back = True
        self.closed = True


def test_readiness_is_cached_and_passes_read_only_timeouts_to_connect():
    calls = []
    conn = FakeConnection()
    probe = ReadinessProbe(connect_fn=lambda dsn: calls.append(dsn) or conn)
    auth = SimpleNamespace(mode="development")
    first = probe.check(dsn="postgresql://safe-user:safe-pass@localhost/db",
                        auth_service=auth, auth_mode="development")
    second = probe.check(dsn="postgresql://safe-user:safe-pass@localhost/db",
                         auth_service=auth, auth_mode="development")
    assert first.as_dict() == {"status": "ready"}
    assert second == first
    assert len(calls) == 1
    assert conn.closed and conn.rolled_back
    from psycopg.conninfo import conninfo_to_dict
    dsn = conninfo_to_dict(calls[0])
    assert dsn["connect_timeout"] == "3"
    assert "default_transaction_read_only=on" in dsn["options"]
    assert "statement_timeout=3000" in dsn["options"]
    assert conn.sql == [queries.get("list_applied_migrations")]


def test_readiness_reports_wrong_or_missing_migration_without_raw_details():
    rows = _migration_rows()
    rows[0] = {**rows[0], "sha256": "0" * 64}
    auth = SimpleNamespace(mode="oidc")
    result = ReadinessProbe(connect_fn=lambda _dsn: FakeConnection(rows)).check(
        dsn="postgresql://private:credential@localhost/private", auth_service=auth,
        auth_mode="oidc",
    )
    assert result.as_dict() == {"status": "unavailable", "reasons": ["schema_mismatch"]}
    assert "private" not in str(result.as_dict())
    missing = rows[1:]
    result = ReadinessProbe(connect_fn=lambda _dsn: FakeConnection(missing)).check(
        dsn="postgresql://private:credential@localhost/private", auth_service=auth,
        auth_mode="oidc",
    )
    assert result.reasons == ("schema_mismatch",)


def test_readiness_connect_failure_and_missing_dsn_are_safe():
    auth = SimpleNamespace(mode="development")
    def fail(_dsn):
        raise OSError("contains a private DSN and password")
    failed = ReadinessProbe(connect_fn=fail).check(
        dsn="postgresql://private:credential@localhost/db", auth_service=auth,
        auth_mode="development",
    )
    assert failed.as_dict() == {"status": "unavailable", "reasons": ["database_unavailable"]}
    calls = []
    missing = ReadinessProbe(connect_fn=lambda dsn: calls.append(dsn)).check(
        dsn=None, auth_service=auth, auth_mode="development",
    )
    assert missing.reasons == ("database_not_configured",)
    assert calls == []


def test_readiness_deadline_and_auth_mismatch_are_bounded_and_generic():
    clock = [0.0]
    conn = FakeConnection()
    def connect_slow(_dsn):
        clock[0] = 3.0
        return conn
    probe = ReadinessProbe(connect_fn=connect_slow, monotonic=lambda: clock[0])
    result = probe.check(dsn="postgresql://private/db", auth_service=None,
                         auth_mode="oidc")
    assert result.as_dict() == {
        "status": "unavailable",
        "reasons": ["auth_not_configured", "database_probe_timeout"],
    }
    assert conn.closed


def test_query_timeout_fails_closed():
    import psycopg
    conn = FakeConnection(error=psycopg.errors.QueryCanceled("private query detail"))
    result = ReadinessProbe(connect_fn=lambda _dsn: conn).check(
        dsn="postgresql://private/db", auth_service=SimpleNamespace(mode="development"),
        auth_mode="development",
    )
    assert result.reasons == ("database_probe_timeout",)
    assert conn.closed


def test_driver_cleanup_failure_is_a_safe_unavailable_result(monkeypatch):
    conn = FakeConnection()
    def fail_close():
        raise OSError("private database address and credentials")
    monkeypatch.setattr(conn, "close", fail_close)
    result = ReadinessProbe(connect_fn=lambda _dsn: conn).check(
        dsn="postgresql://private:secret@localhost/db",
        auth_service=SimpleNamespace(mode="development"), auth_mode="development",
    )
    assert result.as_dict() == {"status": "unavailable", "reasons": ["database_unavailable"]}


def test_readiness_checks_real_migration_metadata_and_restores_test_mutations(db, migrated_dsn):
    auth = SimpleNamespace(mode="development")
    probe = ReadinessProbe()
    ready = probe.check(dsn=migrated_dsn, auth_service=auth, auth_mode="development")
    assert ready.ready
    original = db.execute(
        "SELECT version, name, sha256, applied_at FROM schema_migrations "
        "ORDER BY version LIMIT 1"
    ).fetchone()
    try:
        db.execute("UPDATE schema_migrations SET sha256=%s WHERE version=%s",
                   ("0" * 64, original["version"]))
        db.commit()
        wrong = ReadinessProbe().check(
            dsn=migrated_dsn, auth_service=auth, auth_mode="development")
        assert wrong.reasons == ("schema_mismatch",)

        db.execute("DELETE FROM schema_migrations WHERE version=%s", (original["version"],))
        db.commit()
        missing = ReadinessProbe().check(
            dsn=migrated_dsn, auth_service=auth, auth_mode="development")
        assert missing.reasons == ("schema_mismatch",)
    finally:
        db.execute(
            "INSERT INTO schema_migrations (version, name, sha256, applied_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (version) DO UPDATE "
            "SET name=EXCLUDED.name, sha256=EXCLUDED.sha256, applied_at=EXCLUDED.applied_at",
            (original["version"], original["name"], original["sha256"], original["applied_at"]),
        )
        db.commit()
    assert ReadinessProbe().check(
        dsn=migrated_dsn, auth_service=auth, auth_mode="development").ready


def test_ready_route_is_public_and_returns_minimal_stable_json():

    from app.api import create_app
    from app.config import load_settings
    from app.retrieval.config import load_retrieval_config

    conn = FakeConnection()
    settings = replace(load_settings(), database_url="postgresql://safe/db",
                       auth_mode="development", readiness_timeout_seconds=2,
                       office_enabled=False, tools_enabled=False, routing_enabled=False)
    app = create_app(
        settings=settings, config=load_retrieval_config(settings=settings),
        embedder=object(), provider=object(), auth_service=SimpleNamespace(mode="development"),
        readiness_connect=lambda _dsn: conn,
    )
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"


def test_ready_route_returns_503_with_only_bounded_reason_codes():
    from app.api import create_app
    from app.config import load_settings
    from app.retrieval.config import load_retrieval_config

    settings = replace(load_settings(), database_url=None, auth_mode="development",
                       office_enabled=False, tools_enabled=False, routing_enabled=False)
    calls = []
    app = create_app(
        settings=settings, config=load_retrieval_config(settings=settings),
        embedder=object(), provider=object(), auth_service=SimpleNamespace(mode="development"),
        readiness_connect=lambda dsn: calls.append(dsn),
    )
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable", "reasons": ["database_not_configured"],
    }
    assert calls == []
    assert "database_url" not in response.text

def test_readiness_timeout_setting_is_bounded(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "11")
    with pytest.raises(ValueError, match="READINESS_TIMEOUT_SECONDS"):
        load_settings()
