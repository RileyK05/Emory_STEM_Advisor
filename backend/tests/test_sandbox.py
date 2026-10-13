import io
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.sandbox import (
    DockerCliRuntime,
    IsolationUnavailable,
    RuntimeResult,
    SandboxCancelled,
    SandboxConfig,
    SandboxInputFile,
    SandboxOutputLimitExceeded,
    SandboxRequest,
    SandboxRunner,
    UnsafeSandboxOutput,
)


class RecordingRuntime:
    def __init__(self, *, cancelled=False, output_exceeded=False):
        self.called = False
        self.argv = None
        self.cancelled = cancelled
        self.output_exceeded = output_exceeded

    def execute(
        self,
        create_argv,
        *,
        container_name,
        timeout_seconds,
        output_limit_bytes,
        cancel_event,
    ):
        self.called = True
        self.argv = create_argv
        input_mount = next(arg for arg in create_argv if "target=/inputs" in arg)
        output_mount = next(arg for arg in create_argv if "target=/output" in arg)
        input_dir = input_mount.split("source=", 1)[1].split(",target=", 1)[0]
        output_dir = output_mount.split("source=", 1)[1].split(",target=", 1)[0]
        program = "main.py" if any(arg.endswith("main.py") for arg in create_argv) else "main.js"
        assert Path(input_dir, program).read_text(encoding="utf-8")
        with Path(output_dir, "answer.txt").open("w", encoding="utf-8") as stream:
            stream.write("done")
        return RuntimeResult(
            exit_code=0,
            stdout=b"ok",
            stderr=b"",
            cancelled=self.cancelled,
            output_exceeded=self.output_exceeded,
        )


def make_config(tmp_path, **changes):
    values = {
        "work_root": tmp_path / "sandbox-work",
        "max_timeout_seconds": 10,
        "max_output_bytes": 4096,
    }
    values.update(changes)
    return SandboxConfig(**values)


def test_sandbox_argv_has_no_network_credentials_or_shell_and_source_is_file_input(tmp_path):
    runtime = RecordingRuntime()
    config = make_config(tmp_path, max_cpu_count=0.25, max_memory_bytes=128 * 1024 * 1024)
    runner = SandboxRunner(config, runtime)
    request = SandboxRequest(
        language="python",
        source="import os; open('/etc/passwd').read()",
        inputs=(SandboxInputFile("source-1", "syllabus.txt", b"syllabus"),),
        timeout_seconds=2,
    )
    result = runner.run(request)
    assert result.stdout == "ok"
    assert [(item.path, item.content) for item in result.files] == [("answer.txt", b"done")]
    argv = runtime.argv
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in argv and "no-new-privileges:true" in argv
    assert "--memory-swap" in argv
    assert "--pids-limit" in argv
    assert "--env" in argv and argv[argv.index("--env") + 1] == "PYTHONDONTWRITEBYTECODE=1"
    assert all("secret" not in arg.lower() for arg in argv)
    assert not any("import os; open" in arg for arg in argv)
    assert "--mount" in argv
    # Limits come from the operator's config; the program cannot request more.
    assert argv[argv.index("--cpus") + 1] == "0.25"
    assert argv[argv.index("--memory") + 1] == "128m"
    assert list(config.work_root.iterdir()) == []


def test_production_requires_runsc_on_a_separate_host(tmp_path):
    ok = make_config(tmp_path, production=True, runtime="runsc", separate_sandbox_host=True,
                     docker_host=None)
    assert ok.validate_isolation() is None
    bad = make_config(tmp_path, production=True, runtime="runc", separate_sandbox_host=True,
                      docker_host="tcp://sandbox-host.example:2376")
    with pytest.raises(IsolationUnavailable):
        bad.validate_isolation()


def test_production_fails_closed_without_separate_gvisor_host(tmp_path):
    runtime = RecordingRuntime()
    config = make_config(
        tmp_path, production=True, runtime=None, separate_sandbox_host=False, docker_host=None
    )
    runner = SandboxRunner(config, runtime)
    with pytest.raises(IsolationUnavailable, match="gVisor"):
        runner.run(SandboxRequest(language="javascript", source="console.log(1)"))
    assert runtime.called is False


