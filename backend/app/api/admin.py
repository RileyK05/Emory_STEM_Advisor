"""Loopback-only, opt-in local administration boundary.

Local administration and process metrics are disabled unless `ADMIN_API_ENABLED`
is true, and are reachable only from a loopback client with a loopback Host.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def require_local_admin(request: Request) -> None:
    settings = request.app.state.settings
    if not getattr(settings, "admin_api_enabled", False):
        raise HTTPException(status_code=404, detail="not found")
    if any(name in request.headers for name in (
        "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    )):
        raise HTTPException(
            status_code=403,
            detail="forwarded requests cannot access local administration",
        )
    host_header = request.headers.get("host", "")
    if not loopback_authority(host_header):
        raise HTTPException(
            status_code=403, detail="local administration requires a loopback Host")
    origin = request.headers.get("origin")
    if origin is not None:
        parsed_origin = urlsplit(origin)
        if (parsed_origin.scheme not in {"http", "https"}
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query or parsed_origin.fragment
                or parsed_origin.username or parsed_origin.password
                or not loopback_hostname(parsed_origin.hostname)
                or parsed_origin.hostname.lower().rstrip(".")
                != authority_hostname(host_header)):
            raise HTTPException(
                status_code=403,
                detail="local administration requires a loopback Origin",
            )
    host = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise HTTPException(
            status_code=403,
            detail="local administration is available only on loopback",
        )


def loopback_authority(authority: str) -> bool:
    try:
        parsed = urlsplit(f"//{authority}")
        return (not parsed.username and not parsed.password and parsed.path == ""
                and not parsed.query and not parsed.fragment
                and loopback_hostname(parsed.hostname))
    except ValueError:
        return False


def authority_hostname(authority: str) -> str:
    try:
        return (urlsplit(f"//{authority}").hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
