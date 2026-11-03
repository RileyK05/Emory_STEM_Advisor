"""Docker CLI runner with isolated one-shot container lifecycle."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.sandbox.types import (
    IsolationUnavailable,
    SandboxConfig,
    SandboxOutputFile,
    SandboxOutputLimitExceeded,
    UnsafeSandboxOutput,
)

_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
_BACKSLASH = "\\"
# Windows reserves these device names (optionally with any extension) and
# normalizes trailing dots/spaces, so distinct inputs could alias the same path.
_RESERVED_DEVICE_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$")


def _reserved_device(value: str) -> bool:
    return bool(_RESERVED_DEVICE_RE.fullmatch(value.split(".", 1)[0].upper()))


@dataclass(frozen=True)
class RuntimeResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    cancelled: bool = False
    output_exceeded: bool = False


class ContainerRuntime(Protocol):
    def execute(
        self,
        create_argv: list[str],
        *,
        container_name: str,
        timeout_seconds: int,
        output_limit_bytes: int,
        cancel_event: threading.Event | None,
    ) -> RuntimeResult: ...


def _safe_filename(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or not _FILENAME_RE.fullmatch(value)
        or "/" in value
        or _BACKSLASH in value
        or ":" in value
        or value.endswith((".", " "))
        or _reserved_device(value)
    ):
        raise ValueError("input filename must be a safe basename")
    return value


def _safe_output_component(value: str) -> bool:
    forbidden = set('<>:"|?*') | {_BACKSLASH}
    if (
        not value
        or value in {".", ".."}
        or value.endswith((".", " "))
        or any(ord(char) < 32 or char in forbidden for char in value)
    ):
        return False
    return not _reserved_device(value)


def _memory_limit(value: int) -> str:
    mib = (value + 1024 * 1024 - 1) // (1024 * 1024)
    return f"{mib}m"


def _retry_after_chmod(function, path, _exc_info) -> None:
    """rmtree error hook: the sandbox leaves read-only inputs behind.

    Making the file writable is not enough when its parent directory is 0o555 —
    unlinking still needs write+execute on the directory (EMO-004).
    """
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    parent = os.path.dirname(path)
    if parent:
        try:
            os.chmod(parent, stat.S_IRWXU)
        except OSError:
            pass
    function(path)


def _validate_image(value: str) -> str:
    if not _IMAGE_RE.fullmatch(value):
        raise ValueError("sandbox image must be a fixed image reference")
    return value


class DockerCliRuntime:
    """Run create/start/kill/rm using subprocess argv with shell=False."""

    def __init__(self, executable: str = "docker", *, create_timeout_seconds: int = 15):
        if not 1 <= create_timeout_seconds <= 60:
            raise ValueError("create_timeout_seconds must be between 1 and 60")
        self.executable = executable
        self.create_timeout_seconds = create_timeout_seconds

    def probe(self, config: SandboxConfig) -> dict[str, str]:
        """Probe daemon runtime and resolve configured images to immutable IDs."""

        def call(args: list[str]) -> str:
            try:
                result = subprocess.run(
                    [self.executable, *args],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=self.create_timeout_seconds,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise IsolationUnavailable("Sandbox Docker daemon is unavailable.") from exc
            if result.returncode != 0:
                raise IsolationUnavailable("Sandbox Docker daemon probe failed.")
            return result.stdout.decode("utf-8", errors="replace").strip()

        try:
            runtimes = json.loads(call(["info", "--format", "{{json .Runtimes}}"]))
        except (ValueError, TypeError) as exc:
            raise IsolationUnavailable("Sandbox Docker runtime information is invalid.") from exc
        if not isinstance(runtimes, dict) or "runsc" not in runtimes:
            raise IsolationUnavailable("gVisor runsc is not registered with the sandbox daemon.")
        resolved = {}
        images = [config.office_image]
        if config.browser_image:
            images.append(config.browser_image)
        for image in images:
            image_id = call(["image", "inspect", "--format", "{{.Id}}", image])
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise IsolationUnavailable(
                    "A configured sandbox image is missing or has an invalid ID."
                )
            resolved[image] = image_id
        return resolved

    def execute(
        self,
        create_argv: list[str],
        *,
        container_name: str,
        timeout_seconds: int,
        output_limit_bytes: int,
        cancel_event: threading.Event | None,
    ) -> RuntimeResult:
        command = [self.executable, *create_argv[1:]]
        try:
            created = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=self.create_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsolationUnavailable("Docker sandbox runtime is unavailable.") from exc
        if created.returncode != 0:
            raise IsolationUnavailable("Docker refused to create the isolated sandbox.")
        container_id = created.stdout.decode("ascii", errors="ignore").strip()
        if not _CONTAINER_ID_RE.fullmatch(container_id):
            raise IsolationUnavailable("Docker returned an invalid sandbox container ID.")

        started = time.monotonic()
        command_process = None
        capture = _BoundedCapture(output_limit_bytes)
        timed_out = False
        cancelled = False
        exceeded = False
        try:
            if cancel_event is not None and cancel_event.is_set():
                self._kill(container_name)
                return RuntimeResult(0, b"", b"", cancelled=True)
            try:
                command_process = subprocess.Popen(
                    [
                        self.executable,
                        "start",
                        "--attach",
                        "--sig-proxy=false",
                        container_id,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                )
            except OSError as exc:
                raise IsolationUnavailable(
                    "Docker sandbox runtime could not start the container."
                ) from exc
            readers = [
                threading.Thread(
                    target=capture.read, args=(command_process.stdout, False), daemon=True
                ),
                threading.Thread(
                    target=capture.read, args=(command_process.stderr, True), daemon=True
                ),
            ]
            for reader in readers:
                reader.start()
            deadline = started + timeout_seconds
            while command_process.poll() is None:
                if capture.exceeded.is_set():
                    exceeded = True
                    self._kill(container_name)
                    break
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    self._kill(container_name)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._kill(container_name)
                    break
                time.sleep(0.05)
            try:
                command_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                command_process.kill()
                command_process.wait(timeout=2)
            for reader in readers:
                reader.join(timeout=2)
            exceeded = exceeded or capture.exceeded.is_set()
            return RuntimeResult(
                exit_code=command_process.returncode or 0,
                stdout=bytes(capture.stdout),
                stderr=bytes(capture.stderr),
                timed_out=timed_out,
                cancelled=cancelled,
                output_exceeded=exceeded,
            )
        finally:
            if command_process is not None and command_process.poll() is None:
                self._kill(container_name)
                command_process.kill()
                command_process.wait(timeout=2)
            self._remove(container_name)

    def _kill(self, container_name: str) -> None:
        self._run_quiet(["kill", container_name], timeout=10)

    def _remove(self, container_name: str) -> None:
        self._run_quiet(["rm", "-f", container_name], timeout=15)

    def _run_quiet(self, args: list[str], *, timeout: int) -> None:
        """Best-effort container housekeeping; failures are deliberately ignored."""
        try:
            subprocess.run(
                [self.executable, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


class _BoundedCapture:
    def __init__(self, limit: int):
        self.limit = limit
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.total = 0
        self.lock = threading.Lock()
        self.exceeded = threading.Event()

    def read(self, stream, is_stderr: bool) -> None:
        destination = self.stderr if is_stderr else self.stdout
        while True:
            block = stream.read(8192)
            if not block:
                return
            with self.lock:
                remaining = self.limit - self.total
                if len(block) > remaining:
                    destination.extend(block[: max(0, remaining)])
                    self.total = self.limit
                    self.exceeded.set()
                    return
                destination.extend(block)
                self.total += len(block)


def collect_outputs(
    output_dir: Path, *, max_files: int, max_bytes: int
) -> tuple[SandboxOutputFile, ...]:
    """Read a finished container's output directory within fixed limits."""
    root = output_dir.resolve(strict=True)
    if output_dir.is_symlink():
        raise UnsafeSandboxOutput("Sandbox output directory became a symlink.")
    result = []
    total = 0
    entry_count = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        entry_count += len(directories) + len(filenames)
        if entry_count > max_files * 2:
            raise SandboxOutputLimitExceeded("Sandbox created too many output entries.")
        current_path = Path(current)
        for name in directories:
            check_output_entry(current_path / name, root)
        for name in filenames:
            candidate = current_path / name
            check_output_entry(candidate, root)
            info = candidate.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafeSandboxOutput("Sandbox output contains a non-regular file.")
            if len(result) >= max_files:
                raise SandboxOutputLimitExceeded("Sandbox created too many output files.")
            remaining = max_bytes - total
            with candidate.open("rb") as stream:
                data = stream.read(remaining + 1)
            if len(data) > remaining:
                raise SandboxOutputLimitExceeded(
                    "Sandbox output files exceeded the byte limit."
                )
            total += len(data)
            relative = candidate.relative_to(root).as_posix()
            parts = relative.split("/")
            if (
                len(relative) > 1024
                or len(parts) > 8
                or any(not _safe_output_component(part) for part in parts)
            ):
                raise UnsafeSandboxOutput("Sandbox output path is invalid.")
            result.append(SandboxOutputFile(path=relative, content=data))
    return tuple(result)


def check_output_entry(path: Path, root: Path) -> None:
    """Reject symlinks and anything that resolves outside the output root."""
    if path.is_symlink():
        raise UnsafeSandboxOutput("Sandbox created a symlink in its output directory.")
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise UnsafeSandboxOutput("Sandbox output escaped its approved directory.") from exc
