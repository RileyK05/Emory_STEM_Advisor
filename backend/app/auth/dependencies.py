"""Verified identity and browser request boundaries for protected APIs."""
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from app.auth.models import Principal
from app.auth.service import COOKIE_NAME


def _boundary(request: Request, service) -> None:
    try:
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
    except ValueError as exc:
        raise HTTPException(403, detail="invalid application Host") from exc
    if service.mode == "development":
        if any(k in request.headers for k in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")):
            raise HTTPException(403, detail="development mode cannot serve a forwarded request")
        allowed = host in {"localhost", "127.0.0.1", "::1"} or (
            service.allow_test_client and host == "testserver")
        if not allowed:
            raise HTTPException(403, detail="local development Host is required")
        expected_origin = request.url.scheme + "://" + request.headers.get("host", "")
    else:
        callback = urlsplit(service.oidc.redirect_uri)
        expected_origin = f"{callback.scheme}://{callback.netloc}"
        if request.headers.get("host") != callback.netloc:
            raise HTTPException(403, detail="configured application Host is required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        # Development CLI calls need no cookie. Browser calls must match Host.
        if (service.mode == "oidc" and not origin) or (origin and origin != expected_origin):
            raise HTTPException(403, detail="same-origin request is required")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, detail="cross-site API requests are rejected")


def get_principal(request: Request) -> Principal:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="authentication is not configured")
    _boundary(request, service)
    principal = service.resolve(
        request.cookies.get(COOKIE_NAME),
        client_host=request.client.host if request.client else None,
    )
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if principal.status != "active":
        raise HTTPException(status_code=403, detail="user account is disabled")
    return principal


def get_workspace_key(principal: Annotated[Principal, Depends(get_principal)]) -> str:
    return principal.workspace_key


def get_workspace_principal(
    request: Request, principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    from app.auth.capabilities import Capability, require_capability

    capability = (Capability.READ_OWN_WORKSPACE if request.method in {"GET", "HEAD", "OPTIONS"}
                  else Capability.WRITE_OWN_WORKSPACE)
    require_capability(principal, capability)
    return principal


def get_admin_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if principal.role != "admin" or principal.status != "active":
        raise HTTPException(status_code=403, detail="institutional administrator access required")
    return principal


def get_auditor_or_admin_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if principal.role not in {"admin", "auditor"} or principal.status != "active":
        raise HTTPException(status_code=403, detail="institutional auditor or administrator access required")
    return principal
