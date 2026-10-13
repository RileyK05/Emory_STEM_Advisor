"""Application authentication, membership gates, and private resource isolation."""
import asyncio
import base64
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth.factory import build_auth_service, membership_policy
from app.auth.service import AuthConfigurationError, AuthService, Membership, OIDCConfig
from app.config import load_settings
from app.db.connection import connect
from tests.fakes import FakeEmbedder, FakeProvider
from tests.helpers import lenient_config

ORIGIN = "https://advisor.example.test"
KEY = b"offline-integration-secret-at-least-32-bytes"


class IdentityProvider:
    async def authorization_endpoint(self):
        return "https://idp.example.test/authorize"

    async def exchange_and_verify(self, code, verifier, nonce):
        assert verifier
        return {"iss": "https://idp.example.test", "sub": code, "aud": "client",
                "nonce": nonce, "exp": time.time() + 600, "iat": time.time(),
                "email": code + "@example.test", "email_verified": True}


def test_membership_policy_is_explicit_and_verified():
    settings = load_settings()
    with pytest.raises(AuthConfigurationError):
        membership_policy(settings)
    allow = membership_policy(replace(settings, auth_verified_email_domains=("emory.edu",)))
    assert allow({"email": "person@emory.edu", "email_verified": True})
    assert allow({"email": "person@emory.edu", "email_verified": False}) is None
    assert allow({"email": "person@emory.edu.attacker.test", "email_verified": True}) is None
    assert allow({"email": "person@emory.edu", "email_verified": "true"}) is None
    by_claim = membership_policy(replace(settings, auth_membership_claim="groups",
                                        auth_membership_values=("student",)))
    assert by_claim({"groups": ["student", "other"]})
    assert by_claim({"groups": "student"})
    assert by_claim({"groups": ["studentish"]}) is None


def test_oidc_factory_cannot_start_with_missing_policy_or_key():
    settings = replace(load_settings(), auth_mode="oidc", oidc_issuer="https://idp.example.test",
                       oidc_client_id="client", oidc_client_secret="secret",
                       oidc_redirect_uri=ORIGIN + "/api/auth/callback")
    with pytest.raises(AuthConfigurationError):
        build_auth_service(settings, lambda: None)
    with pytest.raises(AuthConfigurationError):
        build_auth_service(replace(settings, auth_server_key=base64.b64encode(KEY).decode()),
                           lambda: None)


