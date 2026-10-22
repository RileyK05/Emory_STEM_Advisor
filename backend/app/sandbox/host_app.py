"""Worker process entrypoint; only deployed on dedicated sandbox host."""

import os
from pathlib import Path

from fastapi import FastAPI

from app.sandbox.types import SandboxConfig
from app.sandbox.worker import create_worker_app

_secret = os.environ.get("SANDBOX_WORKER_BEARER_TOKEN", "")
if not _secret:
    raise RuntimeError("SANDBOX_WORKER_BEARER_TOKEN is required")
_config = SandboxConfig(
    work_root=Path(os.environ.get("SANDBOX_WORK_ROOT", "/var/lib/emory-sandbox/jobs")),
    python_image=os.environ.get("SANDBOX_PYTHON_IMAGE", "emory-sandbox-python:3.12-v1"),
    javascript_image=os.environ.get("SANDBOX_JAVASCRIPT_IMAGE", "emory-sandbox-javascript:22-v1"),
    office_image=os.environ.get("SANDBOX_OFFICE_IMAGE", "emory-sandbox-office:7.4.7-v1"),
    browser_image=os.environ.get("SANDBOX_SCREENSHOT_IMAGE", ""),
    runtime="runsc",
    production=True,
    separate_sandbox_host=os.environ.get("SANDBOX_SEPARATE_HOST_ATTESTED", "false").lower() == "true",
    max_timeout_seconds=int(os.environ.get("SANDBOX_MAX_TIMEOUT_SECONDS", "120")),
    max_cpu_count=float(os.environ.get("SANDBOX_MAX_CPU_COUNT", "1")),
    max_memory_bytes=int(os.environ.get("SANDBOX_MAX_MEMORY_BYTES", str(512 * 1024 * 1024))),
    max_output_bytes=int(os.environ.get("SANDBOX_MAX_OUTPUT_BYTES", str(4 * 1024 * 1024))),
    max_input_bytes=int(os.environ.get("SANDBOX_MAX_INPUT_BYTES", str(16 * 1024 * 1024))),
    max_output_files=int(os.environ.get("SANDBOX_MAX_OUTPUT_FILES", "100")),
    max_pids=int(os.environ.get("SANDBOX_MAX_PIDS", "64")),
)
app: FastAPI = create_worker_app(_config, _secret)
