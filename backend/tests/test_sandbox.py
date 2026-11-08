import base64
import hashlib
import io
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.sandbox import (
    DockerCliRuntime,
    IsolationUnavailable,
    SandboxConfig,
    UnsafeSandboxOutput,
    create_worker_app,
)
from app.sandbox.office import MAX_OFFICE_INPUT_BYTES, OfficeRunner
from app.sandbox.runner import RuntimeResult, check_output_entry, collect_outputs


def make_config(tmp_path, **changes):
    values = {
        "work_root": tmp_path / "sandbox-work",
        "max_timeout_seconds": 10,
        "max_output_bytes": 4096,
    }
    values.update(changes)
    return SandboxConfig(**values)


def test_screenshot_argv_uses_the_dedicated_browser_sandbox(tmp_path):
    from app.sandbox.screenshot import build_screenshot_create_argv

    config = make_config(tmp_path, browser_image="emory-sandbox-browser:1.50.0-v1")
    root = config.work_root
    root.mkdir(parents=True, exist_ok=True)
    input_dir, output_dir = root / "inputs", root / "outputs"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    argv = build_screenshot_create_argv(
        config, container_name="advisor-shot-" + "a" * 32,
        image=config.browser_image, input_dir=input_dir, output_dir=output_dir,
        width=1280, height=800, timeout_seconds=10,
    )
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "python3" in argv and "/usr/local/bin/html-screenshot" in argv
    assert str(input_dir.resolve()).replace("\\", "/") in " ".join(argv)
    assert str(output_dir.resolve()).replace("\\", "/") in " ".join(argv)


def test_production_requires_runsc_on_a_separate_host(tmp_path):
    ok = make_config(tmp_path, production=True, runtime="runsc", separate_sandbox_host=True,
                     docker_host=None)
    assert ok.validate_isolation() is None
    bad = make_config(tmp_path, production=True, runtime="runc", separate_sandbox_host=True,
                      docker_host="tcp://sandbox-host.example:2376")
    with pytest.raises(IsolationUnavailable):
        bad.validate_isolation()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory permission semantics")
def test_cleanup_hook_removes_a_read_only_input_tree(tmp_path):
    """EMO-004: a 0o555 input directory blocks unlink even after the file is writable."""
    from app.sandbox.runner import _retry_after_chmod

    task = tmp_path / "task"
    (task / "input").mkdir(parents=True)
    (task / "input" / "file.txt").write_text("x")
    os.chmod(task / "input" / "file.txt", 0o444)
    os.chmod(task / "input", 0o555)
    shutil.rmtree(task, onerror=_retry_after_chmod)
    assert not task.exists()


def test_output_path_escape_is_rejected(tmp_path):
    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(UnsafeSandboxOutput):
        check_output_entry(outside, root)


