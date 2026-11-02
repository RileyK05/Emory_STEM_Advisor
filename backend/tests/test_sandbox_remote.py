import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.sandbox import (
    DockerCliRuntime,
    IsolationUnavailable,
    RemoteSandboxClient,
    RemoteSandboxConfig,
    SandboxConfig,
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


class FakeScreenshotRunner:
    def probe_browser(self):
        return "sha256:" + "d" * 64


def test_production_worker_requires_local_daemon_and_gvisor(tmp_path):
    for opts in (
        {"runtime": None},
        {"runtime": "runc"},
        {"docker_host": "tcp://host:2376"},
        {"separate_sandbox_host": False},
    ):
        with pytest.raises(IsolationUnavailable):
            create_worker_app(
                prod_config(tmp_path, **opts), TOKEN, office_runner=FakeOfficeRunner()
            )
    app = create_worker_app(prod_config(tmp_path), TOKEN, office_runner=FakeOfficeRunner())
    health = TestClient(app)
    assert health.get("/v1/health").status_code == 401
    body = health.get("/v1/health", headers={"Authorization": "Bearer " + TOKEN}).json()
    assert body["runtime"] == "runsc"
    # Program execution is gone: the worker reports the images it still runs.
    assert set(body["image_ids"]) == {prod_config(tmp_path).office_image}


def test_health_lists_the_office_and_browser_images_when_configured(tmp_path):
    browser = "emory-sandbox-browser:1.50.0-v1"
    config = prod_config(tmp_path, browser_image=browser)
    app = create_worker_app(
        config,
        TOKEN,
        office_runner=FakeOfficeRunner(),
        screenshot_runner=FakeScreenshotRunner(),
    )
    body = TestClient(app).get(
        "/v1/health", headers={"Authorization": "Bearer " + TOKEN}
    ).json()
    assert body["image_ids"] == {
        config.office_image: "sha256:" + "c" * 64,
        browser: "sha256:" + "d" * 64,
    }


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
        client._request("POST", "/v1/office/execute", "id", b"{}", 2)


def test_docker_probe_requires_registered_runsc_and_resolves_image_ids(tmp_path, monkeypatch):
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
    assert set(resolved) == {config.office_image}
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
