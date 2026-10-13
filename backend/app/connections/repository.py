"""Persistence and credential handling for server-managed provider profiles."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.connections.crypto import (
    decrypt_credentials,
    encrypt_credentials,
    validate_encryption_key,
)
from app.connections.models import (
    CredentialDiagnostic,
    CredentialUnreadable,
    EvaluationEvidence,
    MissingCredential,
    ProfileCreate,
    ProfileUpdate,
    ProfileValidationError,
    ProfileView,
    StoredProfile,
    validate_profile,
)
from app.db import queries


class ConcurrentProfileUpdate(RuntimeError):
    """The profile revision changed since the administrator loaded it."""


_SUITE_HASH = re.compile(r"^[a-f0-9]{64}$")


def _stored(row: Mapping[str, Any]) -> StoredProfile:
    return StoredProfile(
        profile_id=row["profile_id"], name=row["name"], provider=row["provider"],
        model_id=row["model_id"], base_url=row["base_url"], account_id=row["account_id"],
        secret_ciphertext=bytes(row["secret_ciphertext"]) if row["secret_ciphertext"] is not None else None,
        provider_options=dict(row["provider_options"] or {}), roles=frozenset(row["roles"]),
        capabilities=frozenset(row["capabilities"]), data_scopes=frozenset(row["data_scopes"]),
        enabled=row["enabled"], priority=row["priority"], revision=row["revision"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        eval_passed=bool(row.get("eval_passed", False)),
    )


def _view(profile: StoredProfile) -> ProfileView:
    return ProfileView(
        profile_id=profile.profile_id, name=profile.name, provider=profile.provider,
        model_id=profile.model_id, base_url=profile.base_url, account_id=profile.account_id,
        secret_configured=profile.secret_configured, provider_options=profile.provider_options,
        roles=profile.roles, capabilities=profile.capabilities, data_scopes=profile.data_scopes,
        enabled=profile.enabled, priority=profile.priority, revision=profile.revision,
        created_at=profile.created_at, updated_at=profile.updated_at,
        eval_passed=profile.eval_passed,
    )


class ConnectionProfiles:
    """Repository plus encryption boundary; methods never return plaintext secrets in profile DTOs."""

    def __init__(self, encryption_key: str | bytes | None):
        self._encryption_key = encryption_key

    def create(self, conn, data: ProfileCreate) -> ProfileView:
        validate_profile(data)
        ciphertext = self._encrypt(data.provider, data.secret)
        row = conn.execute(queries.get("insert_connection_profile"), {
            "profile_id": uuid4(), "name": data.name.strip(), "provider": data.provider,
            "model_id": data.model_id.strip(), "base_url": data.base_url,
            "account_id": data.account_id, "secret_ciphertext": ciphertext,
            "provider_options": Jsonb(dict(data.provider_options)), "roles": sorted(data.roles),
            "capabilities": sorted(data.capabilities), "data_scopes": sorted(data.data_scopes),
            "enabled": data.enabled, "priority": data.priority,
        }).fetchone()
        return _view(_stored(row))

    def get(self, conn, profile_id: UUID) -> StoredProfile | None:
        row = conn.execute(queries.get("get_connection_profile"), {"profile_id": profile_id}).fetchone()
        return _stored(row) if row else None

    def get_current_revision(self, conn, profile_id: UUID, *, lock: bool = False):
        """Read current revision, optionally holding a share lock through caller commit."""
        query_name = ("get_connection_profile_revision_for_share" if lock
                      else "get_connection_profile_revision")
        row = conn.execute(queries.get(query_name), {"profile_id": profile_id}).fetchone()
        return (bool(row["enabled"]), int(row["revision"])) if row else None

    def list_views(self, conn) -> tuple[ProfileView, ...]:
        rows = conn.execute(queries.get("list_connection_profiles")).fetchall()
        return tuple(_view(_stored(row)) for row in rows)

    def list_routes(self, conn, *, suite_hash: str) -> tuple[StoredProfile, ...]:
        if not _SUITE_HASH.fullmatch(suite_hash):
            raise ValueError("a lowercase SHA-256 eval suite hash is required for routing")
        rows = conn.execute(queries.get("list_route_profiles"), {"suite_hash": suite_hash}).fetchall()
        return tuple(_stored(row) for row in rows)

    def update(self, conn, profile_id: UUID, data: ProfileUpdate) -> ProfileView:
        if data.expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        validate_profile(data)
        row = conn.execute(queries.get("update_connection_profile"), {
            "profile_id": profile_id, "expected_revision": data.expected_revision,
            "name": data.name.strip(), "provider": data.provider, "model_id": data.model_id.strip(),
            "base_url": data.base_url, "account_id": data.account_id,
            "provider_options": Jsonb(dict(data.provider_options)), "roles": sorted(data.roles),
            "capabilities": sorted(data.capabilities), "data_scopes": sorted(data.data_scopes),
            "enabled": data.enabled, "priority": data.priority,
            "now": datetime.now(UTC),
        }).fetchone()
        if row is None:
            raise ConcurrentProfileUpdate("profile was changed or deleted; reload it and retry")
        return _view(_stored(row))

    def replace_secret(self, conn, profile_id: UUID, *, expected_revision: int,
                       secret: Mapping[str, str]) -> ProfileView:
        current = self.get(conn, profile_id)
        if current is None:
            raise KeyError("connection profile does not exist")
        if current.revision != expected_revision:
            raise ConcurrentProfileUpdate("profile was changed; reload it before replacing credentials")
        ciphertext = self._encrypt(current.provider, secret)
        row = conn.execute(queries.get("replace_connection_profile_secret"), {
            "profile_id": profile_id, "expected_revision": expected_revision,
            "secret_ciphertext": ciphertext, "now": datetime.now(UTC),
        }).fetchone()
        if row is None:
            raise ConcurrentProfileUpdate("profile was changed or deleted; reload it and retry")
        return _view(_stored(row))

    def clear_secret(self, conn, profile_id: UUID, *, expected_revision: int) -> ProfileView:
        """Delete stored credentials with optimistic revision control."""
        row = conn.execute(queries.get("clear_connection_profile_secret"), {
            "profile_id": profile_id, "expected_revision": expected_revision,
            "now": datetime.now(UTC),
        }).fetchone()
        if row is None:
            raise ConcurrentProfileUpdate("profile was changed or deleted; reload it and retry")
        return _view(_stored(row))

    def delete(self, conn, profile_id: UUID, *, expected_revision: int) -> bool:
        row = conn.execute(queries.get("delete_connection_profile"), {
            "profile_id": profile_id, "expected_revision": expected_revision,
        }).fetchone()
        if row is None:
            raise ConcurrentProfileUpdate("profile was changed or deleted; reload it and retry")
        return True

    def credentials(self, profile: StoredProfile) -> Mapping[str, str]:
        if profile.provider == "hf-local":
            return {}
        if profile.secret_ciphertext is None:
            raise MissingCredential(f"profile {profile.name!r} has no provider credential configured")
        return decrypt_credentials(profile.secret_ciphertext, self._encryption_key,
                                   provider=profile.provider)

    def diagnose(self, profile: StoredProfile) -> CredentialDiagnostic:
        if profile.provider == "hf-local":
            return CredentialDiagnostic("not_required", "This local provider does not require an API credential.")
        if profile.secret_ciphertext is None:
            return CredentialDiagnostic("missing", "No provider credential is configured.")
        try:
            self.credentials(profile)
        except CredentialUnreadable:
            return CredentialDiagnostic("unreadable", "A credential is stored but cannot be decrypted with the active key.")
        return CredentialDiagnostic("configured", "Provider credential is available.")

    def record_evaluation(self, conn, evidence: EvaluationEvidence) -> None:
        if not _SUITE_HASH.fullmatch(evidence.suite_hash):
            raise ValueError("suite_hash must be a lowercase SHA-256 hash")
        # The caller supplies a result produced by the eval runner. This method
        # binds it to the exact profile revision/provider/model; it never marks
        # a profile as passed by inference.
        json.dumps(dict(evidence.metrics), allow_nan=False)
        row = conn.execute(queries.get("insert_connection_profile_evaluation"), {
            "evaluation_id": evidence.evaluation_id, "profile_id": evidence.profile_id,
            "profile_revision": evidence.profile_revision, "provider": evidence.provider,
            "model_id": evidence.model_id, "suite_hash": evidence.suite_hash,
            "status": evidence.status, "metrics": Jsonb(dict(evidence.metrics)),
        }).fetchone()
        if row is None:
            raise ConcurrentProfileUpdate("evaluation evidence does not match the current profile revision")

    def rotate_encryption_key(self, conn, new_key: str | bytes, *, dry_run: bool = False) -> int:
        """Re-encrypt all stored credentials atomically and switch this store to the new key.

        With `dry_run`, every stored secret is decrypted with the current key and
        re-encrypted with the new one, but nothing is written and the key is kept.
        """
        validate_encryption_key(new_key)
        rows = conn.execute(queries.get("read_connection_profile_secrets")).fetchall()
        conn.commit()
        decrypted = []
        for row in rows:
            profile_id, revision = row["profile_id"], row["revision"]
            clear = decrypt_credentials(row["secret_ciphertext"], self._encryption_key,
                                        provider=row["provider"])
            encrypted = encrypt_credentials(clear, new_key, provider=row["provider"])
            decrypted.append((profile_id, revision, encrypted))
        if dry_run:
            return len(decrypted)
        now = datetime.now(UTC)
        with conn.transaction():
            for profile_id, revision, ciphertext in decrypted:
                row = conn.execute(queries.get("rotate_connection_profile_secret"), {
                    "profile_id": profile_id, "expected_revision": revision,
                    "secret_ciphertext": ciphertext, "now": now,
                }).fetchone()
                if row is None:
                    raise ConcurrentProfileUpdate("profile changed during encryption key rotation")
        self._encryption_key = new_key
        return len(decrypted)

    def _encrypt(self, provider: str, secret: Mapping[str, str] | None) -> bytes | None:
        if provider == "hf-local":
            if secret:
                raise ProfileValidationError("hf-local profiles do not accept credentials")
            return None
        if secret is None:
            return None
        return encrypt_credentials(secret, self._encryption_key, provider=provider)
