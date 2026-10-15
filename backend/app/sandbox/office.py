"""Fixed-command LibreOffice conversions in disposable isolated containers."""

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

MAX_OFFICE_INPUT_BYTES = 16 * 1024 * 1024
MAX_OFFICE_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_OFFICE_STDIO_BYTES = 64 * 1024
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class OfficeJobResult:
    operation: str
    content: bytes
    input_sha256: str
    output_sha256: str
    runtime_seconds: float


def build_office_create_argv(
    config: SandboxConfig,
    *,
    container_name: str,
    image: str,
    input_dir: Path,
    output_dir: Path,
    operation: str,
    timeout_seconds: int,
) -> list[str]:
    """Construct a fixed, shell-free LibreOffice command with scoped mounts."""
    config.validate_isolation()
    if not re.fullmatch(r"advisor-office-[0-9a-f]{32}", container_name):
        raise ValueError("container name must be generated internally")
    if operation not in {"recalculate_xlsx", "render_docx_pdf", "render_xlsx_pdf"}:
        raise ValueError("unsupported Office operation")
    if not 1 <= timeout_seconds <= config.max_timeout_seconds:
        raise ValueError("timeout exceeds configured Office limit")
    root = config.work_root.resolve(strict=True)
    mounts = []
    for original in (input_dir, output_dir):
        if original.is_symlink():
            raise ValueError("Office bind mount cannot be a symlink")
        directory = original.resolve(strict=True)
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise ValueError("Office bind mount must stay under approved work root") from exc
        current = original
        while current != root and current != current.parent:
            if current.is_symlink():
                raise ValueError("Office bind mount path contains a symlink")
            current = current.parent
        if current != root or "," in str(directory):
            raise ValueError("Office bind mount path is invalid")
        mounts.append(directory)
    input_dir, output_dir = mounts
    cpu = format(config.max_cpu_count, ".3f").rstrip("0").rstrip(".")
    args = [
        "docker", "create", "--name", container_name,
        "--label", "app=emory-stem-advisor-office",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", "65532:65532",
        "--pids-limit", str(config.max_pids), "--cpus", cpu,
        "--memory", _memory_limit(config.max_memory_bytes),
        "--memory-swap", _memory_limit(config.max_memory_bytes),
        "--ulimit", f"cpu={timeout_seconds}:{timeout_seconds}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", f"type=bind,source={str(input_dir).replace(chr(92), '/')},target=/inputs,readonly",
        "--mount", f"type=bind,source={str(output_dir).replace(chr(92), '/')},target=/output",
        "--env", "HOME=/tmp", "--env", "SAL_USE_VCLPLUGIN=svp",
        "--workdir", "/output", _validate_image(image),
        "python3", "/usr/local/bin/office-convert", operation,
    ]
    if config.runtime:
        image_index = args.index(_validate_image(image))
        args[image_index:image_index] = ["--runtime", config.runtime]
    return args


