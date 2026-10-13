"""Explicit institutional membership policy and factory configuration.

Uploaded reference documents cannot activate an institution or approve identities.
"""
import base64
import binascii
import secrets
from collections.abc import Mapping

from app.auth.oidc import AuthlibOIDCProvider
from app.auth.service import AuthConfigurationError, AuthService, Membership, OIDCConfig


def membership_policy(settings):
    claim = settings.auth_membership_claim
    allowed = frozenset(settings.auth_membership_values)
    domains = frozenset(d.lower() for d in settings.auth_verified_email_domains)
    if not ((claim and allowed) or domains):
        raise AuthConfigurationError("OIDC requires an explicit institutional membership policy")

    def authorize(claims: Mapping[str, object]):
        value = claims.get(claim) if claim else None
        values = {value} if isinstance(value, str) else (
            {v for v in value if isinstance(v, str)} if isinstance(value, list) else set())
        claim_matches = bool(claim and allowed.intersection(values))
        email = claims.get("email")
        email_matches = (
            claims.get("email_verified") is True and isinstance(email, str)
            and email.count("@") == 1 and email.rsplit("@", 1)[1].lower() in domains
        )
        return Membership("verified") if claim_matches or email_matches else None

    return authorize


def build_auth_service(settings, connect, *, allow_test_client=False):
    encoded = settings.auth_server_key
    if encoded:
        try:
            key = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AuthConfigurationError("AUTH_SERVER_KEY must be a base64 encoded secret") from exc
        if len(key) < 32:
            raise AuthConfigurationError("AUTH_SERVER_KEY must decode to at least 32 bytes")
    elif settings.auth_mode == "development":
        key = secrets.token_bytes(32)
    else:
        raise AuthConfigurationError("OIDC requires a stable AUTH_SERVER_KEY")
    if settings.auth_mode == "development":
        return AuthService(connect, mode="development", server_key=key,
                           allow_test_client=allow_test_client)
    oidc = OIDCConfig(
        issuer=settings.oidc_issuer or "", client_id=settings.oidc_client_id or "",
        client_secret=settings.oidc_client_secret or "",
        redirect_uri=settings.oidc_redirect_uri or "", server_key=key,
        session_ttl_seconds=settings.auth_session_ttl_seconds,
        transaction_ttl_seconds=settings.auth_transaction_ttl_seconds,
    )
    oidc.validate()
    return AuthService(connect, mode="oidc", oidc=oidc,
                       provider=AuthlibOIDCProvider(oidc),
                       membership_policy=membership_policy(settings))
