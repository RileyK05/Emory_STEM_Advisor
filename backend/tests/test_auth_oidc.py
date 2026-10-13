"""Offline OIDC verification tests using real Authlib/joserfc and an HTTPX mock IDP."""

import asyncio
import time
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.jwk import RSAKey

from app.auth.oidc import AuthlibOIDCProvider
from app.auth.service import AuthRejected, OIDCConfig

ISSUER = "https://issuer.example.test"
CLIENT_ID = "advisor-client"
NONCE = "bound-nonce-value"
VERIFIER = "pkce-verifier-test-value"


def _config():
    return OIDCConfig(
        issuer=ISSUER, client_id=CLIENT_ID, client_secret="mock-client-secret",
        redirect_uri="https://advisor.example.test/api/auth/callback",
        server_key=b"test-auth-key-material-that-is-at-least-32-bytes",
    )


def _signed_token(key, **changes):
    now = time.time()
    claims = {
        "iss": ISSUER, "sub": "subject-123", "aud": CLIENT_ID,
        "exp": now + 600, "iat": now, "nonce": NONCE,
    }
    # Resolve relative times when the token is signed, not at test collection:
    # a slow DB/bootstrap run must not turn a future claim into a valid one.
    claims.update({name: value() if callable(value) else value
                   for name, value in changes.items()})
    return jwt.encode({"alg": "RS256", "kid": "mock-signing-key"}, claims, key)


def _mock_idp(monkeypatch, token, signing_key):
    public_key = signing_key.as_dict(private=False) | {
        "kid": "mock-signing-key", "use": "sig", "alg": "RS256",
    }
    requests = []

    def response_body(request):
        requests.append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return 200, {
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "jwks_uri": ISSUER + "/jwks",
            }
        if request.url.path == "/token":
            assert request.method == "POST"
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code"] == ["mock-code"]
            assert form["code_verifier"] == [VERIFIER]
            return 200, {"access_token": "mock-access-token",
                         "token_type": "Bearer", "id_token": token}
        if request.url.path == "/jwks":
            return 200, {"keys": [public_key]}
        return 404, {}

    def sync_handler(request):
        status, body = response_body(request)
        return httpx.Response(status, json=body, request=request)

    def async_handler(request):
        status, body = response_body(request)
        return httpx2.Response(status, json=body, request=request)

    transport = httpx.MockTransport(sync_handler)
    async_transport = httpx2.MockTransport(async_handler)
    original_httpx_init = httpx.AsyncClient

    def async_client_with_transport(*args, **kwargs):
        kwargs["transport"] = transport
        return original_httpx_init(*args, **kwargs)

    monkeypatch.setattr("app.auth.oidc.httpx.AsyncClient", async_client_with_transport)
    original_init = AsyncOAuth2Client.__init__

    def init_with_transport(self, *args, **kwargs):
        kwargs["transport"] = async_transport
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(AsyncOAuth2Client, "__init__", init_with_transport)
    return requests


def test_authlib_joserfc_validate_signed_id_token_and_pkce_exchange(monkeypatch):
    key = RSAKey.generate_key(2048, auto_kid=True)
    token = _signed_token(key)
    requests = _mock_idp(monkeypatch, token, key)
    provider = AuthlibOIDCProvider(_config())
    claims = asyncio.run(provider.exchange_and_verify("mock-code", VERIFIER, NONCE))
    assert claims["sub"] == "subject-123"
    assert claims["aud"] == CLIENT_ID
    assert any(r.url.path.endswith("/.well-known/openid-configuration") for r in requests)
    assert any(r.url.path == "/token" for r in requests)
    assert any(r.url.path == "/jwks" for r in requests)


@pytest.mark.parametrize("changes", [
    {"iss": "https://attacker.example.test"},
    {"sub": ""},
    {"aud": "another-client"},
    {"aud": [CLIENT_ID, "other-client"], "azp": "other-client"},
    {"nonce": "wrong-nonce"},
    {"exp": lambda: time.time() - 120},
    {"exp": None},
    {"iat": lambda: time.time() + 120},
    {"iat": lambda: time.time() - 90_000},
    {"iat": None},
    {"nbf": lambda: time.time() + 120},
])
def test_signed_but_invalid_id_token_claims_are_rejected(monkeypatch, changes):
    key = RSAKey.generate_key(2048, auto_kid=True)
    _mock_idp(monkeypatch, _signed_token(key, **changes), key)
    provider = AuthlibOIDCProvider(_config())
    with pytest.raises(AuthRejected):
        asyncio.run(provider.exchange_and_verify("mock-code", VERIFIER, NONCE))


def test_id_token_signature_tampering_and_disallowed_algorithm_fail(monkeypatch):
    key = RSAKey.generate_key(2048, auto_kid=True)
    token = _signed_token(key)
    pieces = token.split(".")
    pieces[-1] = ("A" if pieces[-1][0] != "A" else "B") + pieces[-1][1:]
    _mock_idp(monkeypatch, ".".join(pieces), key)
    with pytest.raises(AuthRejected):
        asyncio.run(AuthlibOIDCProvider(_config()).exchange_and_verify("mock-code", VERIFIER, NONCE))

    # A MAC token is signed using the client secret. The provider allowlist only
    # accepts asymmetric ID-token algorithms, so it must be rejected.
    from joserfc.jwk import OctKey

    mac_key = OctKey.import_key(_config().client_secret.encode())
    mac = jwt.encode({"alg": "HS256", "kid": "mock-signing-key"}, {
        "iss": ISSUER, "sub": "subject-123", "aud": CLIENT_ID,
        "exp": time.time() + 600, "iat": time.time(), "nonce": NONCE,
    }, mac_key)
    _mock_idp(monkeypatch, mac, key)
    with pytest.raises(AuthRejected):
        asyncio.run(AuthlibOIDCProvider(_config()).exchange_and_verify("mock-code", VERIFIER, NONCE))


def test_discovery_issuer_and_endpoint_transport_fail_closed(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "issuer": "https://attacker.example.test",
            "authorization_endpoint": "https://attacker.example.test/authorize",
            "token_endpoint": "https://attacker.example.test/token",
            "jwks_uri": "https://attacker.example.test/jwks",
        }, request=request)

    original = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("app.auth.oidc.httpx.AsyncClient", mocked_client)
    provider = AuthlibOIDCProvider(_config())
    with pytest.raises(AuthRejected, match="issuer did not match"):
        asyncio.run(provider.authorization_endpoint())
    assert len(requests) == 1
