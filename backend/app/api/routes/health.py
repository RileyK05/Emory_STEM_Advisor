"""Unauthenticated process liveness (/health) and readiness (/ready) endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.schemas import HealthStatus
from app.db.connection import direct_database_url

router = APIRouter()


@router.get("/health", response_model=HealthStatus)
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
def readiness(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    provider = getattr(request.app.state, "provider", None)
    tools_capable = settings.routing_enabled or (
        provider is not None and settings.llm_provider != "hf-local"
    )
    result = request.app.state.readiness_probe.check(
        # The probe sets read-only and timeout startup options a pooler refuses.
        dsn=direct_database_url(settings),
        auth_service=request.app.state.auth_service,
        auth_mode=settings.auth_mode,
        advising_model_configured=tools_capable,
    )
    return JSONResponse(
        result.as_dict(),
        status_code=200 if result.ready else 503,
        headers={"Cache-Control": "no-store"},
    )
