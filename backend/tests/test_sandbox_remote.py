import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.sandbox import (
    DockerCliRuntime,
    IsolationUnavailable,
    RemoteSandboxClient,
    RemoteSandboxConfig,
    SandboxCancelled,
    SandboxConfig,
    SandboxInputFile,
    SandboxRequest,
    SandboxResult,
    create_worker_app,
)

TOKEN = "a" * 40


def prod_config(tmp_path, **changes):
    values = {
        "work_root": tmp_path / "worker-jobs",
        "production": True,
        "separate_sandbox_host": True,
        "runtime": "runsc",
    }
    values.update(changes)
    return SandboxConfig(**values)


class FakeOfficeRunner:
    def probe_office(self):
        return "sha256:" + "c" * 64


class FakeRunner:
    def __init__(self):
        self.called = None

    def probe_production(self):
        return {"python": "sha256:" + "a" * 64, "javascript": "sha256:" + "b" * 64, "office": "sha256:" + "c" * 64}

    def run(self, request, *, cancel_event=None):
        self.called = (request, cancel_event)
        return SandboxResult(request.language, 0, "ok", "", (), 0.01)


def test_production_worker_requires_local_daemon_and_gvisor(tmp_path):
    for opts in (
        {"runtime": None},
        {"runtime": "runc"},
        {"docker_host": "tcp://host:2376"},
        {"separate_sandbox_host": False},
    ):
        with pytest.raises(IsolationUnavailable):
            create_worker_app(prod_config(tmp_path, **opts), TOKEN, FakeRunner())
    app = create_worker_app(prod_config(tmp_path), TOKEN, FakeRunner(), FakeOfficeRunner())
    health = TestClient(app)
    assert health.get("/v1/health").status_code == 401
    assert (
        health.get("/v1/health", headers={"Authorization": "Bearer " + TOKEN}).json()["runtime"]
        == "runsc"
    )