class OfficeRunner:
    """Isolated LibreOffice job runner; never opens workbooks in the web process."""

    def __init__(self, config: SandboxConfig, runtime: ContainerRuntime | None = None):
        self.config = config
        self.runtime = runtime or DockerCliRuntime(create_timeout_seconds=config.create_timeout_seconds)
        # Reuse the audited bounded output walker with a larger but fixed office ceiling.
        import dataclasses

        self._collector = SandboxRunner(
            dataclasses.replace(config, max_output_bytes=MAX_OFFICE_OUTPUT_BYTES)
        )

    def probe_office(self) -> str:
        probe = getattr(self.runtime, "probe", None)
        if probe is None:
            raise IsolationUnavailable("Office execution requires a probed Docker/gVisor runtime.")
        images = probe(self.config)
        image_id = images.get(self.config.office_image)
        if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
            raise IsolationUnavailable("Pinned LibreOffice image is unavailable.")
        return image_id

    def run(
        self,
        operation: str,
        content: bytes,
        *,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OfficeJobResult:
        self.config.validate_isolation()
        if not self.config.production:
            raise IsolationUnavailable(
                "Office conversion requires a separate host with probed gVisor isolation."
            )
        formats = {
            "recalculate_xlsx": ("input.xlsx", "xlsx", b"PK\x03\x04"),
            "render_docx_pdf": ("input.docx", "pdf", b"PK\x03\x04"),
            "render_xlsx_pdf": ("input.xlsx", "pdf", b"PK\x03\x04"),
        }
        if operation not in formats:
            raise ValueError("unsupported Office operation")
        if not isinstance(content, bytes) or not content or len(content) > MAX_OFFICE_INPUT_BYTES:
            raise ValueError("Office input is empty or exceeds the byte limit")
        filename, target_format, magic = formats[operation]
        if not content.startswith(magic):
            raise ValueError("Office input is not a supported OOXML package")
        timeout = (
            min(120, self.config.max_timeout_seconds)
            if timeout_seconds is None
            else timeout_seconds
        )
        if not 1 <= timeout <= self.config.max_timeout_seconds:
            raise ValueError("Office timeout exceeds configured limit")
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Office execution was cancelled.")
        root = self.config.work_root
        if root.is_symlink():
            raise ValueError("Office work root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
        task_dir = Path(tempfile.mkdtemp(prefix="office-", dir=resolved_root))
        try:
            input_dir, output_dir = task_dir / "inputs", task_dir / "outputs"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o777)
            source = input_dir / filename
            source.write_bytes(content)
            source.chmod(0o444)
            input_dir.chmod(0o555)
            name = "advisor-office-" + uuid4().hex
            image = self.config.office_image
            if self.config.production:
                image = self.probe_office()
            argv = build_office_create_argv(
                self.config, container_name=name, image=image, input_dir=input_dir,
                output_dir=output_dir, operation=operation, timeout_seconds=timeout,
            )
            started = time.monotonic()
            result = self.runtime.execute(
                argv, container_name=name, timeout_seconds=timeout,
                output_limit_bytes=MAX_OFFICE_STDIO_BYTES, cancel_event=cancel_event,
            )
            elapsed = time.monotonic() - started
            if result.cancelled:
                raise SandboxCancelled("Office execution was cancelled.")
            if result.timed_out:
                raise SandboxTimedOut("Office conversion exceeded its time limit.")
            if result.output_exceeded:
                raise SandboxOutputLimitExceeded("Office process output exceeded its limit.")
            if result.exit_code != 0:
                raise IsolationUnavailable("Isolated Office conversion failed.")
            outputs = self._collector._collect_outputs(output_dir)
            expected = "input." + target_format
            matches = [item for item in outputs if item.path == expected]
            if len(matches) != 1 or len(outputs) != 1 or not matches[0].content:
                raise IsolationUnavailable("Office converter did not produce the expected output.")
            generated = matches[0].content
            if operation.endswith("pdf") and not generated.startswith(b"%PDF-"):
                raise IsolationUnavailable("Office converter returned an invalid PDF.")
            if operation == "recalculate_xlsx" and not generated.startswith(b"PK\x03\x04"):
                raise IsolationUnavailable("Office converter returned an invalid XLSX package.")
            return OfficeJobResult(
                operation, generated, hashlib.sha256(content).hexdigest(),
                hashlib.sha256(generated).hexdigest(), elapsed,
            )
        finally:
            try:
                shutil.rmtree(task_dir, onerror=_retry_after_chmod)
            except OSError as exc:
                raise IsolationUnavailable("Office temporary files could not be removed safely.") from exc

    def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
        return self.run("recalculate_xlsx", xlsx_bytes, timeout_seconds=timeout_seconds).content

    def render_docx_pdf(self, docx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run("render_docx_pdf", docx_bytes, timeout_seconds=timeout_seconds).content

    def render_xlsx_pdf(self, xlsx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run("render_xlsx_pdf", xlsx_bytes, timeout_seconds=timeout_seconds).content
