"""FastAPI dependency wiring. Concrete providers are built here, once."""

from collections.abc import Iterator
from uuid import UUID

from fastapi import HTTPException, Request

from app.config import Settings
from app.db import queries
from app.db.connection import connect
from app.embeddings.base import Embedder
from app.llm.base import Provider
from app.retrieval.config import RetrievalConfig


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_config(request: Request) -> RetrievalConfig:
    return request.app.state.config


def get_embedder_dep(request: Request) -> Embedder:
    return request.app.state.embedder


def get_provider_dep(request: Request) -> Provider:
    return request.app.state.provider


def get_conn(request: Request) -> Iterator:
    """A request-scoped connection.

    Tests set `app.state.override_conn` to the fixture connection. Production
    requires `DATABASE_URL`; a server without one has no database to serve.
    """
    override = getattr(request.app.state, "override_conn", None)
    if override is not None:
        yield override
        return
    dsn = request.app.state.settings.database_url
    if not dsn:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    conn = connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


def resolve_collection(conn, settings: Settings, name: str | None) -> tuple[UUID, str]:
    """Map a collection name (or the configured default) to its id.

    A named collection that does not exist is a 404, never an empty result.
    """
    resolved = name or settings.default_collection
    row = conn.execute(
        queries.get("get_collection_by_name"), {"name": resolved}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown collection {resolved!r}")
    return row["collection_id"], resolved
