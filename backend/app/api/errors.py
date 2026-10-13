"""Safe public artifact diagnostics; payloads and credentials are never echoed."""

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.artifacts.errors import ArtifactError
from app.artifacts.storage import ArtifactNotFound, RevisionConflict


async def artifact_error(request: Request, exc: ArtifactError):
    status = {
        "artifact_limit": 413,
        "office_unavailable": 503,
        "office_failed": 503,
        "calculation_unverified": 409,
    }.get(exc.code, 422)
    return JSONResponse(status_code=status,
                        content={"detail": str(exc), "code": exc.code, "features": exc.details})


async def not_found(request: Request, exc: ArtifactNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def revision_conflict(request: Request, exc: RevisionConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def artifact_validation(request: Request, exc: ValidationError):
    return JSONResponse(status_code=422, content={
        "detail": "artifact data failed validation",
        "errors": [{"type": item["type"], "loc": list(item["loc"]), "msg": item["msg"]}
                   for item in exc.errors(include_input=False, include_context=False)],
    })
