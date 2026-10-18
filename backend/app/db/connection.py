"""The only place psycopg connects. Nothing else calls psycopg.connect."""

import atexit
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Self

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import Settings


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        # pgvector raises ProgrammingError("vector type not found") when the
        # extension does not exist yet (fresh database, before the first
        # migration). The baseline (or legacy 001) creates it; callers that bind
        # vector values run against a migrated database.
        conn.rollback()
    return conn


def _configure_pooled(conn: psycopg.Connection) -> None:
    """Prepare a new pooled connection and leave it idle, as the pool requires."""
    try:
        register_vector(conn)
        conn.commit()
    except psycopg.ProgrammingError:
        # Same fresh-database case as `connect`; `PooledConnection` retries the
        # registration when a connection is borrowed after migration.
        conn.rollback()


class PooledConnection:
    """A borrowed pool connection with `psycopg.Connection`'s close/`with` semantics.

    `close()` (and leaving a `with` block) returns the connection to the pool
    instead of closing it; a transaction left open is rolled back by the pool.
    Everything else is the underlying connection.
    """

    __slots__ = ("_conn", "_pool")

    def __init__(self, pool: ConnectionPool, conn: psycopg.Connection):
        self._pool = pool
        self._conn = conn

    def __getattr__(self, name: str):
        conn = self._conn
        if conn is None:
            raise psycopg.OperationalError("the connection is closed")
        return getattr(conn, name)

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            # Closing a plain connection discards uncommitted work; do the same
            # explicitly so the pool does not log every read-only request.
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.rollback()
        except psycopg.Error:
            pass  # a broken connection is discarded by the pool on return
        self._pool.putconn(conn)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._conn is not None:
                if exc_type is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
        finally:
            self.close()


def register_vector_adapter(conn: "psycopg.Connection | PooledConnection") -> None:
    """Register pgvector on a plain or borrowed connection.

    pgvector type-checks for a real `psycopg.Connection`, so a pooled borrow
    (the API's first-request schema bootstrap) registers on its underlying one.
    """
    register_vector(conn._conn if isinstance(conn, PooledConnection) else conn)


_pools: dict[tuple[str, int, float, str], ConnectionPool] = {}
_pools_lock = threading.Lock()


def _pool_for(dsn: str, max_size: int, timeout: float, pool_name: str = "default") -> ConnectionPool:
    key = (dsn, max_size, timeout, pool_name)
    pool = _pools.get(key)
    if pool is None:
        with _pools_lock:
            pool = _pools.get(key)
            if pool is None:
                pool = ConnectionPool(
                    dsn, min_size=1, max_size=max_size, timeout=timeout, open=False,
                    kwargs={"row_factory": dict_row, "autocommit": False},
                    configure=_configure_pooled,
                )
                pool.open()
                _pools[key] = pool
    return pool


def pooled_connect(dsn: str, *, max_size: int, timeout: float,
                   pool_name: str = "default") -> PooledConnection:
    """Borrow a connection from the shared pool for `dsn` (raises `PoolTimeout` when exhausted)."""
    pool = _pool_for(dsn, max_size, timeout, pool_name)
    conn = pool.getconn()
    try:
        # TypesRegistry has no __contains__, so membership tests against a
        # string are always false. get() is the supported lookup.
        if conn.adapters.types.get("vector") is None:
            # The connection was created before the pgvector extension existed.
            try:
                register_vector(conn)
            except psycopg.ProgrammingError:
                pass
            conn.rollback()
    except BaseException:
        # Never leak the slot: an OperationalError from registration or
        # rollback must return the connection to the pool before propagating.
        pool.putconn(conn)
        raise
    return PooledConnection(pool, conn)


def connector(settings: Settings, dsn: str | None = None, *,
              pool_name: str = "default") -> Callable[[], "psycopg.Connection | PooledConnection"]:
    """The connection factory for request and worker paths: pooled unless disabled.

    `dsn` defaults to the configured database. Settings objects without pool
    fields (minimal test doubles) get plain connections.
    """
    dsn = dsn or settings.database_url
    max_size = getattr(settings, "db_pool_max_size", 0)
    if max_size <= 0:
        return lambda: connect(dsn)
    timeout = settings.db_pool_timeout_seconds
    return lambda: pooled_connect(dsn, max_size=max_size, timeout=timeout,
                                  pool_name=pool_name)


def resolved_database_url(settings: Settings, conn: psycopg.Connection) -> str:
    """Return the runtime DSN without dropping the configured password (EMO-001).

    psycopg exposes ``conn.info.dsn`` without the password, so round-tripping a
    password-authenticated `DATABASE_URL` through it loses the credential and
    every pooled connection later fails with ``fe_sendauth: no password
    supplied``. Keep the configured URL when one is set; the connection's DSN is
    only needed for the embedded `pgserver` case, where no URL is configured and
    the server uses trust auth.
    """
    return settings.database_url or conn.info.dsn


def pool_stats() -> list[dict[str, int | str]]:
    """This process's pool snapshots for load diagnostics and readiness metrics."""
    with _pools_lock:
        entries = [(key, pool) for key, pool in _pools.items()]
    snapshots = []
    for (_dsn, max_size, _timeout, pool_name), pool in entries:
        current = pool.get_stats()
        snapshots.append({
            "pool": pool_name,
            "maxSize": max_size,
            "size": current.get("pool_size", 0),
            "available": current.get("pool_available", 0),
            "waiting": current.get("requests_waiting", 0),
            "errors": current.get("requests_errors", 0),
        })
    return snapshots


def close_pools() -> None:
    """Close every pool (process exit and test teardown)."""
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close(timeout=5)


atexit.register(close_pools)


@contextmanager
def connection_transaction(connect_fn):
    """Open a fresh connection, run one transaction on it, and always close it."""
    conn = connect_fn()
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()


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
