"""Credential encryption with Fernet keys supplied by application Settings."""

from __future__ import annotations

import json
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

from app.connections.models import CredentialUnreadable, ProfileValidationError


def _fernet(key: str | bytes | None) -> Fernet:
    if not key:
        raise CredentialUnreadable("profile encryption key is not configured")
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError, UnicodeEncodeError):
        raise CredentialUnreadable("profile encryption key is invalid") from None


def validate_encryption_key(key: str | bytes | None) -> None:
    """Reject an absent or malformed Fernet key before storage/rotation."""
    _fernet(key)


def encrypt_credentials(secret: Mapping[str, str], key: str | bytes | None,
                        *, provider: str) -> bytes:
    allowed = {"api_token"} if provider == "cloudflare-clef" else {"api_key"}
    if provider == "hf-local":
        raise ProfileValidationError("hf-local profiles do not accept credentials")
    if not secret or set(secret) != allowed or any(not isinstance(v, str) or not v for v in secret.values()):
        expected = next(iter(allowed))
        raise ProfileValidationError(f"credential payload must contain only {expected!r}")
    serialized = json.dumps({"provider": provider, "credentials": dict(secret)},
                           separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return _fernet(key).encrypt(serialized)


def decrypt_credentials(ciphertext: bytes, key: str | bytes | None,
                        *, provider: str) -> dict[str, str]:
    if provider == "hf-local":
        raise CredentialUnreadable("hf-local does not use API credentials")
    try:
        raw = _fernet(key).decrypt(bytes(ciphertext))
        values = json.loads(raw)
    except CredentialUnreadable:
        raise
    except (InvalidToken, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise CredentialUnreadable("stored credentials cannot be decrypted") from None
    expected = {"api_token"} if provider == "cloudflare-clef" else {"api_key"}
    if (not isinstance(values, dict) or set(values) != {"provider", "credentials"}
            or values["provider"] != provider):
        raise CredentialUnreadable("stored credentials belong to a different provider or have an invalid shape")
    credentials = values["credentials"]
    if not isinstance(credentials, dict) or set(credentials) != expected or not all(
        isinstance(value, str) and value for value in credentials.values()
    ):
        raise CredentialUnreadable("stored credentials have an invalid shape")
    return credentials
