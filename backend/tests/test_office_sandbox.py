from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.sandbox.office import (
    MAX_OFFICE_INPUT_BYTES,
    OfficeJobResult,
    OfficeRunner,
    build_office_create_argv,
)
from app.sandbox.office_remote import RemoteLibreOfficeRunner
from app.sandbox.remote import RemoteSandboxClient, RemoteSandboxConfig
from app.sandbox.runner import DockerCliRuntime, RuntimeResult
from app.sandbox.types import IsolationUnavailable, SandboxCancelled, SandboxConfig
from app.sandbox.worker import create_worker_app

TOKEN = "s" * 40
IMAGE_ID = "sha256:" + "a" * 64
OFFICE_REF = "emory-sandbox-office:7.4.7-v1"


def config(tmp_path, **changes):
    values = {
        "work_root": tmp_path / "jobs", "production": True,
        "separate_sandbox_host": True, "runtime": "runsc",
        "max_timeout_seconds": 10, "max_output_bytes": 4096,
    }
    values.update(changes)
    return SandboxConfig(**values)


def zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


class RecordingOfficeRuntime:
    def __init__(self, output=None, *, exit_code=0, cancelled=False):
        self.output, self.exit_code, self.cancelled = output, exit_code, cancelled
        self.argv = None
        self.called = False

    def execute(self, argv, *, container_name, timeout_seconds, output_limit_bytes, cancel_event):
        self.argv, self.called = argv, True
        out_mount = next(a for a in argv if "target=/output" in a)
        out_dir = Path(out_mount.split("source=", 1)[1].split(",target=", 1)[0])
        op = argv[-1]
        if self.output is not None:
            out_name = "input.xlsx" if op == "recalculate_xlsx" else "input.pdf"
            (out_dir / out_name).write_bytes(self.output)
        return RuntimeResult(self.exit_code, b"", b"", cancelled=self.cancelled)

    def probe(self, cfg):
        return {cfg.python_image: IMAGE_ID, cfg.javascript_image: IMAGE_ID, cfg.office_image: IMAGE_ID}


@pytest.mark.parametrize("operation", ["recalculate_xlsx", "render_xlsx_pdf", "render_docx_pdf"])
def test_office_runner_uses_fixed_non_network_container_command(tmp_path, operation):
    result_bytes = zip_bytes() if operation == "recalculate_xlsx" else b"%PDF-1.7\nrendered"
    runtime = RecordingOfficeRuntime(result_bytes)
    runner = OfficeRunner(config(tmp_path), runtime)
    raw = zip_bytes()
    result = runner.run(operation, raw, timeout_seconds=5)
    assert result.content == result_bytes
    assert result.input_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.output_sha256 == hashlib.sha256(result_bytes).hexdigest()
    argv = runtime.argv
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--user") + 1] == "65532:65532"
    assert argv[argv.index("--runtime") + 1] == "runsc"
    assert argv[-2:] == ["/usr/local/bin/office-convert", operation]
    assert not any(str(raw) in part for part in argv)
    assert list(config(tmp_path).work_root.glob("**/*")) == []


def test_office_runner_rejects_invalid_format_size_and_unknown_command_before_runtime(tmp_path):
    runtime = RecordingOfficeRuntime(zip_bytes())
    runner = OfficeRunner(config(tmp_path), runtime)
    with pytest.raises(ValueError, match="OOXML"):
        runner.run("recalculate_xlsx", b"not an xlsx")
    with pytest.raises(ValueError, match="byte limit"):
        runner.run("recalculate_xlsx", b"PK" + b"x" * MAX_OFFICE_INPUT_BYTES)
    with pytest.raises(ValueError, match="unsupported"):
        runner.run("rm -rf /", zip_bytes())
    with pytest.raises(ValueError, match="timeout"):
        runner.run("recalculate_xlsx", zip_bytes(), timeout_seconds=0)
    assert not runtime.called


