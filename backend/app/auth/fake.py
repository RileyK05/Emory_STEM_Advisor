"""In-memory identity provider for tests and load runs.

It mints opaque sessions for arbitrary workspaces without a real login flow.
It is never selected by configuration; callers inject it into ``create_app``.
"""

from __future__ import annotations

import secrets
from uuid import uuid4

from app.auth.models import Principal
from app.auth.service import AuthRejected, LoginStart

_DEFAULT_REDIRECT_URI = "https://advisor.example.test/api/auth/callback"


class FakeAuthProvider:
    mode = "oidc"

    def __init__(
        self,
        *,
        redirect_uri: str = _DEFAULT_REDIRECT_URI,
        session_ttl_seconds: int = 3600,
    ) -> None:
        self.redirect_uri = redirect_uri
        self.session_ttl_seconds = session_ttl_seconds
        self._sessions: dict[str, Principal] = {}

    def issue(
        self,
        workspace_key: str,
        *,
        role: str = "student",
        status: str = "active",
        display_name: str | None = None,
        email: str | None = None,
        issuer: str = "https://fake-identity.example.test",
        subject: str | None = None,
    ) -> str:
        """Mint a session token for one workspace."""
        token = secrets.token_urlsafe(32)
        self._sessions[token] = Principal(
            workspace_key=workspace_key,
            issuer=issuer,
            subject=subject or workspace_key,
            display_name=display_name or workspace_key,
            email=email,
            mode="oidc",
            role=role,
            user_id=uuid4(),
            status=status,
        )
        return token

    async def begin_login(self) -> LoginStart:
        raise AuthRejected("the fake identity provider has no interactive login")

    def state_matches(self, cookie_state: str | None, query_state: str | None) -> bool:
        return False

    async def complete_login(self, state: str, code: str) -> str:
        raise AuthRejected("the fake identity provider has no interactive login")

    async def consume_failed_login(self, state: str) -> None:
        raise AuthRejected("the fake identity provider has no interactive login")

    def resolve(self, token: str | None, *, client_host: str | None) -> Principal | None:
        if not token or len(token) > 256:
            return None
        return self._sessions.get(token)

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
