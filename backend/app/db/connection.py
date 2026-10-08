"""The only place psycopg connects. Nothing else calls psycopg.connect."""

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import Settings


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        # pgvector raises ProgrammingError("vector type not found") when the
        # extension does not exist yet (fresh database, before the first
        # migration). The type is created by migration 001; callers that bind
        # vector values run against a migrated database.
        conn.rollback()
    return conn


@contextmanager
def open_connection(settings: Settings, database_url: str | None = None):
    """Yield a migrated connection.

    Uses an explicit URL, then `DATABASE_URL`, then an embedded `pgserver`
    instance (D2). The embedded server keeps its data under `DATA_DIR/pgserver`
    (gitignored) so callers can run in sequence without a DATABASE_URL; its
    schema is migrated on first use. Set `PGSERVER_DATA` to relocate it.
    """
    dsn = database_url or settings.database_url
    if dsn:
        conn = connect(dsn)
        try:
            yield conn
        finally:
            conn.close()
        return

    import pgserver  # dev-only dependency; production always sets a URL

    pgdata = Path(os.environ.get("PGSERVER_DATA") or settings.data_dir / "pgserver")
    pgdata.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    try:
        conn = connect(server.get_uri())
        try:
            from app.db.migrate import apply_migrations

            apply_migrations(conn)
            conn.commit()
            yield conn
        finally:
            conn.close()
    finally:
        server.cleanup()