def test_office_runner_fails_closed_for_missing_daemon_and_kills_cancelled_job(tmp_path):
    cfg = config(tmp_path, production=True, runtime="runsc", separate_sandbox_host=True)
    runner = OfficeRunner(cfg, DockerCliRuntime(str(tmp_path / "missing-docker.exe")))
    with pytest.raises(IsolationUnavailable, match="daemon"):
        runner.run("recalculate_xlsx", zip_bytes())
    assert list(cfg.work_root.iterdir()) == []

    pinned = RecordingOfficeRuntime(zip_bytes())
    OfficeRunner(cfg, pinned).run("recalculate_xlsx", zip_bytes())
    assert pinned.argv[pinned.argv.index("--runtime") + 1] == "runsc"

    event = threading.Event()
    event.set()
    runtime = RecordingOfficeRuntime(zip_bytes())
    with pytest.raises(SandboxCancelled):
        OfficeRunner(config(tmp_path / "cancel"), runtime).run(
            "recalculate_xlsx", zip_bytes(), cancel_event=event
        )
    assert not runtime.called
    cancelled_runtime = RecordingOfficeRuntime(zip_bytes(), cancelled=True)
    with pytest.raises(SandboxCancelled):
        OfficeRunner(config(tmp_path / "runtime-cancel"), cancelled_runtime).run(
            "recalculate_xlsx", zip_bytes()
        )


def test_office_runner_rejects_missing_or_wrong_output(tmp_path):
    with pytest.raises(IsolationUnavailable, match="expected output"):
        OfficeRunner(config(tmp_path), RecordingOfficeRuntime(None)).run(
            "recalculate_xlsx", zip_bytes()
        )
    with pytest.raises(IsolationUnavailable, match="invalid PDF"):
        OfficeRunner(config(tmp_path), RecordingOfficeRuntime(b"not-pdf")).run(
            "render_docx_pdf", zip_bytes()
        )


def test_office_mounts_must_stay_under_approved_root(tmp_path):
    cfg = config(tmp_path)
    cfg.work_root.mkdir(parents=True)
    inside_in, inside_out = cfg.work_root / "input", cfg.work_root / "output"
    inside_in.mkdir(); inside_out.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    with pytest.raises(ValueError, match="approved work root"):
        build_office_create_argv(
            cfg, container_name="advisor-office-" + "a" * 32, image=cfg.office_image,
            input_dir=external, output_dir=inside_out,
            operation="recalculate_xlsx", timeout_seconds=2,
        )


def test_office_readiness_fails_when_image_probe_is_missing(tmp_path):
    class GenericRunner:
        def probe_production(self):
            return {"python": IMAGE_ID, "javascript": IMAGE_ID}
    class MissingOffice:
        def probe_office(self):
            raise IsolationUnavailable("missing Office image")
    app = create_worker_app(
        config(tmp_path, production=True, runtime="runsc", separate_sandbox_host=True),
        TOKEN, GenericRunner(), MissingOffice(),
    )
    response = TestClient(app).get(
        "/v1/health", headers={"Authorization":"Bearer "+TOKEN}
    )
    assert response.status_code == 503

def test_worker_cancel_kills_active_office_request(tmp_path):
    class GenericRunner:
        def probe_production(self):
            return {"python": IMAGE_ID, "javascript": IMAGE_ID, OFFICE_REF: IMAGE_ID}
    class BlockingOffice:
        def __init__(self): self.started = threading.Event()
        def probe_office(self): return IMAGE_ID
        def run(self, operation, content, *, timeout_seconds, cancel_event):
            self.started.set()
            assert cancel_event.wait(3)
            raise SandboxCancelled("cancelled")
    office = BlockingOffice()
    app = create_worker_app(
        config(tmp_path, production=True, runtime="runsc", separate_sandbox_host=True),
        TOKEN, GenericRunner(), office,
    )
    client = TestClient(app)
    raw = zip_bytes()
    body = {"request_id":"office-cancel", "operation":"recalculate_xlsx",
            "input_base64":base64.b64encode(raw).decode(),
            "input_sha256":hashlib.sha256(raw).hexdigest()}
    headers = {"Authorization":"Bearer "+TOKEN}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/v1/office/execute", json=body, headers=headers)
        assert office.started.wait(2)
        assert client.delete("/v1/jobs/office-cancel", headers=headers).json() == {"cancelled": True}
        assert pending.result(timeout=3).status_code == 409

