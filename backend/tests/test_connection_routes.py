from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.connections import router
from app.connections.models import CredentialDiagnostic, ProfileView


def _view():
    return ProfileView(profile_id=uuid4(), name="local", provider="openai", model_id="model-x",
        base_url=None, account_id=None, secret_configured=True, provider_options={},
        roles=frozenset({"standard"}), capabilities=frozenset({"streaming"}),
        data_scopes=frozenset({"institutional_corpus"}), enabled=True, priority=10,
        revision=1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC), eval_passed=False)


class Conn:
    def commit(self):
        pass
    def rollback(self):
        pass


class Repo:
    def __init__(self):
        self.item = _view()
        self.secret = None
    def list_views(self, conn):
        return (self.item,)
    def create(self, conn, data):
        self.secret = data.secret
        return self.item
    def update(self, conn, profile_id, data):
        return self.item
    def replace_secret(self, conn, profile_id, *, expected_revision, secret):
        self.secret = secret
        return self.item
    def clear_secret(self, conn, profile_id, *, expected_revision):
        return replace(self.item, secret_configured=False, revision=expected_revision + 1)
    def delete(self, conn, profile_id, *, expected_revision):
        return True
    def get(self, conn, profile_id):
        return SimpleNamespace(profile_id=profile_id) if profile_id == self.item.profile_id else None
    def diagnose(self, profile):
        return CredentialDiagnostic("configured", "Provider credential is available.")


def _client(*, enabled, host="127.0.0.1"):
    app = FastAPI()
    app.state.settings = SimpleNamespace(admin_api_enabled=enabled, profile_encryption_key="unused")
    app.state.override_conn = Conn()
    app.state.connection_profiles = Repo()
    app.state.connection_runtime = SimpleNamespace(invalidate=lambda _profile_id=None: None)
    app.include_router(router)
    return TestClient(app, client=(host, 1234), base_url="http://127.0.0.1"), app.state.connection_profiles


def test_connection_admin_is_hidden_until_explicitly_enabled():
    client, _ = _client(enabled=False)
    assert client.get("/api/admin/connections").status_code == 404


def test_connection_admin_requires_loopback_and_masks_credentials():
    remote, _ = _client(enabled=True, host="192.0.2.8")
    assert remote.get("/api/admin/connections").status_code == 403

    client, repo = _client(enabled=True)
    assert client.get("/api/admin/connections", headers={"origin":"http://attacker.example"}).status_code == 403
    assert client.get("/api/admin/connections", headers={"host":"attacker.example"}).status_code == 403
    assert client.get("/api/admin/connections", headers={"x-forwarded-for":"203.0.113.10"}).status_code == 403
    assert client.get("/api/admin/connections", headers={"origin":"http://127.0.0.1:5173"}).status_code == 200
    response = client.get("/api/admin/connections")
    assert response.status_code == 200
    assert response.json()[0]["secret_configured"] is True
    assert "secret" not in response.json()[0] and "api_key" not in response.json()[0]

    created = client.post("/api/admin/connections", json={
        "name":"fresh", "provider":"openai", "model_id":"model-x", "roles":["standard"],
        "capabilities":["streaming"], "data_scopes":["institutional_corpus"],
        "secret":{"api_key":"super-secret-value"}})
    assert created.status_code == 201
    assert repo.secret == {"api_key":"super-secret-value"}
    assert "super-secret-value" not in created.text

    replaced = client.put(f"/api/admin/connections/{repo.item.profile_id}/secret", json={
        "expected_revision":1, "secret":{"api_key":"replacement-secret"}})
    assert replaced.status_code == 200 and "replacement-secret" not in replaced.text
    assert repo.secret == {"api_key":"replacement-secret"}

    cleared = client.request("DELETE", f"/api/admin/connections/{repo.item.profile_id}/secret",
                             json={"expected_revision":1})
    assert cleared.status_code == 200 and cleared.json()["secret_configured"] is False

    diagnosis = client.get(f"/api/admin/connections/{repo.item.profile_id}/diagnose")
    assert diagnosis.status_code == 200 and diagnosis.json()["status"] == "configured"
    assert client.post(f"/api/admin/connections/{repo.item.profile_id}/evaluation", json={
        "passed":True}).status_code == 404
