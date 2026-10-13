"""Database fixtures. Tests actually run against Postgres + pgvector."""

import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

# Must be set before `app.config` is imported (via app.db below): tests are
# hermetic and never read a developer's repo-root `.env`.
os.environ["ADVISOR_SKIP_DOTENV"] = "1"

from app.db import queries
from app.db.connection import close_pools, connect
from app.db.migrate import apply_migrations


def _remove_session_directory(directory, *, root: Path, prefix: str):
    target = Path(directory).resolve()
    if target.parent != root or not target.name.startswith(prefix):
        raise RuntimeError("temporary test cleanup target escaped its configured root")
    shutil.rmtree(target, ignore_errors=True)


_DB_FIXTURES = {"db", "collection", "migrated_dsn", "pg_dsn"}


def pytest_collection_modifyitems(items):
    """Mark every test that reaches Postgres (directly or via another fixture) as `db`."""
    for item in items:
        if _DB_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.db)


@pytest.fixture(autouse=True)
def _offline_http_transports(monkeypatch):
    """HTTP must use injected mock transports; real Postgres is a separate seam."""
    import httpx
    import httpx2
    import requests

    def reject_http(*_args, **_kwargs):
        raise AssertionError("real HTTP is forbidden in pytest; inject a mock transport")

    async def reject_async_http(*_args, **_kwargs):
        reject_http()

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", reject_http)
    for client in (httpx, httpx2):
        monkeypatch.setattr(client.HTTPTransport, "handle_request", reject_http)
        monkeypatch.setattr(client.AsyncHTTPTransport, "handle_async_request", reject_async_http)


@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dir():
    """Keep ingest's raw uploads out of the repo's real `data/` folder."""
    previous = os.environ.get("DATA_DIR")
    cleanup_root = Path(tempfile.gettempdir()).resolve()
    data_dir = tempfile.mkdtemp(prefix="advisor_data_", dir=cleanup_root)
    os.environ["DATA_DIR"] = data_dir
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = previous
        _remove_session_directory(data_dir, root=cleanup_root, prefix="advisor_data_")


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        try:
            yield explicit
        finally:
            close_pools()
        return

    import pgserver

    # Deliberately not pytest's tmp_path: on Windows the postmaster's data
    # files can leave the directory undeletable, which then breaks pytest's
    # own basetemp cleanup.
    cleanup_root = Path(tempfile.gettempdir()).resolve()
    pgdata = tempfile.mkdtemp(prefix="pgserver_pytest_", dir=cleanup_root)
    try:
        server = pgserver.get_server(pgdata, cleanup_mode="stop")
    except Exception as exc:
        _remove_session_directory(pgdata, root=cleanup_root, prefix="pgserver_pytest_")
        raise RuntimeError(
            "Could not start the embedded Postgres test server. Set TEST_DATABASE_URL "
            "to a real Postgres+pgvector instance (for example, run docker compose up -d postgres)."
        ) from exc
    try:
        yield server.get_uri()
    finally:
        # Pools must close before the server stops, or they retry against a dead socket.
        close_pools()
        server.cleanup()
        _remove_session_directory(pgdata, root=cleanup_root, prefix="pgserver_pytest_")


@pytest.fixture(scope="session")
def migrated_dsn(pg_dsn: str) -> str:
    conn = connect(pg_dsn)
    try:
        apply_migrations(conn)
        conn.commit()
    finally:
        conn.close()
    return pg_dsn


# Cleaning up after a test touches only the tables it wrote to. DELETE with foreign
# keys and triggers off (session_replication_role = replica) takes about 5 ms; a
# TRUNCATE ... CASCADE rewrites every table that references the target and takes
# about 200 ms. Replica mode needs a superuser, which the embedded server is; if the
# role can't set it (an external TEST_DATABASE_URL), fall back to TRUNCATE.
_CLEAN_BY_DELETE = """
DO $$
DECLARE r record; has_rows boolean;
BEGIN
    SET LOCAL session_replication_role = replica;
    FOR r IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'schema_migrations'
    LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', r.tablename) INTO has_rows;
        IF has_rows THEN
            EXECUTE format('DELETE FROM %I', r.tablename);
        END IF;
    END LOOP;
END $$;
"""
_CLEAN_BY_TRUNCATE = """
DO $$
DECLARE r record; has_rows boolean; touched text[] := '{}';
BEGIN
    FOR r IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'schema_migrations'
    LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', r.tablename) INTO has_rows;
        IF has_rows THEN
            touched := touched || format('%I', r.tablename);
        END IF;
    END LOOP;
    IF cardinality(touched) > 0 THEN
        EXECUTE 'TRUNCATE TABLE ' || array_to_string(touched, ', ') || ' CASCADE';
    END IF;
END $$;
"""


def _clean_database(conn) -> None:
    conn.execute("SET lock_timeout = '15s'")  # a leaked open transaction fails, not hangs
    try:
        conn.execute(_CLEAN_BY_DELETE)
    except psycopg.errors.InsufficientPrivilege:
        conn.rollback()
        conn.execute("SET lock_timeout = '15s'")
        conn.execute(_CLEAN_BY_TRUNCATE)
    conn.commit()


@pytest.fixture
def db(migrated_dsn: str):
    conn = connect(migrated_dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        _clean_database(conn)
        conn.close()


@pytest.fixture
def collection(db):
    collection_id = uuid4()
    db.execute(
        queries.get("insert_collection"),
        {"collection_id": collection_id, "name": f"c-{collection_id}"},
    )
    db.commit()
    return collection_id
