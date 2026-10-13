"""Authlib-backed OpenID Connect discovery, code exchange, and ID-token checks."""

from __future__ import annotations

import hmac
import json
import math
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.auth.service import AuthRejected, OIDCConfig

_ALLOWED_ALGORITHMS = ("RS256", "RS384", "PS256", "PS384", "ES256", "ES384", "EdDSA")
_MAX_DISCOVERY_BYTES = 256 * 1024
_MAX_TOKEN_BYTES = 64 * 1024


async def _bounded_json(client: httpx.AsyncClient, method: str, url: str, *, limit: int,
                        **kwargs: object) -> tuple[int, dict[str, object]]:
    async with client.stream(method, url, **kwargs) as response:
        if response.is_redirect:
            raise AuthRejected("identity provider attempted a redirect")
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    raise AuthRejected("identity provider response is too large")
            except ValueError:
                raise AuthRejected("identity provider response length is invalid")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise AuthRejected("identity provider response is too large")
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise AuthRejected("identity provider response is invalid") from exc
        if not isinstance(decoded, dict):
            raise AuthRejected("identity provider response is invalid")
        return response.status_code, decoded


def append_query(url: str, values: Mapping[str, str]) -> str:
    parts = urlsplit(url)
    if parts.fragment:
        raise AuthRejected("provider authorization endpoint is invalid")
    existing = {key for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if existing.intersection(values):
        raise AuthRejected("provider authorization endpoint has conflicting parameters")
    query = "&".join(part for part in (parts.query, urlencode(values)) if part)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


class AuthlibOIDCProvider:
    """Small adapter with no implicit host/redirect trust.

    Authlib provides OAuth2 code exchange and JOSE signature validation. The
    adapter separately checks OIDC claims and only permits asymmetric signing
    algorithms advertised by the exact configured issuer.
    """

    def __init__(self, config: OIDCConfig, *, timeout_seconds: float = 8.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._metadata: dict[str, object] | None = None

    async def _get_metadata(self) -> dict[str, object]:
        if self._metadata is not None:
            return self._metadata
        issuer = self.config.issuer.rstrip("/")
        discovery_url = issuer + "/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            status, metadata = await _bounded_json(
                client, "GET", discovery_url, limit=_MAX_DISCOVERY_BYTES,
                headers={"Accept": "application/json"},
            )
            if status != 200:
                raise AuthRejected("identity provider discovery failed")
        if metadata.get("issuer") != self.config.issuer:
            raise AuthRejected("identity provider issuer did not match configuration")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = metadata.get(name)
            parsed = urlsplit(value) if isinstance(value, str) else None
            if (parsed is None or parsed.scheme != "https" or not parsed.netloc
                    or parsed.username or parsed.password or parsed.fragment):
                raise AuthRejected("identity provider endpoint is invalid")
        self._metadata = metadata
        return metadata

    async def authorization_endpoint(self) -> str:
        return str((await self._get_metadata())["authorization_endpoint"])

    async def exchange_and_verify(self, code: str, verifier: str, nonce: str) -> Mapping[str, object]:
        metadata = await self._get_metadata()
        try:
            from joserfc import jwt
            from joserfc.jwk import KeySet
        except ImportError as exc:
            raise AuthRejected("OIDC support requires the Authlib JOSE dependency") from exc

        token_endpoint = str(metadata["token_endpoint"])
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                status, token = await _bounded_json(
                    client, "POST", token_endpoint, limit=_MAX_TOKEN_BYTES,
                    data={
                        "grant_type": "authorization_code", "code": code,
                        "redirect_uri": self.config.redirect_uri,
                        "code_verifier": verifier, "client_id": self.config.client_id,
                    },
                    auth=httpx.BasicAuth(self.config.client_id, self.config.client_secret),
                    headers={"Accept": "application/json"},
                )
                if status != 200:
                    raise AuthRejected("identity provider code exchange failed")
        except Exception as exc:
            if isinstance(exc, AuthRejected):
                raise
            raise AuthRejected("identity provider code exchange failed") from exc
        id_token = token.get("id_token") if isinstance(token, Mapping) else None
        if not isinstance(id_token, str) or len(id_token) > 32768:
            raise AuthRejected("identity provider returned no valid ID token")

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                jwks_status, jwks = await _bounded_json(
                    client, "GET", str(metadata["jwks_uri"]), limit=_MAX_DISCOVERY_BYTES,
                    headers={"Accept": "application/json"},
                )
                if jwks_status != 200:
                    raise AuthRejected("identity provider signing keys are unavailable")
            key_set = KeySet.import_key_set(jwks)
            decoded = jwt.decode(id_token, key_set, algorithms=list(_ALLOWED_ALGORITHMS))
            # joserfc verifies the signature. Enforce all relevant OIDC
            # claims here, including the transaction nonce.
            claims = decoded.claims
            algorithm = decoded.header.get("alg")
            if algorithm not in _ALLOWED_ALGORITHMS:
                raise AuthRejected("identity token signing algorithm is not permitted")
            issuer = claims.get("iss")
            audience = claims.get("aud")
            subject = claims.get("sub")
            exp = claims.get("exp")
            issued = claims.get("iat")
            not_before = claims.get("nbf")
            if issuer != self.config.issuer or not isinstance(subject, str) or not subject or len(subject) > 2048:
                raise AuthRejected("identity token issuer or subject is invalid")
            if not (audience == self.config.client_id or isinstance(audience, list) and self.config.client_id in audience):
                raise AuthRejected("identity token audience is invalid")
            if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.config.client_id:
                raise AuthRejected("identity token authorized party is invalid")
            if claims.get("azp") is not None and claims.get("azp") != self.config.client_id:
                raise AuthRejected("identity token authorized party is invalid")
            claimed_nonce = claims.get("nonce")
            if not (isinstance(claimed_nonce, str) and hmac.compare_digest(claimed_nonce, nonce)):
                raise AuthRejected("identity token nonce is invalid")
            now = time.time()
            if not isinstance(exp, (int, float)) or not math.isfinite(exp) or exp <= now - 60:
                raise AuthRejected("identity token has expired")
            if (not isinstance(issued, (int, float)) or not math.isfinite(issued)
                    or issued > now + 60 or issued < now - 86400):
                raise AuthRejected("identity token issue time is invalid")
            if (not_before is not None and
                    (not isinstance(not_before, (int, float)) or not math.isfinite(not_before)
                     or not_before > now + 60)):
                raise AuthRejected("identity token is not yet valid")
            return dict(claims)
        except AuthRejected:
            raise
        except Exception as exc:
            raise AuthRejected("identity token could not be verified") from exc