def test_worker_auth_scoped_bytes_hash_limits_and_rejects_paths(tmp_path):
    runner = FakeRunner()
    app = create_worker_app(prod_config(tmp_path), TOKEN, runner)
    client = TestClient(app)
    raw = b"course data"
    body = {
        "request_id": "req_1",
        "language": "python",
        "source": "print(1)",
        "inputs": [
            {
                "resource_id": "doc:1",
                "filename": "notes.txt",
                "content_base64": base64.b64encode(raw).decode(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    assert client.post("/v1/execute", json=body).status_code == 401
    headers = {"Authorization": "Bearer " + TOKEN}
    assert (
        client.post(
            "/v1/execute",
            json={**body, "inputs": [{**body["inputs"][0], "filename": "../secret"}]},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/execute",
            json={**body, "inputs": [{**body["inputs"][0], "sha256": "0" * 64}]},
            headers=headers,
        ).status_code
        == 422
    )
    response = client.post("/v1/execute", json=body, headers=headers)
    assert response.status_code == 200 and response.json()["stdout"] == "ok"
    request, _event = runner.called
    assert request.inputs[0].content == raw and request.inputs[0].filename == "notes.txt"
    assert not hasattr(request, "host_path")


def test_pre_cancelled_request_is_never_executed(tmp_path):
    runner = FakeRunner()
    client = TestClient(create_worker_app(prod_config(tmp_path), TOKEN, runner))
    headers = {"Authorization": "Bearer " + TOKEN}
    assert client.delete("/v1/jobs/cancel-first", headers=headers).json() == {"cancelled": True}
    response = client.post(
        "/v1/execute",
        json={"request_id": "cancel-first", "language": "python", "source": "pass"},
        headers=headers,
    )
    assert response.status_code == 409
    assert runner.called is None


def test_worker_cancel_sets_runner_event(tmp_path):
    class BlockingRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()

        def run(self, request, *, cancel_event=None):
            self.started.set()
            assert cancel_event.wait(3)
            raise SandboxCancelled("cancelled")

    runner = BlockingRunner()
    client = TestClient(create_worker_app(prod_config(tmp_path), TOKEN, runner))
    headers = {"Authorization": "Bearer " + TOKEN}
    body = {"request_id": "cancel-me", "language": "python", "source": "pass"}
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, "/v1/execute", json=body, headers=headers)
        assert runner.started.wait(2)
        response = client.delete("/v1/jobs/cancel-me", headers=headers)
        assert response.json() == {"cancelled": True}
        assert pending.result(timeout=3).status_code == 409


class Response:
    status = 200

    def __init__(self, raw):
        self.raw = raw
        self.headers = {"Content-Length": str(len(raw))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return self.raw[:n]


class Opener:
    def __init__(self, raw):
        self.responses = list(raw) if isinstance(raw, list) else [raw]
        self.request = None
        self.requests = []

    def open(self, request, timeout):
        self.request = request
        self.requests.append(request)
        return Response(self.responses.pop(0))


def test_remote_client_sends_only_bounded_bytes_and_opaque_ids():
    payload = b"output"
    response = json.dumps(
        {
            "language": "python",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "runtime_seconds": 0.1,
            "files": [
                {
                    "path": "out.txt",
                    "content_base64": base64.b64encode(payload).decode(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
    ).encode()
    health = b'{"status":"ready","runtime":"runsc","separate_host_attested":true,"image_ids":{"python":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","javascript":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'
    opener = Opener([health, response])
    client = RemoteSandboxClient(
        RemoteSandboxConfig("https://sandbox.example", "x" * 40), opener=opener
    )
    result = client.run(
        SandboxRequest("python", "print(1)", (SandboxInputFile("doc:1", "in.txt", b"data"),), 3),
        request_id="opaque_01",
    )
    assert result.files[0].content == payload
    assert len(opener.requests) == 2
    assert opener.requests[0].full_url.endswith("/v1/health")
    req = opener.request
    assert req.full_url == "https://sandbox.example/v1/execute"
    assert req.get_header("Authorization") == "Bearer " + "x" * 40
    sent = json.loads(req.data)
    assert sent["inputs"][0]["content_base64"] == base64.b64encode(b"data").decode()
    assert "path" not in sent["inputs"][0] and "host_path" not in sent


@pytest.mark.parametrize(
    "url",
    [
        "http://sandbox.example",
        "https://user:pass@sandbox.example",
        "https://sandbox.example/x?token=x",
    ],
)
def test_remote_client_rejects_insecure_or_credentialed_urls(url):
    with pytest.raises(ValueError):
        RemoteSandboxConfig(url, "x" * 40)


def test_remote_client_refuses_oversized_response():
    client = RemoteSandboxClient(
        RemoteSandboxConfig("https://sandbox.example", "x" * 40, max_response_bytes=1024),
        opener=Opener(b"x" * 1025),
    )
    with pytest.raises(IsolationUnavailable):
        client._request("POST", "/v1/execute", "id", b"{}", 2)


def test_remote_client_rejects_worker_path_traversal_and_bad_checksum():
    file_data = b"secret"
    invalid = {
        "language": "python",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "runtime_seconds": 0.1,
        "files": [
            {
                "path": "../secret",
                "content_base64": base64.b64encode(file_data).decode(),
                "sha256": hashlib.sha256(file_data).hexdigest(),
            }
        ],
    }
    health = b'{"status":"ready","runtime":"runsc","separate_host_attested":true,"image_ids":{"python":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","javascript":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'
    client = RemoteSandboxClient(
        RemoteSandboxConfig("https://sandbox.example", "x" * 40),
        opener=Opener([health, json.dumps(invalid).encode()]),
    )
    with pytest.raises(IsolationUnavailable):
        client.run(SandboxRequest("python", "pass"), request_id="path")


def test_docker_probe_requires_registered_runsc_and_resolves_both_images(tmp_path, monkeypatch):
    calls = []
    image_id = "sha256:" + "c" * 64

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "info":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"runsc": {"path": "runsc"}}).encode()
            )
        return SimpleNamespace(returncode=0, stdout=(image_id + "\n").encode())

    monkeypatch.setattr("app.sandbox.runner.subprocess.run", fake_run)
    runtime = DockerCliRuntime()
    config = prod_config(tmp_path)
    resolved = runtime.probe(config)
    assert set(resolved) == {config.python_image, config.javascript_image, config.office_image}
    assert set(resolved.values()) == {image_id}
    assert all(
        call[1:3] == ["info", "--format"] or call[1:3] == ["image", "inspect"] for call in calls
    )

    calls.clear()

    def no_runsc(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b'{"runc": {}}')

    monkeypatch.setattr("app.sandbox.runner.subprocess.run", no_runsc)
    with pytest.raises(IsolationUnavailable, match="runsc"):
        runtime.probe(config)


def test_remote_client_refuses_worker_without_image_probe():
    unhealthy = b'{"status":"ready","runtime":"runsc","separate_host_attested":true,"image_ids":{}}'
    client = RemoteSandboxClient(
        RemoteSandboxConfig("https://sandbox.example", "x" * 40), opener=Opener(unhealthy)
    )
    with pytest.raises(IsolationUnavailable):
        client._verify_worker()
