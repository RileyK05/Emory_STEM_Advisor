"""Bound incoming bodies before multipart parsing can spool unbounded data."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


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