@pytest.mark.parametrize("filename", ["../secret", "..", "a/b", "a,b", chr(92) + "Windows" + chr(92) + "system.ini"])
def test_input_filename_path_traversal_and_option_injection_are_rejected(tmp_path, filename):
    runner = SandboxRunner(make_config(tmp_path), RecordingRuntime())
    with pytest.raises(ValueError):
        runner.run(
            SandboxRequest(
                language="python",
                source="print(1)",
                inputs=(SandboxInputFile("source-1", filename, b"x"),),
            )
        )


def test_unscoped_or_invalid_resource_names_are_rejected_before_runtime(tmp_path):
    runtime = RecordingRuntime()
    runner = SandboxRunner(make_config(tmp_path), runtime)
    with pytest.raises(ValueError, match="resource ID"):
        runner.run(
            SandboxRequest(
                language="python",
                source="print(1)",
                inputs=(SandboxInputFile("../../secrets", "input.txt", b"x"),),
            )
        )
    assert runtime.called is False


@pytest.mark.parametrize(
    ("runtime_result", "error"),
    [
        (RuntimeResult(0, b"", b"", cancelled=True), SandboxCancelled),
        (RuntimeResult(0, b"", b"", output_exceeded=True), SandboxOutputLimitExceeded),
    ],
)
def test_cancel_and_stream_output_caps_become_safe_errors(tmp_path, runtime_result, error):
    class FixedRuntime(RecordingRuntime):
        def execute(self, *args, **kwargs):
            self.called = True
            return runtime_result

    runner = SandboxRunner(make_config(tmp_path), FixedRuntime())
    with pytest.raises(error):
        runner.run(SandboxRequest(language="javascript", source="while(true){}"))


def test_cancelled_before_start_does_not_invoke_runtime(tmp_path):
    runtime = RecordingRuntime()
    event = threading.Event()
    event.set()
    runner = SandboxRunner(make_config(tmp_path), runtime)
    with pytest.raises(SandboxCancelled):
        runner.run(SandboxRequest(language="python", source="pass"), cancel_event=event)
    assert runtime.called is False


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


def test_default_runner_refuses_execution_when_docker_cli_is_missing(tmp_path):
    config = make_config(tmp_path)
    marker = tmp_path / "host-marker"
    runner = SandboxRunner(config, DockerCliRuntime(str(tmp_path / "missing-docker.exe")))
    with pytest.raises(IsolationUnavailable, match="runtime is unavailable"):
        runner.run(
            SandboxRequest(language="python", source=f"open({str(marker)!r}, 'w').write('ran')")
        )
    assert not marker.exists()


def test_mounts_reject_external_paths_and_symlink_escapes(tmp_path):
    config = make_config(tmp_path)
    config.work_root.mkdir(parents=True)
    input_dir = config.work_root / "inputs"
    output_dir = config.work_root / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="approved work root"):
        from app.sandbox import build_create_argv

        build_create_argv(
            config,
            container_name="advisor-sbx-" + "c" * 32,
            image=config.python_image,
            language="python",
            input_dir=outside,
            output_dir=output_dir,
            timeout_seconds=2,
        )

    link = config.work_root / "linked-input"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        mount_path = outside
    else:
        mount_path = link
    with pytest.raises(ValueError, match="approved work root|symlink"):
        build_create_argv(
            config,
            container_name="advisor-sbx-" + "d" * 32,
            image=config.python_image,
            language="python",
            input_dir=mount_path,
            output_dir=output_dir,
            timeout_seconds=2,
        )


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


def test_production_uses_local_worker_daemon_for_bind_mounts(tmp_path):
    config = make_config(
        tmp_path,
        production=True,
        separate_sandbox_host=True,
        runtime="runsc",
        docker_host=None,
    )
    input_dir = config.work_root / "inputs"
    output_dir = config.work_root / "outputs"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    from app.sandbox import build_create_argv

    argv = build_create_argv(
        config,
        container_name="advisor-sbx-" + "e" * 32,
        image=config.python_image,
        language="python",
        input_dir=input_dir,
        output_dir=output_dir,
        timeout_seconds=2,
    )
    assert argv[0:2] == ["docker", "create"]
    assert argv[argv.index("--runtime") + 1] == "runsc"
