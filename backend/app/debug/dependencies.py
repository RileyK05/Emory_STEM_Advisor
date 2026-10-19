"""Request-scoped debug override resolution."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.api import deps
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.debug.registry import OverrideError
from app.debug.store import AppliedDebug, DebugStore


def get_debug(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    conn: Annotated[object, Depends(deps.get_conn)],
    token: Annotated[str | None, Header(alias="X-Debug-Session")] = None,
) -> AppliedDebug | None:
    if not token:
        return None
    if principal.role != "admin" or principal.status != "active":
        raise HTTPException(403, "institutional administrator access required")
    if not (request.app.state.settings.admin_panel_enabled
            and request.app.state.settings.debug_panel_enabled):
        raise HTTPException(404, "debugging is disabled")
    try:
        value = DebugStore(conn, principal).resolve(
            token, settings=request.app.state.settings,
            config=request.app.state.config)
    except OverrideError as exc:
        raise HTTPException(422, str(exc)) from None
    if value is None:
        raise HTTPException(401, "debug session is invalid or expired")
    return value
