"""Unauthenticated process liveness (/health) and readiness (/ready) endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.schemas import HealthStatus

router = APIRouter()


@router.get("/health", response_model=HealthStatus)
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
def readiness(request: Request) -> JSONResponse:
    result = request.app.state.readiness_probe.check(
        dsn=request.app.state.settings.database_url,
        auth_service=request.app.state.auth_service,
        auth_mode=request.app.state.settings.auth_mode,
    )
    return JSONResponse(
        result.as_dict(),
        status_code=200 if result.ready else 503,
        headers={"Cache-Control": "no-store"},
    )
