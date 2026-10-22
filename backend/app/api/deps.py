"""FastAPI dependency wiring. Concrete providers are built here, once."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from psycopg_pool import PoolTimeout

from app.auth.dependencies import get_admin_principal, get_auditor_or_admin_principal
from app.auth.models import Principal
from app.config import Settings
from app.db.bootstrap import prepare_database
from app.db.collections import UnknownCollection
from app.db.collections import resolve_collection as lookup_collection
from app.db.connection import connector
from app.embeddings.base import Embedder
from app.jobs.store import JobStore
from app.llm.base import Provider
from app.retrieval.config import RetrievalConfig


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def execution_settings(request: Request, conn, workspace: str, settings=None) -> Settings:
    from app.flags.service import FeatureFlagService

    return FeatureFlagService.execution_settings(
        conn, settings or request.app.state.settings, workspace,
        ceilings=request.app.state.settings)


def get_config(request: Request) -> RetrievalConfig:
    return request.app.state.config


def get_embedder_dep(request: Request) -> Embedder:
    return request.app.state.embedder


def get_provider_dep(request: Request) -> Provider | None:
    # Retrieval-only refusals and inspection remain available without a model.
    return request.app.state.provider


def open_ready_connection(app_state):
    """A pooled connection with the schema bootstrapped; 503 when none can be had."""
    if not app_state.settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    try:
        conn = connector(app_state.settings)()
    except PoolTimeout:
        raise HTTPException(status_code=503, detail="database is busy; retry shortly") from None
    try:
        ensure_database_ready(app_state, conn)
    except BaseException:
        conn.close()
        raise
    return conn


def get_conn(request: Request) -> Iterator:
    """A request-scoped connection.

    Tests set `app.state.override_conn` to the fixture connection. Production
    requires `DATABASE_URL`; a server without one has no database to serve.
    """
    override = getattr(request.app.state, "override_conn", None)
    if override is not None:
        yield override
        return
    conn = open_ready_connection(request.app.state)
    try:
        yield conn
    finally:
        conn.close()


def ensure_database_ready_dependency(request: Request) -> None:
    """Bootstrap schema with a short borrow, returned before the route runs.

    Store-backed routes use their own transaction-scoped connection. This
    dependency ensures they don't retain a request connection while doing so.
    """
    override = getattr(request.app.state, "override_conn", None)
    if override is not None:
        ensure_database_ready(request.app.state, override)
        return
    open_ready_connection(request.app.state).close()


def ensure_database_ready(app_state, conn) -> None:
    """Run one-time schema bootstrap under the app's database lock."""
    if app_state.database_ready:
        return
    with app_state.database_lock:
        if not app_state.database_ready:
            prepare_database(conn, app_state.settings.default_collection)
            app_state.database_ready = True
        conn.commit()


def request_job_store(request: Request, *, state_attr: str,
                      unavailable_detail: str) -> JobStore:
    """The configured JobStore, or one built from DATABASE_URL on demand."""
    configured = getattr(request.app.state, state_attr, None)
    if configured is not None:
        return configured
    dsn = request.app.state.settings.database_url
    if not dsn:
        raise HTTPException(503, unavailable_detail)
    return JobStore(connector(request.app.state.settings))


def resolve_collection(conn, settings: Settings, name: str | None) -> tuple[UUID, str]:
    """HTTP adapter: an unknown collection is a 404, never an empty result."""
    try:
        return lookup_collection(conn, settings, name)
    except UnknownCollection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _admin_panel_enabled(request: Request, principal: Principal) -> Principal:
    if not request.app.state.settings.admin_panel_enabled:
        raise HTTPException(404, "institutional administration is disabled")
    return principal


def panel_admin(request: Request,
                principal: Annotated[Principal, Depends(get_admin_principal)]) -> Principal:
    """An active admin, while the institutional admin surface is enabled."""
    return _admin_panel_enabled(request, principal)


def panel_auditor_or_admin(
    request: Request,
    principal: Annotated[Principal, Depends(get_auditor_or_admin_principal)],
) -> Principal:
    """An active auditor or admin, while the institutional admin surface is enabled."""
    return _admin_panel_enabled(request, principal)


PanelAdmin = Annotated[Principal, Depends(panel_admin)]
PanelAuditorOrAdmin = Annotated[Principal, Depends(panel_auditor_or_admin)]
