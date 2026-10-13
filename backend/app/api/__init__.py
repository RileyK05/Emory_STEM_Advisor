"""FastAPI application factory.

Providers are constructed once at startup and stored on `app.state`:
- `embedder`, `provider` — real ones by default; tests overwrite both.
- `settings`, `config` — env + retrieval.toml.
- `override_conn` — optional; tests set it to borrow their DB fixture.
"""

from threading import Lock

from fastapi import FastAPI

from app.config import Settings
from app.retrieval.config import RetrievalConfig


def _office_runner(settings: Settings):
    """The remote LibreOffice runner, when Office export/recalculation is enabled."""
    if not settings.office_enabled:
        return None
    from app.sandbox.office_remote import RemoteLibreOfficeRunner
    from app.sandbox.remote import RemoteSandboxConfig

    return RemoteLibreOfficeRunner(
        RemoteSandboxConfig(
            base_url=settings.sandbox_remote_url,
            bearer_token=settings.sandbox_bearer_token,
            request_timeout_seconds=int(settings.office_timeout_seconds) + 30,
            max_timeout_seconds=int(settings.office_timeout_seconds),
            max_response_bytes=48 * 1024 * 1024,
            max_output_bytes=settings.office_max_output_bytes,
            max_input_bytes=settings.office_max_input_bytes,
        ),
        office_image=settings.sandbox_office_image,
        max_input_bytes=settings.office_max_input_bytes,
        max_output_bytes=settings.office_max_output_bytes,
    )


def _auth_connection_factory(app: FastAPI):
    """A connection opener for the auth service that first makes sure the schema is ready."""
    from app.api import deps
    from app.db.connection import connector

    def auth_connect():
        if not app.state.settings.database_url:
            from app.auth.service import AuthConfigurationError

            raise AuthConfigurationError("authentication database is unavailable")
        conn = connector(app.state.settings)()
        try:
            deps.ensure_database_ready(app.state, conn)
            return conn
        except Exception:
            conn.close()
            raise

    return auth_connect


def _include_routers(app: FastAPI) -> None:
    """Mount every router, grouped by who may call it."""
    from fastapi import Depends

    from app.api import deps
    from app.api.routes.artifacts import router as artifact_router
    from app.api.routes.auth import router as auth_router
    from app.api.routes.batches import router as batch_router
    from app.api.routes.connections import router as connection_router
    from app.api.routes.conversation_summaries import router as summary_router
    from app.api.routes.conversations import router as conversation_router
    from app.api.routes.corpus import router
    from app.api.routes.decisions import router as decision_router
    from app.api.routes.gaps import router as gap_router
    from app.api.routes.health import router as health_router
    from app.api.routes.runs import router as run_router
    from app.auth.dependencies import get_principal

    # Public: no identity required.
    app.include_router(health_router)
    app.include_router(auth_router)
    # Local administration guards itself (loopback-only; see routes.connections._admin).
    app.include_router(connection_router)
    # Everything else requires an authenticated principal.
    for authenticated_router in (router, artifact_router, conversation_router, summary_router,
                                 gap_router, batch_router):
        app.include_router(authenticated_router, dependencies=[Depends(get_principal)])
    # Run handlers own their short transactions through JobStore. Bootstrap
    # with a short borrow and return it before the handler starts.
    app.include_router(run_router, dependencies=[
        Depends(get_principal), Depends(deps.ensure_database_ready_dependency),
    ])
    app.include_router(decision_router,
                       dependencies=[Depends(get_principal), Depends(deps.get_conn)])


def _install_error_handlers(app: FastAPI) -> None:
    from pydantic import ValidationError

    from app.api import errors
    from app.artifacts.errors import ArtifactError
    from app.artifacts.storage import ArtifactNotFound, RevisionConflict

    app.add_exception_handler(ArtifactError, errors.artifact_error)
    app.add_exception_handler(ArtifactNotFound, errors.not_found)
    app.add_exception_handler(RevisionConflict, errors.revision_conflict)
    app.add_exception_handler(ValidationError, errors.artifact_validation)


def create_app(
    *,
    settings: Settings | None = None,
    config: RetrievalConfig | None = None,
    embedder=None,
    provider=None,
    auth_service=None,
    allow_test_client: bool = False,
    readiness_connect=None,
) -> FastAPI:
    from app.api.body_limit import MaxBodyMiddleware
    from app.auth.factory import build_auth_service
    from app.db.connection import connect
    from app.ops.readiness import ReadinessProbe
    from app.runtime import build_runtime

    app = FastAPI(title="Emory STEM Advisor API")
    runtime = build_runtime(settings, config=config, embedder=embedder, provider=provider)
    state = app.state
    state.settings = runtime.settings
    state.config = runtime.config
    state.embedder = runtime.embedder
    state.provider = runtime.provider
    state.provider_error = runtime.provider_error
    state.connection_profiles = runtime.connection_profiles
    state.connection_runtime = runtime.connection_runtime
    app.add_middleware(MaxBodyMiddleware, max_bytes=state.settings.request_max_bytes)
    state.override_conn = None
    state.database_ready = False
    state.database_lock = Lock()
    state.readiness_probe = ReadinessProbe(
        timeout_seconds=state.settings.readiness_timeout_seconds,
        connect_fn=readiness_connect or connect,
    )
    state.auth_service = auth_service or build_auth_service(
        state.settings, _auth_connection_factory(app), allow_test_client=allow_test_client)
    state.office_runner = _office_runner(state.settings)

    @app.middleware("http")
    async def private_response_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    _include_routers(app)
    _install_error_handlers(app)
    return app


def app() -> FastAPI:  # uvicorn target: `uvicorn app.api:app`
    return create_app()
