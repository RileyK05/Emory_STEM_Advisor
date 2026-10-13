import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.auth import router
from app.auth.service import (
    AuthConfigurationError,
    AuthRejected,
    AuthService,
    Membership,
    OIDCConfig,
)
from app.db.connection import connect

KEY = b"test-only-server-key-material-32-bytes-minimum"


class FakeProvider:
    async def authorization_endpoint(self):
        return "https://idp.example.test/authorize"

    async def exchange_and_verify(self, code, verifier, nonce):
        assert code == "valid-code"
        assert verifier
        return {
            "iss": "https://idp.example.test",
            "sub": "person-123",
            "aud": "advisor-client",
            "nonce": nonce,
            "exp": time.time() + 600,
            "iat": time.time(),
            "name": "Student Example",
            "email": "student@emory.edu",
        }


def _config(**changes):
    base = {
        "issuer": "https://idp.example.test",
        "client_id": "advisor-client",
        "client_secret": "test-secret",
        "redirect_uri": "https://advisor.example.test/api/auth/callback",
        "server_key": KEY,
    }
    return OIDCConfig(**(base | changes))


def test_oidc_config_fails_closed_on_http_or_short_key():
    with pytest.raises(AuthConfigurationError):
        OIDCConfig(
            issuer="http://idp.example.test", client_id="c", client_secret="s",
            redirect_uri="https://advisor.test/callback", server_key=KEY,
        ).validate()
    with pytest.raises(AuthConfigurationError):
        OIDCConfig(
            issuer="https://idp.example.test", client_id="c", client_secret="s",
            redirect_uri="https://advisor.test/callback", server_key=b"too-short",
        ).validate()
    with pytest.raises(AuthConfigurationError):
        AuthService(lambda: None, mode="oidc", oidc=_config(), provider=FakeProvider())


def test_development_identity_is_loopback_only():
    service = AuthService(lambda: None, mode="development", server_key=KEY)
    assert service.resolve(None, client_host="127.0.0.1").workspace_key == "development"
    assert service.resolve(None, client_host="::1").mode == "development"
    assert service.resolve(None, client_host="192.0.2.10") is None
    assert service.resolve(None, client_host="testclient") is None
    test_service = AuthService(lambda: None, mode="development", server_key=KEY, allow_test_client=True)
    assert test_service.resolve(None, client_host="testclient").workspace_key == "development"


def test_oidc_login_transactions_sessions_replay_and_revocation(db, migrated_dsn):
    service = AuthService(
        lambda: connect(migrated_dsn), mode="oidc", oidc=_config(),
        provider=FakeProvider(), membership_policy=lambda claims: Membership("ignored"),
    )
    async def scenario():
        start = await service.begin_login()
        params = parse_qs(urlsplit(start.authorization_url).query)
        assert params["redirect_uri"] == [_config().redirect_uri]
        assert params["code_challenge_method"] == ["S256"]
        assert "code_verifier" not in params
        state = params["state"][0]
        assert start.state == state
        cookie = await service.complete_login(state, "valid-code")
        assert "session_hash" not in cookie
        principal = service.resolve(cookie, client_host="203.0.113.7")
        assert principal is not None
        assert principal.email == "student@emory.edu"
        assert principal.workspace_key.startswith("oidc:")
        assert principal.workspace_key != "ignored"
        with pytest.raises(AuthRejected):
            await service.complete_login(state, "valid-code")
        service.logout(cookie)
        assert service.resolve(cookie, client_host="203.0.113.7") is None

    asyncio.run(scenario())


def test_auth_http_routes_use_fixed_callback_and_secure_cookie(db, migrated_dsn):
    service = AuthService(
        lambda: connect(migrated_dsn), mode="oidc", oidc=_config(),
        provider=FakeProvider(), membership_policy=lambda claims: Membership("ignored"),
    )
    app = FastAPI()
    app.state.auth_service = service
    app.include_router(router)
    client = TestClient(app, base_url="https://testserver")
    login = client.get("/api/auth/login", follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["cache-control"] == "no-store"
    query = parse_qs(urlsplit(login.headers["location"]).query)
    assert query["redirect_uri"] == [_config().redirect_uri]
    assert "x-forwarded-host" not in login.headers
    assert "__Host-emory_login_state=" in login.headers["set-cookie"]

    attacker = TestClient(app, base_url="https://testserver")
    attacker_login = attacker.get("/api/auth/login", follow_redirects=False)
    attacker_state = parse_qs(urlsplit(attacker_login.headers["location"]).query)["state"][0]
    victim = TestClient(app, base_url="https://testserver")
    forged = victim.get("/api/auth/callback", params={"state": attacker_state, "code": "valid-code"})
    assert forged.status_code == 400
    assert '__Host-emory_login_state="";' in forged.headers["set-cookie"]

    callback = client.get(
        "/api/auth/callback",
        params={"state": query["state"][0], "code": "valid-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    cookie_header = callback.headers["set-cookie"].lower()
    assert "httponly" in cookie_header and "secure" in cookie_header and "samesite=lax" in cookie_header
    assert "__host-emory_session=" in cookie_header
    assert '__host-emory_login_state="";' in cookie_header
    signed_in = client.get("/api/session")
    assert signed_in.json()["authenticated"] is True
    # The session body is private: it must never be cached or leak a referrer.
    assert signed_in.headers["cache-control"] == "no-store"
    assert signed_in.headers["referrer-policy"] == "no-referrer"
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post(
        "/api/auth/logout", headers={"Origin": "https://advisor.example.test"}
    ).status_code == 204
    assert client.get("/api/session").json()["authenticated"] is False
