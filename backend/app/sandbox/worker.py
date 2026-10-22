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

from app.sandbox.office import MAX_OFFICE_INPUT_BYTES, OfficeRunner
from app.sandbox.runner import SandboxRunner
from app.sandbox.screenshot import MAX_HTML_INPUT_BYTES, HtmlScreenshotRunner
from app.sandbox.types import (
    IsolationUnavailable,
    SandboxCancelled,
    SandboxConfig,
    SandboxInputFile,
    SandboxOutputLimitExceeded,
    SandboxRequest,
    SandboxTimedOut,
)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RESOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


class InputFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resource_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=128)
    content_base64: str = Field(max_length=22_369_624)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OfficeExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    operation: Literal["recalculate_xlsx", "render_docx_pdf", "render_xlsx_pdf"]
    input_base64: str = Field(min_length=1, max_length=22_369_624)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class HtmlScreenshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    html_base64: str = Field(min_length=1, max_length=5_592_408)
    html_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=320, le=3840)
    height: int = Field(ge=240, le=2160)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class ExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    language: Literal["python", "javascript"]
    source: str = Field(min_length=1, max_length=2_000_000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    inputs: list[InputFile] = Field(default_factory=list, max_length=100)


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


def _decode_inputs(body: ExecuteBody) -> tuple[list[SandboxInputFile], int]:
    """Validate and decode the request's input files: (files, total bytes including the source)."""
    total = len(body.source.encode("utf-8"))
    files = []
    try:
        for item in body.inputs:
            if (
                not _RESOURCE.fullmatch(item.resource_id)
                or not _FILENAME.fullmatch(item.filename)
                or item.filename in {".", ".."}
                or any(c in item.filename for c in "/\\:")
            ):
                raise ValueError
            content = base64.b64decode(item.content_base64, validate=True)
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), item.sha256):
                raise ValueError
            total += len(content)
            files.append(SandboxInputFile(item.resource_id, item.filename, content))
    except Exception as exc:
        raise HTTPException(422, "invalid input file") from exc
    return files, total


def _program_response(result) -> dict:
    return {
        "language": result.language,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "runtime_seconds": result.runtime_seconds,
        "files": [
            {
                "path": f.path,
                "content_base64": base64.b64encode(f.content).decode("ascii"),
                "sha256": hashlib.sha256(f.content).hexdigest(),
            }
            for f in result.files
        ],
    }


def _office_response(result, body: OfficeExecuteBody) -> dict:
    if (
        result.operation != body.operation
        or not result.content
        or len(result.content) > 32 * 1024 * 1024
        or not hmac.compare_digest(result.input_sha256, body.input_sha256)
        or not hmac.compare_digest(hashlib.sha256(result.content).hexdigest(), result.output_sha256)
    ):
        raise HTTPException(503, "Office output failed validation")
    return {
        "operation": result.operation,
        "input_sha256": result.input_sha256,
        "output_sha256": result.output_sha256,
        "output_base64": base64.b64encode(result.content).decode("ascii"),
        "runtime_seconds": result.runtime_seconds,
    }