def test_libreoffice_output_is_not_claimed_without_formula_cached_values(tmp_path):
    from app.artifacts import (
        WorkbookCell,
        WorkbookSheet,
        WorkbookSpec,
        export_xlsx,
        recalculate_xlsx,
    )

    spec = WorkbookSpec(title="Calc", sheets=[WorkbookSheet(
        id="s", name="Sheet1", cells=[
            WorkbookCell(id="x", row=1, column=1, value=2),
            WorkbookCell(id="total", row=2, column=1, formula="=SUM(A1:A1)"),
        ],
    )])
    raw = export_xlsx(spec)
    # The runner can return a structurally valid XLSX that still has no formula cache.
    result = recalculate_xlsx(raw, spec, runner=OfficeRunner(
        config(tmp_path), RecordingOfficeRuntime(raw)
    ))
    assert result.status == "needs_recalculation"
    assert result.xlsx_bytes == raw

class Response:
    status = 200
    def __init__(self, body): self.body = body; self.headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n): return self.body[:n]


class Opener:
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    def open(self, request, timeout): self.requests.append(request); return Response(self.responses.pop(0))


def health():
    return json.dumps({"status":"ready", "runtime":"runsc", "separate_host_attested":True,
                       "image_ids":{OFFICE_REF:IMAGE_ID}}).encode()


def test_remote_office_client_sends_authenticated_bounded_bytes_and_checks_checksum():
    raw, output = zip_bytes(), b"%PDF-1.7\nconverted"
    response = json.dumps({"operation":"render_docx_pdf",
        "input_sha256":hashlib.sha256(raw).hexdigest(),
        "output_sha256":hashlib.sha256(output).hexdigest(),
        "output_base64":base64.b64encode(output).decode()}).encode()
    opener = Opener([health(), response])
    cfg = RemoteSandboxConfig("https://office.example", TOKEN, max_response_bytes=48*1024*1024)
    assert TOKEN not in repr(cfg)
    client = RemoteSandboxClient(cfg, opener=opener)
    office = RemoteLibreOfficeRunner(cfg, office_image=OFFICE_REF, client=client)
    assert office.render_docx_pdf(raw) == output
    req = opener.requests[-1]
    assert req.full_url.endswith("/v1/office/execute")
    assert req.get_header("Authorization") == "Bearer " + TOKEN
    sent = json.loads(req.data)
    assert sent["input_base64"] == base64.b64encode(raw).decode()
    assert "host_path" not in sent and "path" not in sent


def test_worker_office_endpoint_auth_checksum_and_cancel(tmp_path):
    class GenericRunner:
        def probe_production(self): return {"python":"sha256:"+"a"*64, "javascript":"sha256:"+"b"*64, OFFICE_REF:IMAGE_ID}
        def run(self, *args, **kwargs): raise AssertionError("not used")
    class FakeOffice:
        def probe_office(self): return IMAGE_ID
        def run(self, operation, content, *, timeout_seconds, cancel_event):
            assert content == raw
            output = b"%PDF-1.7\nworker"
            return OfficeJobResult(operation, output, hashlib.sha256(content).hexdigest(),
                                   hashlib.sha256(output).hexdigest(), 0.01)
    raw = zip_bytes()
    app = create_worker_app(config(tmp_path, production=True, runtime="runsc", separate_sandbox_host=True),
                            TOKEN, GenericRunner(), FakeOffice())
    client = TestClient(app)
    body = {"request_id":"office-1", "operation":"render_docx_pdf",
            "input_base64":base64.b64encode(raw).decode(), "input_sha256":hashlib.sha256(raw).hexdigest()}
    assert client.post("/v1/office/execute", json=body).status_code == 401
    headers = {"Authorization":"Bearer "+TOKEN}
    assert client.post("/v1/office/execute", json={**body,"input_sha256":"0"*64}, headers=headers).status_code == 422
    result = client.post("/v1/office/execute", json=body, headers=headers)
    assert result.status_code == 200
    assert base64.b64decode(result.json()["output_base64"]).startswith(b"%PDF-")
    assert client.get("/v1/health", headers=headers).json()["runtime"] == "runsc"