def test_docker_cli_cancellation_kills_and_removes_container(monkeypatch):
    calls = []
    event = threading.Event()

    class AttachedProcess:
        def __init__(self, argv, **kwargs):
            self.stdout = io.BytesIO(b"partial output")
            self.stderr = io.BytesIO(b"")
            self.returncode = None
            event.set()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.returncode = -9

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "create":
            return SimpleNamespace(returncode=0, stdout=b"a" * 64, stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("app.sandbox.runner.subprocess.run", fake_run)
    monkeypatch.setattr("app.sandbox.runner.subprocess.Popen", AttachedProcess)
    result = DockerCliRuntime().execute(
        ["docker", "create", "--network", "none"],
        container_name="advisor-sbx-" + "b" * 32,
        timeout_seconds=5,
        output_limit_bytes=1024,
        cancel_event=event,
    )
    assert result.cancelled is True
    commands = [argv for argv, _ in calls]
    assert any(argv[1] == "kill" for argv in commands)
    assert any(argv[1:3] == ["rm", "-f"] for argv in commands)
    assert all(kwargs.get("shell") is False for _, kwargs in calls)


def test_output_symlink_escape_is_rejected_when_host_supports_symlinks(tmp_path):
    work = tmp_path / "work"
    output = work / "out"
    outside = tmp_path / "outside.txt"
    output.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    link = output / "leak.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        with pytest.raises(UnsafeSandboxOutput):
            check_output_entry(outside, output)
    else:
        with pytest.raises(UnsafeSandboxOutput):
            collect_outputs(output, max_files=8, max_bytes=1024)


def zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


class FakeContainerRuntime:
    def __init__(
        self,
        output: bytes | None = None,
        *,
        exit_code: int = 0,
        timed_out: bool = False,
        cancelled: bool = False,
        output_exceeded: bool = False,
    ):
        self.output = output
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.cancelled = cancelled
        self.output_exceeded = output_exceeded
        self.argv: list[str] | None = None
        self.called = False

    def probe(self, cfg: SandboxConfig) -> dict[str, str]:
        return {cfg.office_image: "sha256:" + "a" * 64}

    def execute(
        self,
        argv: list[str],
        *,
        container_name: str,
        timeout_seconds: int,
        output_limit_bytes: int,
        cancel_event: threading.Event | None,
    ) -> RuntimeResult:
        self.argv = argv
        self.called = True
        if self.output is not None:
            out_mount = next(a for a in argv if "target=/output" in a)
            out_dir = Path(out_mount.split("source=", 1)[1].split(",target=", 1)[0])
            (out_dir / "input.xlsx").write_bytes(self.output)
        return RuntimeResult(
            self.exit_code,
            b"",
            b"",
            timed_out=self.timed_out,
            cancelled=self.cancelled,
            output_exceeded=self.output_exceeded,
        )


@pytest.mark.parametrize(
    "limit_name, runtime_kwargs, request_kwargs, expected_status, verify_argv",
    [
        (
            "bad_bearer_token",
            {},
            {"auth_header": "Bearer " + "wrong" * 8},
            401,
            None,
        ),
        (
            "input_bytes",
            {},
            {"oversized_input": True},
            413,
            None,
        ),
        (
            "output_bytes",
            {"output_exceeded": True},
            {},
            503,
            None,
        ),
        (
            "timeout",
            {"timed_out": True},
            {},
            408,
            lambda argv: "--ulimit" in argv and "cpu=10:10" in argv[argv.index("--ulimit") + 1],
        ),
        (
            "pids",
            {"exit_code": 137},
            {},
            503,
            lambda argv: "--pids-limit" in argv and argv[argv.index("--pids-limit") + 1] == "64",
        ),
        (
            "memory",
            {"exit_code": 137},
            {},
            503,
            lambda argv: "--memory" in argv and argv[argv.index("--memory") + 1] == "512m",
        ),
    ],
)
def test_sandbox_request_limits_contract(
    tmp_path, limit_name, runtime_kwargs, request_kwargs, expected_status, verify_argv
):
    cfg = SandboxConfig(
        work_root=tmp_path / "sandbox-work",
        production=True,
        separate_sandbox_host=True,
        runtime="runsc",
        max_pids=64,
        max_memory_bytes=512 * 1024 * 1024,
        max_timeout_seconds=10,
    )
    token = "s" * 40
    runtime = FakeContainerRuntime(**runtime_kwargs)
    runner = OfficeRunner(cfg, runtime)
    app = create_worker_app(cfg, token, office_runner=runner)
    client = TestClient(app)

    raw = zip_bytes()
    headers = {"Authorization": request_kwargs.get("auth_header", f"Bearer {token}")}

    if request_kwargs.get("oversized_input"):
        res = client.post(
            "/v1/office/execute",
            headers={**headers, "content-length": str((MAX_OFFICE_INPUT_BYTES * 4 // 3) + 5000)},
            content=b"x" * 1024,
        )
        assert res.status_code == expected_status
        with pytest.raises(ValueError, match="byte limit"):
            runner.run("recalculate_xlsx", b"PK" + b"x" * (MAX_OFFICE_INPUT_BYTES + 1))
        with pytest.raises(ValueError):
            SandboxConfig(work_root=tmp_path / "w", max_input_bytes=-1)
        return

    body = {
        "request_id": f"req-{limit_name}",
        "operation": "recalculate_xlsx",
        "input_base64": base64.b64encode(raw).decode("ascii"),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "timeout_seconds": 10,
    }
    res = client.post("/v1/office/execute", headers=headers, json=body)
    assert res.status_code == expected_status

    if verify_argv:
        assert runtime.called
        assert verify_argv(runtime.argv)

    if limit_name == "bad_bearer_token":
        with pytest.raises(ValueError):
            create_worker_app(cfg, "too_short")
    elif limit_name == "output_bytes":
        with pytest.raises(ValueError):
            SandboxConfig(work_root=tmp_path / "w", max_output_bytes=500)
    elif limit_name == "timeout":
        with pytest.raises(ValueError):
            SandboxConfig(work_root=tmp_path / "w", max_timeout_seconds=0)
    elif limit_name == "pids":
        with pytest.raises(ValueError):
            SandboxConfig(work_root=tmp_path / "w", max_pids=0)
    elif limit_name == "memory":
        with pytest.raises(ValueError):
            SandboxConfig(work_root=tmp_path / "w", max_memory_bytes=1024)

