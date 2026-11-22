"""Fixed-command HTML screenshots in a disposable isolated browser container."""

from __future__ import annotations

import threading
from pathlib import Path

from app.sandbox.container_job import (
    ContainerJob,
    JobResult,
    build_create_argv,
    probe_image,
    run_container_job,
)
from app.sandbox.runner import ContainerRuntime, DockerCliRuntime
from app.sandbox.types import IsolationUnavailable, SandboxConfig

MAX_HTML_INPUT_BYTES = 4 * 1024 * 1024
MAX_SCREENSHOT_OUTPUT_BYTES = 8 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _check_dimensions(width: int, height: int) -> None:
    if not 320 <= width <= 3840 or not 240 <= height <= 2160:
        raise ValueError("screenshot dimensions are outside the supported range")


def screenshot_job(width: int, height: int) -> ContainerJob:
    _check_dimensions(width, height)
    return ContainerJob(
        kind="shot", what="Screenshot rendering",
        command=("python3", "/usr/local/bin/html-screenshot", str(width), str(height)),
        input_name="input.html", output_name="screenshot.png",
        output_label="PNG", output_magic=PNG_MAGIC,
        max_output_bytes=MAX_SCREENSHOT_OUTPUT_BYTES,
    )


def build_screenshot_create_argv(
    config: SandboxConfig,
    *,
    container_name: str,
    image: str,
    input_dir: Path,
    output_dir: Path,
    width: int,
    height: int,
    timeout_seconds: int,
) -> list[str]:
    """The `docker create` argv for one screenshot."""
    return build_create_argv(
        config, screenshot_job(width, height), name=container_name, image=image,
        input_dir=input_dir, output_dir=output_dir, timeout_seconds=timeout_seconds,
    )


class HtmlScreenshotRunner:
    """Isolated Chromium screenshot runner; no browser opens in the web process."""

    def __init__(self, config: SandboxConfig, runtime: ContainerRuntime | None = None):
        self.config = config
        self.runtime = runtime or DockerCliRuntime(
            create_timeout_seconds=config.create_timeout_seconds
        )

    def probe_browser(self) -> str:
        if not self.config.browser_image:
            raise IsolationUnavailable("No screenshot browser image is configured.")
        return probe_image(self.runtime, self.config, self.config.browser_image,
                           "Pinned screenshot browser image is unavailable.")

    def run(
        self,
        html: str,
        *,
        width: int,
        height: int,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> JobResult:
        self.config.validate_isolation()
        if not self.config.browser_image:
            raise IsolationUnavailable("No screenshot browser image is configured.")
        if not isinstance(html, str) or not html or len(html.encode("utf-8")) > MAX_HTML_INPUT_BYTES:
            raise ValueError("HTML screenshot input is empty or exceeds the byte limit")
        job = screenshot_job(width, height)
        timeout = timeout_seconds or min(60, self.config.max_timeout_seconds)
        if not 1 <= timeout <= self.config.max_timeout_seconds:
            raise ValueError("Screenshot timeout exceeds configured limit")
        return run_container_job(
            self.config, self.runtime, job, html.encode("utf-8"),
            resolve_image=(self.probe_browser if self.config.production
                           else lambda: self.config.browser_image),
            timeout_seconds=timeout, cancel_event=cancel_event,
        )
