"""Docker CLI runner with isolated one-shot container lifecycle."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.sandbox.types import (
    IsolationUnavailable,
    SandboxCancelled,
    SandboxConfig,
    SandboxInputFile,
    SandboxOutputFile,
    SandboxOutputLimitExceeded,
    SandboxRequest,
    SandboxResult,
    SandboxTimedOut,
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


def _check_run_limits(config: SandboxConfig, language: str, timeout_seconds: int) -> None:
    if language not in {"python", "javascript"}:
        raise ValueError("unsupported sandbox language")
    if not 1 <= timeout_seconds <= config.max_timeout_seconds:
        raise ValueError("timeout exceeds configured sandbox limit")


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


def build_create_argv(
    config: SandboxConfig,
    *,
    container_name: str,
    image: str,
    language: str,
    input_dir: Path,
    output_dir: Path,
    timeout_seconds: int,
) -> list[str]:
    """Build an argv-only Docker create command; no user source enters arguments."""
    config.validate_isolation()
    if not re.fullmatch(r"advisor-sbx-[0-9a-f]{32}", container_name):
        raise ValueError("container name must be generated internally")
    _check_run_limits(config, language, timeout_seconds)

    root = config.work_root.resolve(strict=True)
    resolved_mounts = []
    for original in (input_dir, output_dir):
        if original.is_symlink():
            raise ValueError("sandbox bind mount cannot be a symlink")
        directory = original.resolve(strict=True)
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise ValueError("sandbox bind mount must stay under the approved work root") from exc
        current = original
        while current != root and current != current.parent:
            if current.is_symlink():
                raise ValueError("sandbox bind mount path contains a symlink")
            current = current.parent
        if current != root:
            raise ValueError("sandbox bind mount must stay under the approved work root")
        if "," in str(directory):
            raise ValueError("sandbox mount path cannot contain a comma")
        resolved_mounts.append(directory)
    input_dir, output_dir = resolved_mounts

    cpus = format(config.max_cpu_count, ".3f").rstrip("0").rstrip(".")
    args = ["docker"]
    if config.docker_host:
        args.extend(["--host", config.docker_host])
    args.extend(
        [
            "create",
            "--name",
            container_name,
            "--label",
            "app=emory-stem-advisor-sandbox",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "65532:65532",
            "--pids-limit",
            str(config.max_pids),
            "--cpus",
            cpus,
            "--memory",
            _memory_limit(config.max_memory_bytes),
            "--memory-swap",
            _memory_limit(config.max_memory_bytes),
            "--ulimit",
            f"cpu={timeout_seconds}:{timeout_seconds}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,source={str(input_dir).replace(_BACKSLASH, '/')},target=/inputs,readonly",
            "--mount",
            f"type=bind,source={str(output_dir).replace(_BACKSLASH, '/')},target=/output",
        ]
    )
    if config.runtime:
        args.extend(["--runtime", config.runtime])
    args.extend(
        [
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--workdir",
            "/output",
            _validate_image(image),
        ]
    )
    if language == "python":
        args.extend(["python", "-I", "/inputs/main.py"])
    else:
        args.extend(["node", "--disable-proto=throw", "/inputs/main.js"])
    return args


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
        for image in (config.python_image, config.javascript_image, config.office_image):
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


class SandboxRunner:
    def __init__(self, config: SandboxConfig, runtime: ContainerRuntime | None = None):
        self.config = config
        self.runtime = runtime or DockerCliRuntime(
            create_timeout_seconds=config.create_timeout_seconds
        )

    def probe_production(self) -> dict[str, str]:
        probe = getattr(self.runtime, "probe", None)
        if probe is None:
            raise IsolationUnavailable(
                "Production execution requires a probed Docker/gVisor runtime."
            )
        return probe(self.config)

    def run(
        self,
        request: SandboxRequest,
        *,
        cancel_event: threading.Event | None = None,
    ) -> SandboxResult:
        self.config.validate_isolation()
        timeout = request.timeout_seconds or self.config.max_timeout_seconds
        _check_run_limits(self.config, request.language, timeout)
        source_bytes = request.source.encode("utf-8")
        if not source_bytes or len(source_bytes) > self.config.max_input_bytes:
            raise ValueError("sandbox source is empty or exceeds the input byte limit")
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxCancelled("Sandbox execution was cancelled.")

        root = self.config.work_root
        if root.is_symlink():
            raise ValueError("sandbox work root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        approved_root = root.resolve(strict=True)
        task_dir = Path(tempfile.mkdtemp(prefix="job-", dir=approved_root))
        try:
            input_dir = task_dir / "inputs"
            output_dir = task_dir / "outputs"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o777)
            self._write_inputs(input_dir, source_bytes, request.inputs)
            (input_dir / ("main.py" if request.language == "python" else "main.js")).write_bytes(
                source_bytes
            )
            for path in input_dir.iterdir():
                path.chmod(0o444)
            input_dir.chmod(0o555)
            name = "advisor-sbx-" + uuid4().hex
            image = (
                self.config.python_image
                if request.language == "python"
                else self.config.javascript_image
            )
            if self.config.production:
                image = self.probe_production()[image]
            argv = build_create_argv(
                self.config,
                container_name=name,
                image=image,
                language=request.language,
                input_dir=input_dir,
                output_dir=output_dir,
                timeout_seconds=timeout,
            )
            started = time.monotonic()
            result = self.runtime.execute(
                argv,
                container_name=name,
                timeout_seconds=timeout,
                output_limit_bytes=self.config.max_output_bytes,
                cancel_event=cancel_event,
            )
            runtime_seconds = time.monotonic() - started
            if result.cancelled:
                raise SandboxCancelled("Sandbox execution was cancelled.")
            if result.timed_out:
                raise SandboxTimedOut("Sandbox exceeded its wall-clock time limit.")
            if result.output_exceeded:
                raise SandboxOutputLimitExceeded("Sandbox exceeded its output byte limit.")
            files = self._collect_outputs(output_dir)
            total_output = (
                len(result.stdout) + len(result.stderr) + sum(len(file.content) for file in files)
            )
            if total_output > self.config.max_output_bytes:
                raise SandboxOutputLimitExceeded("Sandbox exceeded its combined output byte limit.")
            return SandboxResult(
                language=request.language,
                exit_code=result.exit_code,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
                files=files,
                runtime_seconds=runtime_seconds,
            )
        finally:
            try:
                shutil.rmtree(task_dir, onerror=_retry_after_chmod)
            except OSError:
                # Cleanup must never replace a successful result or the real
                # error with a SandboxError, and must not mask a timeout/cancel.
                pass

    def _write_inputs(
        self, input_dir: Path, source: bytes, inputs: tuple[SandboxInputFile, ...]
    ) -> None:
        total = len(source)
        filenames = set()
        resource_ids = set()
        for item in inputs:
            if not _RESOURCE_RE.fullmatch(item.resource_id):
                raise ValueError("input resource ID is invalid")
            filename = _safe_filename(item.filename)
            if filename in filenames or filename in {"main.py", "main.js"}:
                raise ValueError("input filenames must be unique and cannot replace program code")
            if item.resource_id in resource_ids:
                raise ValueError("input resource IDs must be unique")
            if not isinstance(item.content, bytes):
                raise TypeError("input file content must be bytes")
            total += len(item.content)
            if total > self.config.max_input_bytes:
                raise ValueError("sandbox inputs exceed the configured byte limit")
            filenames.add(filename)
            resource_ids.add(item.resource_id)
            path = input_dir / filename
            if path.resolve().parent != input_dir.resolve():
                raise ValueError("sandbox input escaped the approved mount root")
            path.write_bytes(item.content)

    def _collect_outputs(self, output_dir: Path) -> tuple[SandboxOutputFile, ...]:
        root = output_dir.resolve(strict=True)
        if output_dir.is_symlink():
            raise UnsafeSandboxOutput("Sandbox output directory became a symlink.")
        result = []
        total = 0
        entry_count = 0
        for current, directories, filenames in os.walk(root, followlinks=False):
            entry_count += len(directories) + len(filenames)
            if entry_count > self.config.max_output_files * 2:
                raise SandboxOutputLimitExceeded("Sandbox created too many output entries.")
            current_path = Path(current)
            for name in directories:
                self._check_entry(current_path / name, root)
            for name in filenames:
                candidate = current_path / name
                self._check_entry(candidate, root)
                info = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise UnsafeSandboxOutput("Sandbox output contains a non-regular file.")
                if len(result) >= self.config.max_output_files:
                    raise SandboxOutputLimitExceeded("Sandbox created too many output files.")
                remaining = self.config.max_output_bytes - total
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

    @staticmethod
    def _check_entry(path: Path, root: Path) -> None:
        """Reject symlinks and anything that resolves outside the output root."""
        if path.is_symlink():
            raise UnsafeSandboxOutput("Sandbox created a symlink in its output directory.")
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise UnsafeSandboxOutput("Sandbox output escaped its approved directory.") from exc
