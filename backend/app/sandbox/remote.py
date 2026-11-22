"""Authenticated HTTPS client for a separately deployed sandbox worker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from app.sandbox.types import IsolationUnavailable, SandboxCancelled

_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class RemoteSandboxConfig:
    base_url: str
    bearer_token: str = field(repr=False)
    request_timeout_seconds: int = 150
    max_response_bytes: int = 8 * 1024 * 1024
    max_request_bytes: int = 24 * 1024 * 1024
    max_timeout_seconds: int = 120
    max_output_files: int = 100
    production: bool = True

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "sandbox worker URL must be an HTTPS origin without credentials, query, or fragment"
            )
        if (
            not self.bearer_token
            or len(self.bearer_token) < 32
            or any(c in self.bearer_token for c in "\r\n")
        ):
            raise ValueError("sandbox worker bearer token must contain at least 32 characters")
        if (
            not 1 <= self.request_timeout_seconds <= 660
            or not 1024 <= self.max_response_bytes <= 72 * 1024 * 1024
            or not 1024 <= self.max_request_bytes <= 400 * 1024 * 1024
            or not 1 <= self.max_timeout_seconds <= 600
        ):
            raise ValueError("invalid remote sandbox transport bounds")


class RemoteSandboxClient:
    def __init__(self, config: RemoteSandboxConfig, *, opener=None):
        self.config = config
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def _health(self, unavailable: str) -> dict | None:
        """The worker's health report if it attests gVisor on a separate host, else None."""
        try:
            health = json.loads(self._request("GET", "/v1/health", "health", b"", 5))
        except Exception as exc:
            raise IsolationUnavailable(unavailable) from exc
        if (
            not isinstance(health, dict)
            or health.get("status") != "ready"
            or health.get("runtime") != "runsc"
            or health.get("separate_host_attested") is not True
            or not isinstance(health.get("image_ids"), dict)
        ):
            return None
        return health

    def require_image(self, image: str, *, unavailable: str, not_ready: str) -> None:
        """Fail closed unless the attested worker reports a pinned digest for `image`."""
        health = self._health(unavailable)
        if health is None or not _IMAGE_DIGEST.fullmatch(str(health["image_ids"].get(image, ""))):
            raise IsolationUnavailable(not_ready)

    def post_cancellable(self, path: str, request_id: str, payload: bytes, *, timeout: float,
                         cancel_event: threading.Event | None,
                         cancelled: str = "Sandbox execution was cancelled.") -> bytes:
        """POST a job, forwarding a cancellation to the worker while the request is in flight."""
        finished = threading.Event()

        def watch():
            while not finished.wait(0.05):
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        self.cancel(request_id)
                    finally:
                        finished.set()
                    return

        watcher = threading.Thread(target=watch, daemon=True) if cancel_event is not None else None
        if watcher:
            watcher.start()
        try:
            raw = self._request("POST", path, request_id, payload, timeout)
        except IsolationUnavailable as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise SandboxCancelled(cancelled) from exc
            raise
        finally:
            finished.set()
            if watcher:
                watcher.join(timeout=0.2)
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled(cancelled)
        return raw

    def run_job(
        self,
        path: str,
        fields: dict,
        *,
        what: str,
        image: str,
        max_input_bytes: int,
        max_output_bytes: int,
        output_magic: bytes = b"",
        request_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[dict, bytes]:
        """Send one job and return the worker's response with its verified output.

        `fields` is the job's own request body (its base64 input, digest and
        timeout); this adds the request ID. The worker must be attested and hold
        a pinned digest for `image`, and the output it returns must be non-empty,
        within `max_output_bytes`, start with `output_magic` and match its digest.
        Checks that the response echoes the job's own fields are the caller's.
        """
        request_id = request_id or uuid4().hex
        if not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("invalid sandbox request ID")
        cancelled = f"{what} was cancelled."
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled(cancelled)
        self.require_image(
            image,
            unavailable=f"{what} sandbox worker is unavailable.",
            not_ready=f"{what} image is not ready on the worker.",
        )
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled(cancelled)
        payload = json.dumps({"request_id": request_id, **fields},
                             separators=(",", ":")).encode()
        if len(payload) > min(max_input_bytes * 4 // 3 + 4096, self.config.max_request_bytes):
            raise ValueError(f"{what} request exceeds configured transport limit")
        raw = self.post_cancellable(
            path, request_id, payload,
            timeout=min(self.config.request_timeout_seconds, fields["timeout_seconds"] + 30),
            cancel_event=cancel_event, cancelled=cancelled,
        )
        try:
            response = json.loads(raw)
            output = base64.b64decode(response["output_base64"], validate=True)
            digest = response["output_sha256"]
            if (
                not isinstance(digest, str)
                or not _SHA256_HEX.fullmatch(digest)
                or not hmac.compare_digest(hashlib.sha256(output).hexdigest(), digest)
                or not output
                or len(output) > max_output_bytes
                or not output.startswith(output_magic)
            ):
                raise ValueError
        except Exception as exc:
            raise IsolationUnavailable(
                f"{what} worker returned an invalid or oversized result."
            ) from exc
        return response, output

    def cancel(self, request_id: str) -> None:
        self._request("DELETE", "/v1/jobs/" + request_id, request_id, b"", 5)

    def _request(self, method, path, request_id, data, timeout):
        url = self.config.base_url.rstrip("/") + path
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.config.bearer_token,
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
            },
        )
        try:
            with self.opener.open(req, timeout=timeout) as response:
                if response.status != 200:
                    raise IsolationUnavailable("Sandbox worker returned an unexpected status.")
                length = response.headers.get("Content-Length")
                if length and int(length) > self.config.max_response_bytes:
                    raise IsolationUnavailable(
                        "Sandbox worker response exceeded configured limits."
                    )
                raw = response.read(self.config.max_response_bytes + 1)
                if len(raw) > self.config.max_response_bytes:
                    raise IsolationUnavailable(
                        "Sandbox worker response exceeded configured limits."
                    )
                return raw
        except urllib.error.URLError as exc:
            raise IsolationUnavailable("Sandbox worker is unavailable.") from exc
