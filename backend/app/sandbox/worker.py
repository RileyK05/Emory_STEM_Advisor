"""Small authenticated API hosted only on the isolated sandbox worker."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import threading
import time
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.sandbox.office import MAX_OFFICE_INPUT_BYTES, MAX_OFFICE_OUTPUT_BYTES, OfficeRunner
from app.sandbox.screenshot import (
    MAX_HTML_INPUT_BYTES,
    MAX_SCREENSHOT_OUTPUT_BYTES,
    PNG_MAGIC,
    HtmlScreenshotRunner,
)
from app.sandbox.types import (
    IsolationUnavailable,
    SandboxCancelled,
    SandboxConfig,
    SandboxOutputLimitExceeded,
    SandboxTimedOut,
)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class OfficeExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    operation: Literal["recalculate_xlsx", "render_docx_pdf", "render_xlsx_pdf"]
    input_base64: str = Field(min_length=1, max_length=22_369_624)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: int = Field(default=60, ge=1, le=300)

    def job_input(self) -> tuple[str, str]:
        return self.input_base64, self.input_sha256


class HtmlScreenshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    html_base64: str = Field(min_length=1, max_length=5_592_408)
    html_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=320, le=3840)
    height: int = Field(ge=240, le=2160)
    timeout_seconds: int = Field(default=30, ge=1, le=120)

    def job_input(self) -> tuple[str, str]:
        return self.html_base64, self.html_sha256


async def _bounded_json(request: Request, limit: int):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > limit):
        raise HTTPException(413, "request too large")
    data = await request.body()
    if len(data) > limit:
        raise HTTPException(413, "request too large")
    return data


_PRE_CANCEL_TTL_SECONDS = 300
_PRE_CANCEL_LIMIT = 10_000


class _RequestRegistry:
    """Tracks running requests so a cancel can reach them, and remembers cancels that arrive early."""

    def __init__(self):
        self._active: dict[str, threading.Event] = {}
        self._pre_cancelled: dict[str, float] = {}
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        for request_id, created_at in tuple(self._pre_cancelled.items()):
            if now - created_at > _PRE_CANCEL_TTL_SECONDS:
                self._pre_cancelled.pop(request_id, None)

    def start(self, request_id: str) -> threading.Event:
        """Register a request; 409 if it was cancelled before it started or is already running."""
        with self._lock:
            self._expire(time.monotonic())
            if self._pre_cancelled.pop(request_id, None) is not None:
                raise HTTPException(409, "cancelled")
            if request_id in self._active:
                raise HTTPException(409, "request already active")
            event = self._active[request_id] = threading.Event()
            return event

    def finish(self, request_id: str) -> None:
        with self._lock:
            self._active.pop(request_id, None)

    def cancel(self, request_id: str) -> None:
        with self._lock:
            event = self._active.get(request_id)
            if event is None:
                now = time.monotonic()
                self._expire(now)
                if len(self._pre_cancelled) >= _PRE_CANCEL_LIMIT and request_id not in self._pre_cancelled:
                    raise HTTPException(429, "cancellation queue full")
                self._pre_cancelled[request_id] = now
                return
        event.set()


def _execution_error(exc: Exception, what: str) -> HTTPException:
    """Map a sandbox failure to the HTTP status a client may act on; details stay server-side."""
    if isinstance(exc, SandboxCancelled):
        return HTTPException(409, "cancelled")
    if isinstance(exc, SandboxTimedOut):
        return HTTPException(408, "timed out")
    if isinstance(exc, (IsolationUnavailable, SandboxOutputLimitExceeded)):
        return HTTPException(503, f"{what} unavailable or exceeded limits")
    return HTTPException(500, f"{what} failed")


def create_worker_app(
    config: SandboxConfig, bearer_secret: str,
    office_runner: OfficeRunner | None = None,
    screenshot_runner: HtmlScreenshotRunner | None = None,
) -> FastAPI:
    if len(bearer_secret) < 32 or "\r" in bearer_secret or "\n" in bearer_secret:
        raise ValueError("worker bearer secret must contain at least 32 characters")
    config.validate_isolation()
    app = FastAPI(title="Sandbox Worker", docs_url=None, redoc_url=None, openapi_url=None)
    office = office_runner or OfficeRunner(config)
    screenshot = screenshot_runner or HtmlScreenshotRunner(config)
    requests = _RequestRegistry()

    def auth(value: str | None):
        expected = "Bearer " + bearer_secret
        if value is None or not hmac.compare_digest(value, expected):
            raise HTTPException(401, "unauthorized")

    @app.get("/v1/health")
    async def health(authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            image_ids = {config.office_image: office.probe_office()}
            if config.browser_image:
                image_ids[config.browser_image] = screenshot.probe_browser()
        except Exception as exc:
            raise HTTPException(503, "sandbox isolation unavailable") from exc
        return {
            "status": "ready",
            "runtime": config.runtime,
            "separate_host_attested": config.separate_sandbox_host,
            "image_ids": image_ids,
        }

    async def run_job(request: Request, authorization: str | None, body_type: type[BaseModel],
                      *, what: str, max_input_bytes: int, max_output_bytes: int, run,
                      output_magic: bytes = b""):
        """Authenticate, decode and verify one job body, run it, and check its result.

        `body_type.job_input()` gives the base64 input and its declared digest;
        `run(body, content, cancel_event)` executes it and returns a JobResult.
        """
        auth(authorization)
        raw = await _bounded_json(request, max_input_bytes * 4 // 3 + 4096)
        try:
            body = body_type.model_validate_json(raw)
            encoded, expected = body.job_input()
            content = base64.b64decode(encoded, validate=True)
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected):
                raise ValueError
        except Exception as exc:
            raise HTTPException(422, f"invalid {what} request") from exc
        if not content or len(content) > max_input_bytes:
            raise HTTPException(413, f"{what} input too large")
        event = requests.start(body.request_id)
        try:
            result = await asyncio.to_thread(run, body, content, event)
        except Exception as exc:
            raise _execution_error(exc, what) from exc
        finally:
            requests.finish(body.request_id)
        if (
            not result.content
            or len(result.content) > max_output_bytes
            or not result.content.startswith(output_magic)
            or not hmac.compare_digest(result.input_sha256, expected)
            or not hmac.compare_digest(
                hashlib.sha256(result.content).hexdigest(), result.output_sha256)
        ):
            raise HTTPException(503, f"{what} output failed validation")
        return body, result, {
            "output_sha256": result.output_sha256,
            "output_base64": base64.b64encode(result.content).decode("ascii"),
            "runtime_seconds": result.runtime_seconds,
        }

    @app.post("/v1/office/execute")
    async def execute_office(request: Request, authorization: str | None = Header(default=None)):
        body, result, output = await run_job(
            request, authorization, OfficeExecuteBody, what="Office execution",
            max_input_bytes=MAX_OFFICE_INPUT_BYTES, max_output_bytes=MAX_OFFICE_OUTPUT_BYTES,
            run=lambda body, content, event: office.run(
                body.operation, content, timeout_seconds=body.timeout_seconds,
                cancel_event=event),
        )
        return {"operation": body.operation, "input_sha256": result.input_sha256, **output}

    @app.post("/v1/html/screenshot")
    async def execute_screenshot(
        request: Request, authorization: str | None = Header(default=None)
    ):
        body, result, output = await run_job(
            request, authorization, HtmlScreenshotBody, what="screenshot rendering",
            max_input_bytes=MAX_HTML_INPUT_BYTES, max_output_bytes=MAX_SCREENSHOT_OUTPUT_BYTES,
            output_magic=PNG_MAGIC,
            run=lambda body, content, event: screenshot.run(
                content.decode("utf-8"), width=body.width, height=body.height,
                timeout_seconds=body.timeout_seconds, cancel_event=event),
        )
        return {"html_sha256": result.input_sha256, "width": body.width,
                "height": body.height, **output}

    @app.delete("/v1/jobs/{request_id}")
    async def cancel(request_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        if not _ID.fullmatch(request_id):
            raise HTTPException(404, "not found")
        requests.cancel(request_id)
        return {"cancelled": True}

    return app
