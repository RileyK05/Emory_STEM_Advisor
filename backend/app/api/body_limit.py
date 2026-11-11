"""Bound incoming bodies before multipart parsing can spool unbounded data."""

import json

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

NUL_DETAIL = "Request text cannot contain a NUL character."


def _has_nul(value) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(key) or _has_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_nul(item) for item in value)
    return False


class NulTextMiddleware:
    """A NUL character in request text is a 422, for every route at once.

    Postgres text cannot hold one, so it would otherwise fail deep in a handler
    as a 500. Only JSON bodies and query strings are read: an upload is binary.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        refuse = JSONResponse({"detail": NUL_DETAIL}, 422)
        if b"%00" in scope.get("query_string", b""):
            return await refuse(scope, receive, send)
        headers = dict(scope.get("headers", []))
        if not headers.get(b"content-type", b"").startswith(b"application/json"):
            return await self.app(scope, receive, send)
        chunks, more = [], True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        if b"\\u0000" in body:
            try:
                if _has_nul(json.loads(body)):
                    return await refuse(scope, receive, send)
            except ValueError:
                pass  # the route reports malformed JSON itself
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


class MaxBodyMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "invalid content length"}, 400)(scope, receive, send)
        if length > self.max_bytes or length < 0:
            return await JSONResponse({"detail": "request exceeds configured size limit"}, 413)(
                scope, receive, send,
            )
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, "request exceeds configured size limit")
            return message

        await self.app(scope, bounded_receive, send)
