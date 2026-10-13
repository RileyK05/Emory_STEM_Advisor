"""Secure sandbox request and result values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class SandboxError(RuntimeError):
    """Base for safe, user-facing sandbox errors."""


class IsolationUnavailable(SandboxError):
    """Required container or production isolation is missing."""


class SandboxTimedOut(SandboxError):
    """The execution exceeded its wall-clock deadline."""


class SandboxCancelled(SandboxError):
    """The execution was cancelled and its container was killed."""


class SandboxOutputLimitExceeded(SandboxError):
    """Output exceeded configured byte or file limits."""


class UnsafeSandboxOutput(SandboxError):
    """The container produced a path or file type that cannot be collected safely."""


@dataclass(frozen=True)
class SandboxConfig:
    work_root: Path
    python_image: str = "emory-sandbox-python:3.12-v1"
    javascript_image: str = "emory-sandbox-javascript:22-v1"
    office_image: str = "emory-sandbox-office:7.4.7-v1"
    runtime: str | None = None
    docker_host: str | None = None
    production: bool = False
    separate_sandbox_host: bool = False
    max_timeout_seconds: int = 120
    max_cpu_count: float = 1.0
    max_memory_bytes: int = 512 * 1024 * 1024
    max_output_bytes: int = 4 * 1024 * 1024
    max_input_bytes: int = 16 * 1024 * 1024
    max_output_files: int = 100
    max_pids: int = 64
    create_timeout_seconds: int = 15

    def __post_init__(self) -> None:
        if not self.work_root.is_absolute():
            raise ValueError("sandbox work_root must be absolute")
        if not 1 <= self.max_timeout_seconds <= 600:
            raise ValueError("max_timeout_seconds must be between 1 and 600")
        if not 0.1 <= self.max_cpu_count <= 8:
            raise ValueError("max_cpu_count must be between 0.1 and 8")
        if not 64 * 1024 * 1024 <= self.max_memory_bytes <= 8 * 1024 * 1024 * 1024:
            raise ValueError("max_memory_bytes must be between 64 MiB and 8 GiB")
        if not 1024 <= self.max_output_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_output_bytes must be between 1 KiB and 64 MiB")
        if not 0 <= self.max_input_bytes <= 256 * 1024 * 1024:
            raise ValueError("max_input_bytes must be between 0 and 256 MiB")
        if not 1 <= self.max_output_files <= 1000:
            raise ValueError("max_output_files must be between 1 and 1000")
        if not 1 <= self.max_pids <= 512:
            raise ValueError("max_pids must be between 1 and 512")
        if not 1 <= self.create_timeout_seconds <= 60:
            raise ValueError("create_timeout_seconds must be between 1 and 60")
        for image in (self.python_image, self.javascript_image, self.office_image):
            if (
                not image
                or image.startswith("-")
                or any(ch in image for ch in "\n\r,;")
                or image.endswith(":latest")
            ):
                raise ValueError("sandbox image names must be fixed, valid image references")
        if self.runtime is not None and (
            not self.runtime
            or self.runtime.startswith("-")
            or any(ch in self.runtime for ch in "\n\r,;")
        ):
            raise ValueError("container runtime name is invalid")

    def validate_isolation(self) -> None:
        if not self.production:
            return
        if not self.separate_sandbox_host or self.runtime != "runsc" or self.docker_host:
            raise IsolationUnavailable(
                "Production sandbox worker requires a separately deployed host, local Docker socket, and gVisor (runsc)."
            )


@dataclass(frozen=True)
class SandboxInputFile:
    resource_id: str
    filename: str
    content: bytes


@dataclass(frozen=True)
class SandboxRequest:
    language: Literal["python", "javascript"]
    source: str
    inputs: tuple[SandboxInputFile, ...] = ()
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class SandboxOutputFile:
    path: str
    content: bytes


@dataclass(frozen=True)
class SandboxResult:
    language: str
    exit_code: int
    stdout: str
    stderr: str
    files: tuple[SandboxOutputFile, ...]
    runtime_seconds: float
