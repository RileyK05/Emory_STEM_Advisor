"""Fixed-command LibreOffice conversions in disposable isolated containers."""

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

MAX_OFFICE_INPUT_BYTES = 16 * 1024 * 1024
MAX_OFFICE_OUTPUT_BYTES = 32 * 1024 * 1024
_OOXML = b"PK\x03\x04"
# operation -> (input file, output file, output label, output magic)
_OPERATIONS = {
    "recalculate_xlsx": ("input.xlsx", "input.xlsx", "XLSX package", _OOXML),
    "render_docx_pdf": ("input.docx", "input.pdf", "PDF", b"%PDF-"),
    "render_xlsx_pdf": ("input.xlsx", "input.pdf", "PDF", b"%PDF-"),
}


def office_job(operation: str) -> ContainerJob:
    if operation not in _OPERATIONS:
        raise ValueError("unsupported Office operation")
    input_name, output_name, output_label, magic = _OPERATIONS[operation]
    return ContainerJob(
        kind="office", what="Office conversion",
        command=("python3", "/usr/local/bin/office-convert", operation),
        input_name=input_name, output_name=output_name,
        output_label=output_label, output_magic=magic,
        max_output_bytes=MAX_OFFICE_OUTPUT_BYTES,
        env=("HOME=/tmp", "SAL_USE_VCLPLUGIN=svp"),
    )


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
    """The `docker create` argv for one Office operation."""
    return build_create_argv(
        config, office_job(operation), name=container_name, image=image,
        input_dir=input_dir, output_dir=output_dir, timeout_seconds=timeout_seconds,
    )


class OfficeRunner:
    """Isolated LibreOffice job runner; never opens workbooks in the web process."""

    def __init__(self, config: SandboxConfig, runtime: ContainerRuntime | None = None):
        self.config = config
        self.runtime = runtime or DockerCliRuntime(create_timeout_seconds=config.create_timeout_seconds)

    def probe_office(self) -> str:
        return probe_image(self.runtime, self.config, self.config.office_image,
                           "Pinned LibreOffice image is unavailable.")

    def run(
        self,
        operation: str,
        content: bytes,
        *,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> JobResult:
        self.config.validate_isolation()
        if not self.config.production:
            raise IsolationUnavailable(
                "Office conversion requires a separate host with probed gVisor isolation."
            )
        job = office_job(operation)
        if not isinstance(content, bytes) or not content or len(content) > MAX_OFFICE_INPUT_BYTES:
            raise ValueError("Office input is empty or exceeds the byte limit")
        if not content.startswith(_OOXML):
            raise ValueError("Office input is not a supported OOXML package")
        timeout = (
            min(120, self.config.max_timeout_seconds)
            if timeout_seconds is None
            else timeout_seconds
        )
        if not 1 <= timeout <= self.config.max_timeout_seconds:
            raise ValueError("Office timeout exceeds configured limit")
        return run_container_job(
            self.config, self.runtime, job, content, resolve_image=self.probe_office,
            timeout_seconds=timeout, cancel_event=cancel_event,
        )

    def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
        return self.run("recalculate_xlsx", xlsx_bytes, timeout_seconds=timeout_seconds).content

    def render_docx_pdf(self, docx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run("render_docx_pdf", docx_bytes, timeout_seconds=timeout_seconds).content

    def render_xlsx_pdf(self, xlsx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes:
        return self.run("render_xlsx_pdf", xlsx_bytes, timeout_seconds=timeout_seconds).content