def test_two_verified_users_cannot_read_or_mutate_private_resources(db, collection, migrated_dsn):
    config = OIDCConfig("https://idp.example.test", "client", "secret",
                        ORIGIN + "/api/auth/callback", KEY)
    service = AuthService(lambda: connect(migrated_dsn), mode="oidc", oidc=config,
                          provider=IdentityProvider(),
                          membership_policy=lambda claims: Membership("ignored"))
    settings = replace(load_settings(), database_url=migrated_dsn)
    app = create_app(settings=settings, config=lenient_config(), embedder=FakeEmbedder(8),
                     provider=FakeProvider(), auth_service=service)
    app.state.override_conn = db
    alice = TestClient(app, base_url=ORIGIN)
    bob = TestClient(app, base_url=ORIGIN)
    anonymous = TestClient(app, base_url=ORIGIN)
    assert anonymous.get("/health").status_code == 200
    assert anonymous.get("/api/session").json() == {"mode": "oidc", "authenticated": False}
    assert anonymous.get("/api/artifacts").status_code == 401
    assert anonymous.get("/api/sources").status_code == 401

    def sign_in(client, subject):
        login = client.get("/api/auth/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        callback = client.get("/api/auth/callback", params={"state": state, "code": subject},
                              follow_redirects=False)
        assert callback.status_code == 303
        return client.get("/api/session").json()["workspaceKey"]

    assert sign_in(alice, "alice") != sign_in(bob, "bob")
    headers = {"Origin": ORIGIN}
    spec = {"kind": "document", "title": "Private", "blocks": [
        {"id": "p1", "type": "paragraph", "text": "Alice material"}]}
    made = alice.post("/api/artifacts", json={"kind": "document", "spec": spec}, headers=headers)
    assert made.status_code == 200
    artifact = made.json()
    assert len(alice.get("/api/artifacts").json()) == 1
    assert bob.get("/api/artifacts").json() == []
    endpoint = "/api/artifacts/" + artifact["artifactId"]
    for path in (endpoint, endpoint + "/revisions", endpoint + "/preview",
                 endpoint + "/download?format=md", endpoint + "/original"):
        assert bob.get(path).status_code == 404
    assert bob.post(endpoint + "/revisions", headers=headers, json={
        "expectedParentRevisionId": artifact["revisionId"], "spec": spec}).status_code == 404

    run = alice.post("/api/runs", headers=headers, json={
        "kind": "document", "inputs": {"prompt": "Create a private draft"},
        "idempotencyKey": "alice-run"}).json()
    run_path = "/api/runs/" + run["runId"]
    assert bob.get(run_path).status_code == 404
    assert bob.get(run_path + "/events").status_code == 404
    assert bob.post(run_path + "/cancel", headers=headers).status_code == 404
    assert alice.get(run_path).status_code == 200
    conversation = alice.post("/api/conversations", headers=headers,
                              json={"title": "Alice private history"}).json()
    conversation_path = "/api/conversations/" + conversation["conversationId"]
    assert bob.get("/api/conversations").json() == []
    assert bob.get(conversation_path).status_code == 404
    from uuid import uuid4
    assert bob.post(conversation_path + "/turns", headers=headers, json={
        "query": "show history", "mode": "history", "requestId": str(uuid4()),
        "expectedSequence": 0}).status_code == 404
    assert bob.post(conversation_path + "/turns/" + str(uuid4()) + "/abandon",
                    headers=headers).status_code == 404

    collection_name = db.execute("SELECT name FROM collections WHERE collection_id=%s",
                                 (collection,)).fetchone()["name"]
    upload = alice.post("/api/sources", headers=headers, data={"collection": collection_name},
                        files={"file": ("private.txt", b"Alice private quantum mechanics text.",
                                        "text/plain")})
    assert upload.status_code == 200
    source_id = upload.json()["id"]
    assert any(s["id"] == source_id for s in alice.get(
        "/api/sources", params={"collection": collection_name}).json())
    assert bob.get("/api/sources", params={"collection": collection_name}).json() == []
    forbidden = bob.post("/api/query", headers=headers, json={
        "query": "quantum mechanics", "collectionId": collection_name, "sourceIds": [source_id]})
    assert forbidden.status_code == 404
    assert alice.post("/api/artifacts", json={"kind": "document", "spec": spec}).status_code == 403
    assert alice.post("/api/artifacts", headers={"Origin": "https://attacker.test"},
                      json={"kind": "document", "spec": spec}).status_code == 403
    assert alice.get(endpoint, headers={"Host": "attacker.test"}).status_code == 403
    assert alice.post("/api/auth/logout", headers=headers).status_code == 204
    assert alice.get(endpoint).status_code == 401
    assert "no-store" in made.headers["cache-control"]


def test_development_boundary_rejects_external_hosts_and_forwarded_requests(db, collection):
    from tests.helpers import api_client
    client = api_client(db, collection)
    assert client.get("/api/artifacts").status_code == 200
    assert client.get("/api/artifacts", headers={"Host": "attacker.test"}).status_code == 403
    assert client.get("/api/artifacts", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 403
    assert client.post("/api/artifacts", headers={"Origin": "https://attacker.test"},
                       json={"kind": "document", "spec": {}}).status_code == 403


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), -1])
def test_service_rejects_invalid_time_claims(db, migrated_dsn, invalid):
    class BadProvider(IdentityProvider):
        async def exchange_and_verify(self, code, verifier, nonce):
            result = dict(await super().exchange_and_verify(code, verifier, nonce))
            result["iat"] = invalid
            return result
    from app.auth.service import AuthRejected
    service = AuthService(lambda: connect(migrated_dsn), mode="oidc", oidc=OIDCConfig(
        "https://idp.example.test", "client", "secret", ORIGIN + "/api/auth/callback", KEY),
        provider=BadProvider(), membership_policy=lambda claims: Membership("ignored"))
    async def scenario():
        start = await service.begin_login()
        with pytest.raises(AuthRejected):
            await service.complete_login(start.state, "alice")
    asyncio.run(scenario())
