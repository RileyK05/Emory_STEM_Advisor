"""Database fixtures. Tests actually run against Postgres + pgvector."""

import os
import shutil
import tempfile
from uuid import uuid4

import pytest

from app.db import queries
from app.db.connection import connect
from app.db.migrate import apply_migrations


@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dir():
    """Keep ingest's raw uploads out of the repo's real `data/` folder."""
    previous = os.environ.get("DATA_DIR")
    data_dir = tempfile.mkdtemp(prefix="advisor_data_")
    os.environ["DATA_DIR"] = data_dir
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = previous
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return

    import pgserver

    # Deliberately not pytest's tmp_path: on Windows the postmaster's data
    # files can leave the directory undeletable, which then breaks pytest's
    # own basetemp cleanup.
    pgdata = tempfile.mkdtemp(prefix="pgserver_pytest_")
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()
        shutil.rmtree(pgdata, ignore_errors=True)


@pytest.fixture(scope="session")
def migrated_dsn(pg_dsn: str) -> str:
    conn = connect(pg_dsn)
    try:
        apply_migrations(conn)
        conn.commit()
    finally:
        conn.close()
    return pg_dsn


@pytest.fixture
def db(migrated_dsn: str):
    conn = connect(migrated_dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                """
                DO $$
                DECLARE r record;
                BEGIN
                    FOR r IN
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public'
                          AND tablename <> 'schema_migrations'
                    LOOP
                        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename)
                                || ' CASCADE';
                    END LOOP;
                END $$;
                """
            )
        conn.commit()
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
