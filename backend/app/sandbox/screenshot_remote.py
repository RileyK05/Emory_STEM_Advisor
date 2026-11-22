"""Authenticated bounded client for isolated HTML screenshots."""

from __future__ import annotations

import base64
import hashlib
import threading

from app.sandbox.remote import RemoteSandboxClient, RemoteSandboxConfig
from app.sandbox.types import IsolationUnavailable

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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
        encoded = html.encode("utf-8")
        html_sha256 = hashlib.sha256(encoded).hexdigest()
        response, output = self.client.run_job(
            "/v1/html/screenshot",
            {
                "html_base64": base64.b64encode(encoded).decode("ascii"),
                "html_sha256": html_sha256,
                "width": width,
                "height": height,
                "timeout_seconds": timeout_seconds,
            },
            what="Screenshot rendering", image=self.browser_image,
            max_input_bytes=self.max_input_bytes, max_output_bytes=self.max_output_bytes,
            output_magic=_PNG_MAGIC, request_id=request_id, cancel_event=cancel_event,
        )
        if (response.get("html_sha256") != html_sha256 or response.get("width") != width
                or response.get("height") != height):
            raise IsolationUnavailable("Screenshot worker returned an invalid or oversized result.")
        return output