def _screenshot_response(result, body: HtmlScreenshotBody) -> dict:
    if (
        not result.content
        or len(result.content) > 8 * 1024 * 1024
        or not hmac.compare_digest(result.input_sha256, body.html_sha256)
        or not hmac.compare_digest(hashlib.sha256(result.content).hexdigest(), result.output_sha256)
        or not result.content.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise HTTPException(503, "Screenshot output failed validation")
    return {
        "html_sha256": result.input_sha256,
        "width": body.width,
        "height": body.height,
        "output_sha256": result.output_sha256,
        "output_base64": base64.b64encode(result.content).decode("ascii"),
        "runtime_seconds": result.runtime_seconds,
    }


def create_worker_app(
    config: SandboxConfig, bearer_secret: str, runner: SandboxRunner | None = None,
    office_runner: OfficeRunner | None = None,
    screenshot_runner: HtmlScreenshotRunner | None = None,
) -> FastAPI:
    if len(bearer_secret) < 32 or "\r" in bearer_secret or "\n" in bearer_secret:
        raise ValueError("worker bearer secret must contain at least 32 characters")
    config.validate_isolation()
    app = FastAPI(title="Sandbox Worker", docs_url=None, redoc_url=None, openapi_url=None)
    executor = runner or SandboxRunner(config)
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
            image_ids = executor.probe_production()
            office_image_id = office.probe_office()
            image_ids.setdefault(config.office_image, office_image_id)
        except Exception as exc:
            raise HTTPException(503, "sandbox isolation unavailable") from exc
        return {
            "status": "ready",
            "runtime": config.runtime,
            "separate_host_attested": config.separate_sandbox_host,
            "image_ids": image_ids,
        }

    @app.post("/v1/execute")
    async def execute(request: Request, authorization: str | None = Header(default=None)):
        auth(authorization)
        raw = await _bounded_json(request, (config.max_input_bytes * 4 // 3) + 2_000_000)
        try:
            body = ExecuteBody.model_validate_json(raw)
        except Exception as exc:
            raise HTTPException(422, "invalid request") from exc
        files, total = _decode_inputs(body)
        if total > config.max_input_bytes:
            raise HTTPException(413, "input too large")
        event = requests.start(body.request_id)
        try:
            result = await asyncio.to_thread(
                executor.run,
                SandboxRequest(body.language, body.source, tuple(files), body.timeout_seconds),
                cancel_event=event,
            )
        except Exception as exc:
            raise _execution_error(exc, "execution") from exc
        finally:
            requests.finish(body.request_id)
        return _program_response(result)

    @app.post("/v1/office/execute")
    async def execute_office(request: Request, authorization: str | None = Header(default=None)):
        auth(authorization)
        raw = await _bounded_json(request, (MAX_OFFICE_INPUT_BYTES * 4 // 3) + 4096)
        try:
            body = OfficeExecuteBody.model_validate_json(raw)
            content = base64.b64decode(body.input_base64, validate=True)
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), body.input_sha256):
                raise ValueError
        except Exception as exc:
            raise HTTPException(422, "invalid Office request") from exc
        if not content or len(content) > MAX_OFFICE_INPUT_BYTES:
            raise HTTPException(413, "Office input too large")
        event = requests.start(body.request_id)
        try:
            result = await asyncio.to_thread(
                office.run, body.operation, content,
                timeout_seconds=body.timeout_seconds, cancel_event=event,
            )
        except Exception as exc:
            raise _execution_error(exc, "Office execution") from exc
        finally:
            requests.finish(body.request_id)
        return _office_response(result, body)

    @app.post("/v1/html/screenshot")
    async def execute_screenshot(
        request: Request, authorization: str | None = Header(default=None)
    ):
        auth(authorization)
        raw = await _bounded_json(request, (MAX_HTML_INPUT_BYTES * 4 // 3) + 4096)
        try:
            body = HtmlScreenshotBody.model_validate_json(raw)
            content = base64.b64decode(body.html_base64, validate=True)
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), body.html_sha256):
                raise ValueError
        except Exception as exc:
            raise HTTPException(422, "invalid screenshot request") from exc
        if not content or len(content) > MAX_HTML_INPUT_BYTES:
            raise HTTPException(413, "screenshot input too large")
        event = requests.start(body.request_id)
        try:
            result = await asyncio.to_thread(
                screenshot.run, content.decode("utf-8"),
                width=body.width, height=body.height,
                timeout_seconds=body.timeout_seconds, cancel_event=event,
            )
        except Exception as exc:
            raise _execution_error(exc, "screenshot rendering") from exc
        finally:
            requests.finish(body.request_id)
        return _screenshot_response(result, body)

    @app.delete("/v1/jobs/{request_id}")
    async def cancel(request_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        if not _ID.fullmatch(request_id):
            raise HTTPException(404, "not found")
        requests.cancel(request_id)
        return {"cancelled": True}

    return app
