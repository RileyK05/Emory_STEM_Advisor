"""Authentication provider seam behind ``get_principal``.

The OIDC/development ``AuthService`` is the production implementation. A fake
provider can mint sessions for tests and load runs without real credentials.
The school-specific adapter is deliberately not built: its protocol is unknown.
"""

from __future__ import annotations

from typing import Protocol

from app.auth.models import Principal
from app.auth.service import LoginStart


class AuthProvider(Protocol):
    mode: str
    redirect_uri: str
    session_ttl_seconds: int

    async def begin_login(self) -> LoginStart: ...

    def state_matches(self, cookie_state: str | None, query_state: str | None) -> bool: ...

    async def complete_login(self, state: str, code: str) -> str: ...

    async def consume_failed_login(self, state: str) -> None: ...

    def resolve(self, token: str | None, *, client_host: str | None) -> Principal | None: ...

    def logout(self, token: str | None) -> None: ...
