"""Fixed-command HTML screenshots in a disposable isolated browser container."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.sandbox.runner import (
    ContainerRuntime,
    DockerCliRuntime,
    SandboxRunner,
    _memory_limit,
    _retry_after_chmod,
    _validate_image,
)
from app.sandbox.types import (
    IsolationUnavailable,
    SandboxCancelled,
    SandboxConfig,
    SandboxOutputLimitExceeded,
    SandboxTimedOut,
)

MAX_HTML_INPUT_BYTES = 4 * 1024 * 1024
MAX_SCREENSHOT_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_STDIO_BYTES = 64 * 1024
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ScreenshotJobResult:
    content: bytes
    input_sha256: str
    output_sha256: str
    runtime_seconds: float


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
    config.validate_isolation()
    if not re.fullmatch(r"advisor-shot-[0-9a-f]{32}", container_name):
        raise ValueError("container name must be generated internally")
    if not 320 <= width <= 3840 or not 240 <= height <= 2160:
        raise ValueError("screenshot dimensions are outside the supported range")
    if not 1 <= timeout_seconds <= config.max_timeout_seconds:
        raise ValueError("timeout exceeds configured screenshot limit")
    root = config.work_root.resolve(strict=True)
    mounts = []
    for original in (input_dir, output_dir):
        if original.is_symlink():
            raise ValueError("screenshot bind mount cannot be a symlink")
        directory = original.resolve(strict=True)
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise ValueError("screenshot bind mount must stay under approved work root") from exc
        current = original
        while current != root and current != current.parent:
            if current.is_symlink():
                raise ValueError("screenshot bind mount path contains a symlink")
            current = current.parent
        if current != root or "," in str(directory):
            raise ValueError("screenshot bind mount path is invalid")
        mounts.append(directory)
    input_dir, output_dir = mounts
    cpu = format(config.max_cpu_count, ".3f").rstrip("0").rstrip(".")
    args = [
        "docker", "create", "--name", container_name,
        "--label", "app=emory-stem-advisor-screenshot",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", "65532:65532",
        "--pids-limit", str(config.max_pids), "--cpus", cpu,
        "--memory", _memory_limit(config.max_memory_bytes),
        "--memory-swap", _memory_limit(config.max_memory_bytes),
        "--ulimit", f"cpu={timeout_seconds}:{timeout_seconds}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", f"type=bind,source={str(input_dir).replace(chr(92), '/')},target=/inputs,readonly",
        "--mount", f"type=bind,source={str(output_dir).replace(chr(92), '/')},target=/output",
        "--env", "HOME=/tmp",
        "--workdir", "/output", _validate_image(image),
        "python3", "/usr/local/bin/html-screenshot", str(width), str(height),
    ]
    if config.runtime:
        image_index = args.index(_validate_image(image))
        args[image_index:image_index] = ["--runtime", config.runtime]
    return args


class HtmlScreenshotRunner:
    """Isolated Chromium screenshot runner; no browser opens in the web process."""

    def __init__(self, config: SandboxConfig, runtime: ContainerRuntime | None = None):
        self.config = config
        self.runtime = runtime or DockerCliRuntime(
            create_timeout_seconds=config.create_timeout_seconds
        )
        import dataclasses

        self._collector = SandboxRunner(
            dataclasses.replace(config, max_output_bytes=MAX_SCREENSHOT_OUTPUT_BYTES)
        )

    def probe_browser(self) -> str:
        if not self.config.browser_image:
            raise IsolationUnavailable("No screenshot browser image is configured.")
        probe = getattr(self.runtime, "probe", None)
        if probe is None:
            raise IsolationUnavailable(
                "Screenshot rendering requires a probed Docker/gVisor runtime."
            )
        images = probe(self.config)
        image_id = images.get(self.config.browser_image)
        if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
            raise IsolationUnavailable("Pinned screenshot browser image is unavailable.")
        return image_id

    def run(
        self,
        html: str,
        *,
        width: int,
        height: int,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ScreenshotJobResult:
        self.config.validate_isolation()
        if not self.config.browser_image:
            raise IsolationUnavailable("No screenshot browser image is configured.")
        if not isinstance(html, str) or not html or len(html.encode("utf-8")) > MAX_HTML_INPUT_BYTES:
            raise ValueError("HTML screenshot input is empty or exceeds the byte limit")
        if not 320 <= width <= 3840 or not 240 <= height <= 2160:
            raise ValueError("screenshot dimensions are outside the supported range")
        timeout = timeout_seconds or min(60, self.config.max_timeout_seconds)
        if not 1 <= timeout <= self.config.max_timeout_seconds:
            raise ValueError("Screenshot timeout exceeds configured limit")
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Screenshot execution was cancelled.")

        root = self.config.work_root
        if root.is_symlink():
            raise ValueError("screenshot work root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
        task_dir = Path(tempfile.mkdtemp(prefix="shot-", dir=resolved_root))
        try:
            input_dir, output_dir = task_dir / "inputs", task_dir / "outputs"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o777)
            source = input_dir / "input.html"
            source.write_bytes(html.encode("utf-8"))
            source.chmod(0o444)
            input_dir.chmod(0o555)
            name = "advisor-shot-" + uuid4().hex
            image = (
                self.probe_browser()
                if self.config.production
                else self.config.browser_image
            )
            argv = build_screenshot_create_argv(
                self.config, container_name=name, image=image, input_dir=input_dir,
                output_dir=output_dir, width=width, height=height,
                timeout_seconds=timeout,
            )
            started = time.monotonic()
            result = self.runtime.execute(
                argv, container_name=name, timeout_seconds=timeout,
                output_limit_bytes=MAX_SCREENSHOT_STDIO_BYTES, cancel_event=cancel_event,
            )
            elapsed = time.monotonic() - started
            if result.cancelled:
                raise SandboxCancelled("Screenshot execution was cancelled.")
            if result.timed_out:
                raise SandboxTimedOut("Screenshot rendering exceeded its time limit.")
            if result.output_exceeded:
                raise SandboxOutputLimitExceeded("Screenshot process output exceeded its limit.")
            if result.exit_code != 0:
                raise IsolationUnavailable("Isolated screenshot rendering failed.")
            outputs = self._collector._collect_outputs(output_dir)
            matches = [item for item in outputs if item.path == "screenshot.png"]
            if len(matches) != 1 or len(outputs) != 1 or not matches[0].content:
                raise IsolationUnavailable("Browser did not produce the expected screenshot.")
            generated = matches[0].content
            if not generated.startswith(b"\x89PNG\r\n\x1a\n"):
                raise IsolationUnavailable("Browser returned an invalid PNG.")
            if len(generated) > MAX_SCREENSHOT_OUTPUT_BYTES:
                raise SandboxOutputLimitExceeded("Screenshot exceeds the output limit.")
            return ScreenshotJobResult(
                generated,
                hashlib.sha256(html.encode("utf-8")).hexdigest(),
                hashlib.sha256(generated).hexdigest(),
                elapsed,
            )
        finally:
            try:
                shutil.rmtree(task_dir, onerror=_retry_after_chmod)
            except OSError as exc:
                raise IsolationUnavailable(
                    "Screenshot temporary files could not be removed safely."
                ) from exc
