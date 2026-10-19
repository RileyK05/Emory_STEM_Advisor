"""OIDC authorization-code flow and opaque server-side sessions.

The service is intentionally configured by the application factory. In OIDC
mode it requires an explicit membership policy and a fixed callback URL. The
development mode is only valid for loopback clients.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import math
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from app.auth.models import Principal
from app.db import queries

COOKIE_NAME = "__Host-emory_session"
LOGIN_COOKIE_NAME = "__Host-emory_login_state"


class AuthConfigurationError(RuntimeError):
    pass


class AuthRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    server_key: bytes = field(repr=False)
    session_ttl_seconds: int = 28800
    transaction_ttl_seconds: int = 300

    def validate(self) -> None:
        if not all((self.issuer, self.client_id, self.client_secret, self.redirect_uri)):
            raise AuthConfigurationError("OIDC issuer, client, secret, and callback are required")
        issuer = urlsplit(self.issuer)
        callback = urlsplit(self.redirect_uri)
        if issuer.scheme != "https" or not issuer.netloc or issuer.username or issuer.password or issuer.query or issuer.fragment:
            raise AuthConfigurationError("OIDC issuer must be a fixed HTTPS URL")
        if callback.scheme != "https" or not callback.netloc or callback.username or callback.password or callback.query or callback.fragment:
            raise AuthConfigurationError("OIDC callback must be a fixed HTTPS URL")
        if len(self.server_key) < 32:
            raise AuthConfigurationError("auth server key must contain at least 32 bytes")
        if not 60 <= self.transaction_ttl_seconds <= 900:
            raise AuthConfigurationError("OIDC transaction lifetime is outside allowed bounds")
        if not 300 <= self.session_ttl_seconds <= 86400:
            raise AuthConfigurationError("session lifetime is outside allowed bounds")


@dataclass(frozen=True, slots=True)
class Membership:
    workspace_key: str


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorization_url: str
    state: str
    expires_in: int


class OIDCProvider:
    """Adapter protocol implemented by the Authlib-backed provider."""

    async def authorization_endpoint(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    async def exchange_and_verify(
        self, code: str, verifier: str, nonce: str
    ) -> Mapping[str, object]:  # pragma: no cover - interface
        raise NotImplementedError


def _digest(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _now() -> datetime:
    return datetime.now(UTC)


class AuthService:
    def __init__(
        self,
        connect: Callable[[], object],
        *,
        mode: str,
        server_key: bytes | None = None,
        oidc: OIDCConfig | None = None,
        provider: OIDCProvider | None = None,
        membership_policy: Callable[[Mapping[str, object]], Membership | None] | None = None,
        admin_bootstrap_emails: tuple[str, ...] = (),
        admin_bootstrap_subjects: tuple[str, ...] = (),
        allow_test_client: bool = False,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if mode not in {"development", "oidc"}:
            raise AuthConfigurationError("auth mode must be development or oidc")
        self.connect = connect
        self.mode = mode
        self.oidc = oidc
        self.provider = provider
        self.membership_policy = membership_policy
        self.admin_bootstrap_emails = frozenset(
            value.strip().lower() for value in admin_bootstrap_emails if value.strip())
        self.admin_bootstrap_subjects = frozenset(
            value.strip() for value in admin_bootstrap_subjects if value.strip())
        self.allow_test_client = allow_test_client
        self.clock = clock
        self.server_key = oidc.server_key if oidc else server_key
        if mode == "oidc":
            if oidc is None or provider is None or membership_policy is None:
                raise AuthConfigurationError("OIDC mode requires provider and institutional membership policy")
            oidc.validate()
            self.server_key = oidc.server_key
        elif not self.server_key or len(self.server_key) < 32:
            raise AuthConfigurationError("development auth requires a server key")

    def _connection(self):
        return self.connect()

    async def begin_login(self) -> LoginStart:
        if self.mode != "oidc" or not self.oidc or not self.provider:
            raise AuthRejected("OIDC login is unavailable")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        endpoint = await self.provider.authorization_endpoint()
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise AuthConfigurationError("provider authorization endpoint must be HTTPS")
        from app.auth.oidc import append_query

        url = append_query(endpoint, {
            "response_type": "code", "client_id": self.oidc.client_id,
            "redirect_uri": self.oidc.redirect_uri, "scope": "openid email profile",
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        now = self.clock()
        conn = self._connection()
        try:
            with conn.transaction():
                conn.execute(queries.get("insert_auth_transaction"), {
                    "state_hash": _digest(self.server_key, state), "nonce": nonce,
                    "code_verifier": verifier,
                    "expires_at": now + timedelta(seconds=self.oidc.transaction_ttl_seconds),
                })
        finally:
            conn.close()
        return LoginStart(url, state, self.oidc.transaction_ttl_seconds)

    def state_matches(self, cookie_state: str | None, query_state: str | None) -> bool:
        return bool(cookie_state and query_state and cookie_state.isascii() and query_state.isascii()
                    and len(cookie_state) <= 512
                    and len(query_state) <= 512 and hmac.compare_digest(cookie_state, query_state))

    async def complete_login(self, state: str, code: str) -> str:
        if self.mode != "oidc" or not self.oidc or not self.provider or not self.membership_policy:
            raise AuthRejected("OIDC login is unavailable")
        if not state or len(state) > 512 or not code or len(code) > 8192:
            raise AuthRejected("login response is invalid")
        now = self.clock()
        conn = self._connection()
        try:
            with conn.transaction():
                row = conn.execute(queries.get("consume_auth_transaction"), {
                    "state_hash": _digest(self.server_key, state), "now": now,
                }).fetchone()
        finally:
            conn.close()
        if row is None:
            raise AuthRejected("login transaction is invalid or expired")

        claims = await self.provider.exchange_and_verify(code, row["code_verifier"], row["nonce"])
        issuer = claims.get("iss")
        subject = claims.get("sub")
        if issuer != self.oidc.issuer or not isinstance(subject, str) or not subject or len(subject) > 2048:
            raise AuthRejected("identity token claims are invalid")
        audience = claims.get("aud")
        if not (audience == self.oidc.client_id or isinstance(audience, list) and self.oidc.client_id in audience):
            raise AuthRejected("identity token claims are invalid")
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.oidc.client_id:
            raise AuthRejected("identity token claims are invalid")
        if claims.get("azp") is not None and claims.get("azp") != self.oidc.client_id:
            raise AuthRejected("identity token claims are invalid")
        claimed_nonce = claims.get("nonce")
        if not (isinstance(claimed_nonce, str) and hmac.compare_digest(claimed_nonce, row["nonce"])):
            raise AuthRejected("identity token claims are invalid")
        expires_at = claims.get("exp")
        issued_at = claims.get("iat")
        current = self.clock().timestamp()
        if (type(expires_at) not in (int, float) or not math.isfinite(expires_at)
                or expires_at <= current - 60):
            raise AuthRejected("identity token claims are invalid")
        if (type(issued_at) not in (int, float) or not math.isfinite(issued_at)
                or not current - 900 <= issued_at <= current + 60):
            raise AuthRejected("identity token claims are invalid")
        not_before = claims.get("nbf")
        if not_before is not None and (type(not_before) not in (int, float)
                or not math.isfinite(not_before) or not_before > current + 60):
            raise AuthRejected("identity token claims are invalid")
        membership = self.membership_policy(claims)
        if membership is None:
            raise AuthRejected("institutional membership is not authorized")
        # Each verified issuer/subject pair receives a stable private workspace.
        # The policy gates institutional access; it cannot choose a workspace
        # using untrusted request data.
        workspace_key = "oidc:" + hashlib.sha256(
            (issuer + "\0" + subject).encode("utf-8")
        ).hexdigest()

        # The provider adapter has already verified signature and OIDC claims.
        token = secrets.token_urlsafe(32)
        expires = now + timedelta(seconds=self.oidc.session_ttl_seconds)
        display_name = _safe_claim(claims.get("name"), 200)
        email = _safe_claim(claims.get("email"), 320)
        bootstrap = bool(
            subject in self.admin_bootstrap_subjects
            or (email and claims.get("email_verified") is True
                and email.lower() in self.admin_bootstrap_emails)
        )
        conn = self._connection()
        try:
            with conn.transaction():
                user = conn.execute(queries.get("upsert_app_user"), {
                    "user_id": uuid4(), "issuer": issuer, "subject": subject,
                    "workspace_key": workspace_key, "display_name": display_name,
                    "email": email, "bootstrap": bootstrap,
                }).fetchone()
                if user["status"] != "active":
                    raise AuthRejected("this account is disabled")
                conn.execute(queries.get("insert_auth_session"), {
                    "session_hash": _digest(self.server_key, token), "issuer": issuer,
                    "subject": subject, "workspace_key": workspace_key,
                    "display_name": display_name,
                    "email": email, "expires_at": expires,
                })
        finally:
            conn.close()
        return token

    async def consume_failed_login(self, state: str) -> None:
        if self.mode != "oidc" or not self.oidc or not state or len(state) > 512:
            raise AuthRejected("login transaction is invalid")
        conn = self._connection()
        try:
            with conn.transaction():
                row = conn.execute(queries.get("consume_auth_transaction"), {
                    "state_hash": _digest(self.server_key, state), "now": self.clock(),
                }).fetchone()
        finally:
            conn.close()
        if row is None:
            raise AuthRejected("login transaction is invalid or expired")

    def resolve(self, token: str | None, *, client_host: str | None) -> Principal | None:
        if self.mode == "development":
            if client_host == "testclient" and self.allow_test_client:
                return Principal("development", None, "development", "Local developer",
                                 None, "development", role="admin")
            try:
                if not client_host or not ipaddress.ip_address(client_host).is_loopback:
                    return None
            except ValueError:
                return None
            return Principal("development", None, "development", "Local developer",
                             None, "development", role="admin")
        if not token or len(token) > 256:
            return None
        conn = self._connection()
        try:
            row = conn.execute(queries.get("get_auth_session"), {
                "session_hash": _digest(self.server_key, token), "now": self.clock(),
            }).fetchone()
        finally:
            conn.close()
        if row is None or row["status"] != "active":
            return None
        return Principal(row["workspace_key"], row["issuer"], row["subject"],
                         row["display_name"], row["email"], "oidc",
                         role=row["role"], user_id=row["user_id"], status=row["status"])

    def logout(self, token: str | None) -> None:
        if self.mode != "oidc" or not token or len(token) > 256:
            return
        conn = self._connection()
        try:
            with conn.transaction():
                conn.execute(queries.get("revoke_auth_session"), {
                    "session_hash": _digest(self.server_key, token), "now": self.clock(),
                })
        finally:
            conn.close()


def _safe_claim(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return None
    return value.strip()
