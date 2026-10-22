"""Authenticated byte-bounded client for isolated LibreOffice jobs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
from uuid import uuid4

from app.sandbox.remote import RemoteSandboxClient, RemoteSandboxConfig
from app.sandbox.types import IsolationUnavailable, SandboxCancelled

_HEX = re.compile(r"^[0-9a-f]{64}$")
_OPERATIONS = {"recalculate_xlsx", "render_docx_pdf", "render_xlsx_pdf"}


class RemoteLibreOfficeRunner:
    """Implements the XLSX recalculation protocol and bounded document rendering."""

    def __init__(
        self,
        config: RemoteSandboxConfig,
        *,
        office_image: str,
        client: RemoteSandboxClient | None = None,
        max_input_bytes: int = 16 * 1024 * 1024,
        max_output_bytes: int = 32 * 1024 * 1024,
    ):
        if not office_image or len(office_image) > 255 or any(c in office_image for c in "\r\n"):
            raise ValueError("office image reference is invalid")
        if not 1 <= max_input_bytes <= 256 * 1024 * 1024:
            raise ValueError("invalid Office input limit")
        if not 1024 <= max_output_bytes <= 64 * 1024 * 1024:
            raise ValueError("invalid Office output limit")
        if config.max_response_bytes < (max_output_bytes * 4 // 3 + 4096):
            raise ValueError("sandbox response limit is too small for Office output")
        self.config = config
        self.office_image = office_image
        self.client = client or RemoteSandboxClient(config)
        self.max_input_bytes = max_input_bytes
        self.max_output_bytes = max_output_bytes

    def run_operation(
        self,
        operation: str,
        content: bytes,
        *,
        timeout_seconds: int = 60,
        request_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bytes:
        if operation not in _OPERATIONS:
            raise ValueError("unsupported Office operation")
        if not isinstance(content, bytes) or not content or len(content) > self.max_input_bytes:
            raise ValueError("Office input is empty or exceeds the configured limit")
        if not 1 <= timeout_seconds <= min(300, self.config.max_timeout_seconds):
            raise ValueError("Office timeout exceeds configured limit")
        request_id = request_id or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise ValueError("invalid Office request ID")
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Office conversion was cancelled.")
        self.client.require_image(
            self.office_image,
            unavailable="Authenticated Office sandbox worker is unavailable.",
            not_ready="Isolated LibreOffice image is not ready on the worker.",
        )
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Office conversion was cancelled.")
        payload = json.dumps({
            "request_id": request_id,
            "operation": operation,
            "input_base64": base64.b64encode(content).decode("ascii"),
            "input_sha256": hashlib.sha256(content).hexdigest(),
            "timeout_seconds": timeout_seconds,
        }, separators=(",", ":")).encode()
        request_limit = self.max_input_bytes * 4 // 3 + 4096
        if len(payload) > min(request_limit, self.config.max_request_bytes):
            raise ValueError("Office request exceeds configured transport limit")
        raw = self.client.post_cancellable(
            "/v1/office/execute", request_id, payload,
            timeout=min(self.config.request_timeout_seconds, timeout_seconds + 30),
            cancel_event=cancel_event, cancelled="Office conversion was cancelled.",
        )
        try:
            response = json.loads(raw)
            encoded = response["output_base64"]
            output = base64.b64decode(encoded, validate=True)
            if (
                response["operation"] != operation
                or response["input_sha256"] != hashlib.sha256(content).hexdigest()
                or not hmac.compare_digest(hashlib.sha256(output).hexdigest(), response["output_sha256"])
                or not _HEX.fullmatch(response["output_sha256"])
                or not output or len(output) > self.max_output_bytes
            ):
                raise ValueError
        except Exception as exc:
            raise IsolationUnavailable("Office worker returned an invalid or oversized result.") from exc
        return output

    def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
        return self.run_operation("recalculate_xlsx", xlsx_bytes, timeout_seconds=timeout_seconds)

    def render_docx_pdf(self, docx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run_operation("render_docx_pdf", docx_bytes, timeout_seconds=timeout_seconds)

    def render_xlsx_pdf(self, xlsx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run_operation("render_xlsx_pdf", xlsx_bytes, timeout_seconds=timeout_seconds)
