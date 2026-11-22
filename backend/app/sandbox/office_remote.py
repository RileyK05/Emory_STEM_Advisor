"""Authenticated byte-bounded client for isolated LibreOffice jobs."""

from __future__ import annotations

import base64
import hashlib
import threading

from app.sandbox.remote import RemoteSandboxClient, RemoteSandboxConfig
from app.sandbox.types import IsolationUnavailable

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
        input_sha256 = hashlib.sha256(content).hexdigest()
        response, output = self.client.run_job(
            "/v1/office/execute",
            {
                "operation": operation,
                "input_base64": base64.b64encode(content).decode("ascii"),
                "input_sha256": input_sha256,
                "timeout_seconds": timeout_seconds,
            },
            what="Office conversion", image=self.office_image,
            max_input_bytes=self.max_input_bytes, max_output_bytes=self.max_output_bytes,
            request_id=request_id, cancel_event=cancel_event,
        )
        if response.get("operation") != operation or response.get("input_sha256") != input_sha256:
            raise IsolationUnavailable("Office worker returned an invalid or oversized result.")
        return output

    def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
        return self.run_operation("recalculate_xlsx", xlsx_bytes, timeout_seconds=timeout_seconds)

    def render_docx_pdf(self, docx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run_operation("render_docx_pdf", docx_bytes, timeout_seconds=timeout_seconds)

    def render_xlsx_pdf(self, xlsx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run_operation("render_xlsx_pdf", xlsx_bytes, timeout_seconds=timeout_seconds)
