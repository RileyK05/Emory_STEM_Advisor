"""Admin-owned debug sessions. Tokens are bearer secrets stored as digests."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.admin.store import AdminStore
from app.auth.models import Principal
from app.config import Settings
from app.db import queries
from app.debug.registry import (
    OverrideError,
    validate_retrieval,
    validate_settings,
)
from app.retrieval.config import RetrievalConfig


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass(frozen=True)
class AppliedDebug:
    settings: Settings
    config: RetrievalConfig
    system_prompt: str | None
    session_id: UUID
    overrides: dict
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    def snapshot(self) -> dict:
        return {"settings": self.overrides.get("settings") or {},
                "retrieval": self.overrides.get("retrieval") or {},
                "systemPrompt": self.system_prompt}


class DebugStore:
    def __init__(self, conn, principal: Principal):
        self.conn = conn
        self.principal = principal

    def _check_owner(self, row) -> None:
        if row is None:
            raise HTTPException(404, "debug session not found")
        if row["owner_workspace"] != self.principal.workspace_key:
            raise HTTPException(404, "debug session not found")
        if row["owner_user_id"] != self.principal.user_id:
            raise HTTPException(404, "debug session not found")

    def _audit(self, action: str, session_id: UUID, detail: dict) -> None:
        AdminStore(self.conn, self.principal).record_audit(
            action, "debug_session", str(session_id), detail)

    def create(self, *, overrides: dict, system_prompt: str | None,
               settings: Settings, config: RetrievalConfig,
               ttl_seconds: int) -> tuple[dict, str]:
        if not isinstance(overrides, dict) or set(overrides) - {"settings", "retrieval"}:
            raise OverrideError("overrides may contain only 'settings' and 'retrieval'")
        settings_overrides = overrides.get("settings") or {}
        retrieval_overrides = overrides.get("retrieval") or {}
        if not isinstance(settings_overrides, dict) or not isinstance(retrieval_overrides, dict):
            raise OverrideError("settings and retrieval overrides must be objects")
        normalized = {"settings": dict(settings_overrides),
                      "retrieval": dict(retrieval_overrides)}
        # Validate before persisting; invalid overrides never become a token.
        validate_settings(settings, normalized["settings"])
        validate_retrieval(config, normalized["retrieval"])
        prompt = system_prompt.strip() if isinstance(system_prompt, str) else None
        if prompt == "":
            prompt = None
        token = secrets.token_urlsafe(32)
        session_id = uuid4()
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        with self.conn.transaction():
            self.conn.execute(queries.get("debug_insert_session"), {
                "debug_session_id": session_id,
                "owner_user_id": self.principal.user_id,
                "owner_workspace": self.principal.workspace_key,
                "token_hash": _digest(token),
                "overrides": Jsonb(normalized),
                "system_prompt": prompt,
                "expires_at": expires,
            })
            self._audit("debug.session_created", session_id,
                        {"overrides": normalized, "hasSystemPrompt": prompt is not None})
        row = self.conn.execute(queries.get("debug_get_session"), {
            "token_hash": _digest(token),
        }).fetchone()
        return dict(row), token

    def resolve(self, token: str | None, *, settings: Settings,
                config: RetrievalConfig) -> AppliedDebug | None:
        if not token or len(token) > 256:
            return None
        row = self.conn.execute(queries.get("debug_get_session"), {
            "token_hash": _digest(token),
        }).fetchone()
        self.conn.commit()
        if row is None:
            return None
        try:
            self._check_owner(row)
        except HTTPException:
            return None
        overrides = row["overrides"] or {}
        settings_overrides = overrides.get("settings") or {}
        retrieval_overrides = overrides.get("retrieval") or {}
        if not isinstance(settings_overrides, dict) or not isinstance(retrieval_overrides, dict):
            raise OverrideError("stored debug overrides are malformed")
        effective_settings = validate_settings(settings, settings_overrides)
        effective_config = validate_retrieval(config, retrieval_overrides)
        return AppliedDebug(effective_settings, effective_config, row["system_prompt"],
                            row["debug_session_id"], overrides, row["expires_at"],
                            row["created_at"], row["updated_at"])

    def get(self, session_id: UUID) -> dict:
        row = self.conn.execute(queries.get("debug_get_session_by_id"), {
            "debug_session_id": session_id,
            "owner_workspace": self.principal.workspace_key,
            "owner_user_id": self.principal.user_id,
        }).fetchone()
        self.conn.commit()
        if row is None:
            raise HTTPException(404, "debug session not found")
        return dict(row)

    def update(self, session_id: UUID, *, overrides: dict, system_prompt: str | None,
               settings: Settings, config: RetrievalConfig) -> dict:
        if not isinstance(overrides, dict) or set(overrides) - {"settings", "retrieval"}:
            raise OverrideError("overrides may contain only 'settings' and 'retrieval'")
        settings_overrides = overrides.get("settings") or {}
        retrieval_overrides = overrides.get("retrieval") or {}
        if not isinstance(settings_overrides, dict) or not isinstance(retrieval_overrides, dict):
            raise OverrideError("settings and retrieval overrides must be objects")
        normalized = {"settings": dict(settings_overrides),
                      "retrieval": dict(retrieval_overrides)}
        validate_settings(settings, normalized["settings"])
        validate_retrieval(config, normalized["retrieval"])
        prompt = system_prompt.strip() if isinstance(system_prompt, str) else None
        if prompt == "":
            prompt = None
        row = self.conn.execute(queries.get("debug_update_session"), {
            "debug_session_id": session_id,
            "owner_workspace": self.principal.workspace_key,
            "overrides": Jsonb(normalized),
            "system_prompt": prompt,
        }).fetchone()
        if row is None:
            self.conn.commit()
            raise HTTPException(404, "debug session not found")
        self._audit("debug.session_updated", session_id,
                    {"overrides": normalized, "hasSystemPrompt": prompt is not None})
        self.conn.commit()
        return dict(row)

    def revoke(self, session_id: UUID) -> None:
        row = self.conn.execute(queries.get("debug_revoke_session"), {
            "debug_session_id": session_id,
            "owner_workspace": self.principal.workspace_key,
        }).fetchone()
        if row is None:
            self.conn.commit()
            raise HTTPException(404, "debug session not found")
        self._audit("debug.session_revoked", session_id, {})
        self.conn.commit()
