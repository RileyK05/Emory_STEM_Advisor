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

from app.sandbox.types import (
    IsolationUnavailable,
    SandboxCancelled,
    SandboxOutputFile,
    SandboxRequest,
    SandboxResult,
)

_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class RemoteSandboxConfig:
    base_url: str
    bearer_token: str = field(repr=False)
    request_timeout_seconds: int = 150
    max_response_bytes: int = 8 * 1024 * 1024
    max_output_bytes: int = 4 * 1024 * 1024
    max_request_bytes: int = 24 * 1024 * 1024
    max_input_bytes: int = 16 * 1024 * 1024
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
            or not 1024 <= self.max_output_bytes <= 64 * 1024 * 1024
            or not 1024 <= self.max_request_bytes <= 400 * 1024 * 1024
            or not 0 <= self.max_input_bytes <= 256 * 1024 * 1024
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

    def _verify_worker(self) -> None:
        health = self._health("Authenticated production sandbox worker is unavailable.")
        if (
            health is None
            or len(health["image_ids"]) < 2
            or any(not isinstance(value, str) or not _IMAGE_DIGEST.fullmatch(value)
                   for value in health["image_ids"].values())
        ):
            raise IsolationUnavailable(
                "Sandbox worker does not report required production isolation."
            )

    def run(
        self,
        request: SandboxRequest,
        *,
        request_id: str,
        cancel_event: threading.Event | None = None,
    ) -> SandboxResult:
        if (
            not request_id
            or len(request_id) > 128
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for c in request_id
            )
        ):
            raise ValueError("request ID must be an opaque safe identifier")
        self._raise_if_cancelled(cancel_event)
        self._verify_worker()
        self._raise_if_cancelled(cancel_event)
        payload = self._payload(request, request_id)
        raw = self._execute(request_id, payload, cancel_event)
        self._raise_if_cancelled(cancel_event)
        return self._parse_result(raw, request)

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Sandbox execution was cancelled.")

    def _payload(self, request: SandboxRequest, request_id: str) -> bytes:
        inputs = []
        total = len(request.source.encode("utf-8"))
        seen = set()
        for f in request.inputs:
            if (
                not f.resource_id
                or len(f.resource_id) > 128
                or not f.filename
                or f.filename in {".", ".."}
                or any(c in f.filename for c in "/\\:")
                or f.filename in seen
            ):
                raise ValueError("invalid scoped sandbox input")
            seen.add(f.filename)
            total += len(f.content)
            if total > self.config.max_input_bytes:
                raise ValueError("remote sandbox input exceeds configured limit")
            inputs.append(
                {
                    "resource_id": f.resource_id,
                    "filename": f.filename,
                    "content_base64": base64.b64encode(f.content).decode("ascii"),
                    "sha256": hashlib.sha256(f.content).hexdigest(),
                }
            )
        timeout = request.timeout_seconds or self.config.max_timeout_seconds
        if not 1 <= timeout <= self.config.max_timeout_seconds:
            raise ValueError("remote sandbox timeout exceeds configured limit")
        payload = json.dumps(
            {
                "request_id": request_id,
                "language": request.language,
                "source": request.source,
                "timeout_seconds": timeout,
                "inputs": inputs,
            },
            separators=(",", ":"),
        ).encode()
        if len(payload) > self.config.max_request_bytes:
            raise ValueError("remote sandbox request exceeds transport limit")
        return payload

    def _execute(self, request_id: str, payload: bytes, cancel_event: threading.Event | None) -> bytes:
        return self.post_cancellable("/v1/execute", request_id, payload,
                                     timeout=self.config.request_timeout_seconds,
                                     cancel_event=cancel_event)

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

    def _parse_result(self, raw: bytes, request: SandboxRequest) -> SandboxResult:
        try:
            obj = json.loads(raw)
            if (
                not isinstance(obj["files"], list)
                or len(obj["files"]) > self.config.max_output_files
            ):
                raise ValueError
            files = tuple(
                SandboxOutputFile(f["path"], base64.b64decode(f["content_base64"], validate=True))
                for f in obj["files"]
            )
            for file, item in zip(files, obj["files"], strict=True):
                path = file.path
                if (
                    not isinstance(path, str)
                    or not path
                    or path.startswith(("/", "\\"))
                    or "\\" in path
                    or ":" in path
                    or len(path) > 1024
                    or len(path.split("/")) > 8
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                ):
                    raise ValueError
                if not hmac.compare_digest(
                    hashlib.sha256(file.content).hexdigest(), item["sha256"]
                ):
                    raise ValueError
            result = SandboxResult(
                obj["language"],
                int(obj["exit_code"]),
                obj["stdout"],
                obj["stderr"],
                files,
                float(obj["runtime_seconds"]),
            )
        except Exception as exc:
            raise IsolationUnavailable("Sandbox worker returned an invalid response.") from exc
        if (
            result.language != request.language
            or len(result.stdout.encode())
            + len(result.stderr.encode())
            + sum(len(f.content) for f in files)
            > self.config.max_output_bytes
        ):
            raise IsolationUnavailable("Sandbox worker response exceeded configured limits.")
        return result

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
