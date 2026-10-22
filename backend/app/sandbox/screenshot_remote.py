"""Authenticated bounded client for isolated HTML screenshots."""

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


class RemoteHtmlScreenshotRunner:
    """Render HTML to PNG on the separate sandbox host, never in the app process."""

    def __init__(
        self,
        config: RemoteSandboxConfig,
        *,
        browser_image: str,
        client: RemoteSandboxClient | None = None,
        max_input_bytes: int = 4 * 1024 * 1024,
        max_output_bytes: int = 8 * 1024 * 1024,
    ):
        if not browser_image or len(browser_image) > 255 or any(
            c in browser_image for c in "\r\n"
        ):
            raise ValueError("browser image reference is invalid")
        if not 1 <= max_input_bytes <= 64 * 1024 * 1024:
            raise ValueError("invalid screenshot input limit")
        if not 1024 <= max_output_bytes <= 32 * 1024 * 1024:
            raise ValueError("invalid screenshot output limit")
        if config.max_response_bytes < (max_output_bytes * 4 // 3 + 4096):
            raise ValueError("sandbox response limit is too small for screenshot output")
        self.config = config
        self.browser_image = browser_image
        self.client = client or RemoteSandboxClient(config)
        self.max_input_bytes = max_input_bytes
        self.max_output_bytes = max_output_bytes

    def render(
        self,
        html: str,
        *,
        width: int,
        height: int,
        timeout_seconds: int = 30,
        request_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bytes:
        if not isinstance(html, str) or not html or len(html.encode("utf-8")) > self.max_input_bytes:
            raise ValueError("screenshot input is empty or exceeds the configured limit")
        if not 320 <= width <= 3840 or not 240 <= height <= 2160:
            raise ValueError("screenshot dimensions are outside the supported range")
        if not 1 <= timeout_seconds <= min(120, self.config.max_timeout_seconds):
            raise ValueError("screenshot timeout exceeds configured limit")
        request_id = request_id or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise ValueError("invalid screenshot request ID")
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Screenshot rendering was cancelled.")
        self.client.require_image(
            self.browser_image,
            unavailable="Authenticated screenshot sandbox worker is unavailable.",
            not_ready="Isolated screenshot browser image is not ready on the worker.",
        )
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Screenshot rendering was cancelled.")
        encoded = html.encode("utf-8")
        payload = json.dumps({
            "request_id": request_id,
            "html_base64": base64.b64encode(encoded).decode("ascii"),
            "html_sha256": hashlib.sha256(encoded).hexdigest(),
            "width": width,
            "height": height,
            "timeout_seconds": timeout_seconds,
        }, separators=(",", ":")).encode()
        if len(payload) > min(self.max_input_bytes * 4 // 3 + 4096, self.config.max_request_bytes):
            raise ValueError("screenshot request exceeds configured transport limit")
        raw = self.client.post_cancellable(
            "/v1/html/screenshot", request_id, payload,
            timeout=min(self.config.request_timeout_seconds, timeout_seconds + 30),
            cancel_event=cancel_event, cancelled="Screenshot rendering was cancelled.",
        )
        try:
            response = json.loads(raw)
            output = base64.b64decode(response["output_base64"], validate=True)
            if (
                response["html_sha256"] != hashlib.sha256(encoded).hexdigest()
                or response["width"] != width
                or response["height"] != height
                or not hmac.compare_digest(
                    hashlib.sha256(output).hexdigest(), response["output_sha256"]
                )
                or not _HEX.fullmatch(response["output_sha256"])
                or not output
                or len(output) > self.max_output_bytes
                or not output.startswith(b"\x89PNG\r\n\x1a\n")
            ):
                raise ValueError
        except Exception as exc:
            raise IsolationUnavailable(
                "Screenshot worker returned an invalid or oversized result."
            ) from exc
        return output
