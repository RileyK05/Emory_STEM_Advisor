"""One fixed command in a disposable isolated container: the shared job lifecycle.

Office conversions and HTML screenshots are the same job with different inputs:
stage one read-only input file, run a fixed shell-free command with no network
and no capabilities, then collect exactly one output file of a known kind. Each
runner describes its job as a `ContainerJob`; this module owns the mounts, the
`docker create` flags, the failure mapping and the cleanup, so the isolation
rules exist in one place.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.sandbox.runner import (
    ContainerRuntime,
    _memory_limit,
    _retry_after_chmod,
    _validate_image,
    collect_outputs,
)
from app.sandbox.types import (
    IsolationUnavailable,
    SandboxCancelled,
    SandboxConfig,
    SandboxOutputLimitExceeded,
    SandboxTimedOut,
)

MAX_JOB_STDIO_BYTES = 64 * 1024
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ContainerJob:
    """What one job runs, what it is given, and the single file it must produce."""

    kind: str  # "office" | "shot": container name prefix and label
    what: str  # "Office conversion": the subject of user-facing messages
    command: tuple[str, ...]
    input_name: str
    output_name: str
    output_label: str  # "PDF", "XLSX package", "PNG"
    output_magic: bytes
    max_output_bytes: int
    env: tuple[str, ...] = ("HOME=/tmp",)


@dataclass(frozen=True)
class JobResult:
    content: bytes
    input_sha256: str
    output_sha256: str
    runtime_seconds: float


def container_name(job: ContainerJob) -> str:
    return f"advisor-{job.kind}-{uuid4().hex}"


def _bind_mounts(config: SandboxConfig, *dirs: Path) -> list[Path]:
    """Resolve job directories, which must be real directories under the work root."""
    root = config.work_root.resolve(strict=True)
    mounts = []
    for original in dirs:
        if original.is_symlink():
            raise ValueError("sandbox bind mount cannot be a symlink")
        directory = original.resolve(strict=True)
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise ValueError("sandbox bind mount must stay under approved work root") from exc
        current = original
        while current != root and current != current.parent:
            if current.is_symlink():
                raise ValueError("sandbox bind mount path contains a symlink")
            current = current.parent
        if current != root or "," in str(directory):
            raise ValueError("sandbox bind mount path is invalid")
        mounts.append(directory)
    return mounts


def build_create_argv(
    config: SandboxConfig,
    job: ContainerJob,
    *,
    name: str,
    image: str,
    input_dir: Path,
    output_dir: Path,
    timeout_seconds: int,
) -> list[str]:
    """A fixed, shell-free `docker create` with no network and scoped mounts."""
    config.validate_isolation()
    if not re.fullmatch(rf"advisor-{job.kind}-[0-9a-f]{{32}}", name):
        raise ValueError("container name must be generated internally")
    if not 1 <= timeout_seconds <= config.max_timeout_seconds:
        raise ValueError(f"timeout exceeds configured {job.what} limit")
    input_dir, output_dir = _bind_mounts(config, input_dir, output_dir)
    image = _validate_image(image)
    cpu = format(config.max_cpu_count, ".3f").rstrip("0").rstrip(".")
    memory = _memory_limit(config.max_memory_bytes)
    env = [arg for variable in job.env for arg in ("--env", variable)]
    runtime = ["--runtime", config.runtime] if config.runtime else []
    return [
        "docker", "create", "--name", name,
        "--label", f"app=emory-stem-advisor-{job.kind}",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", "65532:65532",
        "--pids-limit", str(config.max_pids), "--cpus", cpu,
        "--memory", memory, "--memory-swap", memory,
        "--ulimit", f"cpu={timeout_seconds}:{timeout_seconds}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", f"type=bind,source={input_dir.as_posix()},target=/inputs,readonly",
        "--mount", f"type=bind,source={output_dir.as_posix()},target=/output",
        *env, "--workdir", "/output", *runtime, image, *job.command,
    ]


def probe_image(runtime: ContainerRuntime, config: SandboxConfig, image: str, missing: str) -> str:
    """The immutable ID of a configured image, from a probed Docker/gVisor runtime."""
    probe = getattr(runtime, "probe", None)
    if probe is None:
        raise IsolationUnavailable("Sandbox execution requires a probed Docker/gVisor runtime.")
    image_id = probe(config).get(image)
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise IsolationUnavailable(missing)
    return image_id


def run_container_job(
    config: SandboxConfig,
    runtime: ContainerRuntime,
    job: ContainerJob,
    content: bytes,
    *,
    resolve_image: Callable[[], str],
    timeout_seconds: int,
    cancel_event: threading.Event | None,
) -> JobResult:
    """Run `job` over `content` once, in a fresh container, and remove every trace of it.

    The image is resolved (probed, in production) only after the job's directory
    exists, so a failed probe still leaves the work root clean.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise SandboxCancelled(f"{job.what} was cancelled.")
    root = config.work_root
    if root.is_symlink():
        raise ValueError("sandbox work root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    task_dir = Path(tempfile.mkdtemp(prefix=f"{job.kind}-", dir=root.resolve(strict=True)))
    try:
        input_dir, output_dir = task_dir / "inputs", task_dir / "outputs"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o777)
        source = input_dir / job.input_name
        source.write_bytes(content)
        source.chmod(0o444)
        input_dir.chmod(0o555)
        name = container_name(job)
        argv = build_create_argv(
            config, job, name=name, image=resolve_image(), input_dir=input_dir,
            output_dir=output_dir, timeout_seconds=timeout_seconds,
        )
        started = time.monotonic()
        result = runtime.execute(
            argv, container_name=name, timeout_seconds=timeout_seconds,
            output_limit_bytes=MAX_JOB_STDIO_BYTES, cancel_event=cancel_event,
        )
        elapsed = time.monotonic() - started
        if result.cancelled:
            raise SandboxCancelled(f"{job.what} was cancelled.")
        if result.timed_out:
            raise SandboxTimedOut(f"{job.what} exceeded its time limit.")
        if result.output_exceeded:
            raise SandboxOutputLimitExceeded(f"{job.what} process output exceeded its limit.")
        if result.exit_code != 0:
            raise IsolationUnavailable(f"{job.what} failed in its isolated container.")
        outputs = collect_outputs(output_dir, max_files=config.max_output_files,
                                  max_bytes=job.max_output_bytes)
        if len(outputs) != 1 or outputs[0].path != job.output_name or not outputs[0].content:
            raise IsolationUnavailable(f"{job.what} did not produce the expected output.")
        generated = outputs[0].content
        if not generated.startswith(job.output_magic):
            raise IsolationUnavailable(f"{job.what} returned an invalid {job.output_label}.")
        return JobResult(
            generated, hashlib.sha256(content).hexdigest(),
            hashlib.sha256(generated).hexdigest(), elapsed,
        )
    finally:
        try:
            shutil.rmtree(task_dir, onerror=_retry_after_chmod)
        except OSError as exc:
            raise IsolationUnavailable("Sandbox temporary files could not be removed safely.") from exc
