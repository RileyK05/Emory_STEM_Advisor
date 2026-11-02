import io
import os
import shutil
import sys
import threading
from types import SimpleNamespace

import pytest

from app.sandbox import (
    DockerCliRuntime,
    IsolationUnavailable,
    SandboxConfig,
    SandboxRunner,
    UnsafeSandboxOutput,
)


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
        SandboxRunner._check_entry(outside, root)


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
            SandboxRunner._check_entry(outside, output)
    else:
        with pytest.raises(UnsafeSandboxOutput):
            SandboxRunner(make_config(tmp_path))._collect_outputs(output)
