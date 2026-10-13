"""Runtime registry for evaluated, scoped provider connections."""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID

from app.config import with_hf_model
from app.connections.models import ConnectionUnavailable, StoredProfile
from app.llm.factory import get_batch_provider, get_decision_provider, get_provider
from app.llm.routing import Candidate


class ConnectionRuntime:
    """Builds clients lazily outside DB transactions and caches per profile revision."""

    def __init__(self, settings: Any, profiles):
        self._settings = settings
        self._profiles = profiles
        self._lock = threading.RLock()
        self._cache: dict[tuple[UUID, int, bool], Any] = {}
        self._batch_cache: dict[tuple[UUID, int], Any] = {}

    def invalidate(self, profile_id: UUID | None = None) -> None:
        """Evict provider instances after an administrator changes a profile."""
        with self._lock:
            if profile_id is None:
                self._cache.clear()
                self._batch_cache.clear()
            else:
                for key in tuple(self._cache):
                    if key[0] == profile_id:
                        self._cache.pop(key, None)
                for key in tuple(self._batch_cache):
                    if key[0] == profile_id:
                        self._batch_cache.pop(key, None)

    @staticmethod
    def _eligible(profile: StoredProfile, *, role: str, data_scope: str,
                  required_capabilities: frozenset[str]) -> None:
        if not profile.enabled or not profile.eval_passed:
            raise ConnectionUnavailable("profile must be enabled and pass the current evaluation suite")
        if role not in profile.roles:
            raise ConnectionUnavailable(f"profile is not approved for role {role!r}")
        if data_scope not in profile.data_scopes:
            raise ConnectionUnavailable(f"profile is not authorized for data scope {data_scope!r}")
        if not required_capabilities <= profile.capabilities:
            raise ConnectionUnavailable("profile lacks one or more required capabilities")

    def generation_provider(self, profile: StoredProfile, *, role: str,
                            data_scope: str, required_capabilities: frozenset[str] = frozenset()):
        self._eligible(profile, role=role, data_scope=data_scope,
                       required_capabilities=required_capabilities)
        if profile.provider in {"openai-decisions", "cloudflare-clef"}:
            raise ConnectionUnavailable("decision-only profiles cannot generate text")
        return self._get(profile, decision=False)

    def decision_provider(self, profile: StoredProfile, *, data_scope: str,
                          required_capabilities: frozenset[str] = frozenset({"decision"})):
        self._eligible(profile, role="decision", data_scope=data_scope,
                       required_capabilities=required_capabilities)
        if profile.provider not in {"openai-decisions", "cloudflare-clef"}:
            raise ConnectionUnavailable("profile does not provide native decisions")
        return self._get(profile, decision=True)

    def batch_provider(self, profile: StoredProfile, *, role: str, data_scope: str,
                       required_capabilities: frozenset[str] = frozenset({"batch", "structured_output"})):
        """Build/cache a native batch adapter only for an evaluated eligible profile."""
        self._eligible(profile, role=role, data_scope=data_scope,
                       required_capabilities=required_capabilities)
        if role not in {"fast_generation", "complex"}:
            raise ConnectionUnavailable("native batches require a fast or complex generation route")
        if profile.provider not in {"openai", "anthropic", "google"}:
            raise ConnectionUnavailable("this provider has no configured native batch adapter")
        key = (profile.profile_id, profile.revision)
        with self._lock:
            cached = self._batch_cache.get(key)
            if cached is not None:
                return cached
            secret = self._profiles.credentials(profile)
            adapter = get_batch_provider(
                profile.provider, model=profile.model_id,
                api_key=secret.get("api_key"),
                timeout_seconds=getattr(self._settings, "llm_timeout_seconds", 60.0),
            )
            for old_key in tuple(self._batch_cache):
                if old_key[0] == profile.profile_id:
                    self._batch_cache.pop(old_key, None)
            self._batch_cache[key] = adapter
            return adapter

    def existing_batch_provider(self, profile: StoredProfile, *, expected_provider: str):
        """Rebuild read/cancel access for a persisted batch after profile rotation.

        This does not authorize new submissions or publication. The caller must
        already have loaded the batch in the authenticated workspace and verify
        its profile ID/provider binding. Publication separately checks revision.
        """
        if profile.provider != expected_provider or profile.provider not in {
            "openai", "anthropic", "google",
        }:
            raise ConnectionUnavailable("stored provider batch connection is unavailable")
        key = (profile.profile_id, profile.revision)
        with self._lock:
            cached = self._batch_cache.get(key)
            if cached is not None:
                return cached
            secret = self._profiles.credentials(profile)
            adapter = get_batch_provider(
                profile.provider, model=profile.model_id,
                api_key=secret.get("api_key"),
                timeout_seconds=getattr(self._settings, "llm_timeout_seconds", 60.0),
            )
            self._batch_cache[key] = adapter
            return adapter

    def _get(self, profile: StoredProfile, *, decision: bool):
        key = (profile.profile_id, profile.revision, decision)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            # Credentials are decrypted only after all authorization/evaluation checks.
            secret = self._profiles.credentials(profile) if profile.provider != "hf-local" else {}
            if decision:
                provider = ("openai" if profile.provider == "openai-decisions" else "cloudflare")
                client = get_decision_provider(
                    provider, model=profile.model_id,
                    api_key=secret.get("api_key") or secret.get("api_token"),
                    account_id=profile.account_id, endpoint=profile.base_url,
                )
            else:
                settings = self._settings
                if profile.provider == "hf-local":
                    try:
                        settings = with_hf_model(settings, profile.model_id)
                    except TypeError:
                        values = vars(settings).copy()
                        values["hf_model_id"] = profile.model_id
                        values["hf_stop_token_strings"] = ()
                        settings = type("ProfileSettings", (), values)()
                client = get_provider(
                    settings, provider=profile.provider, model=profile.model_id,
                    api_key=secret.get("api_key"), base_url=profile.base_url,
                )
            # A new revision supersedes prior instances for this profile.
            for old_key in tuple(self._cache):
                if old_key[0] == profile.profile_id:
                    self._cache.pop(old_key, None)
            self._cache[key] = client
            return client

    @staticmethod
    def candidates(profiles: tuple[StoredProfile, ...]) -> tuple[Candidate, ...]:
        """Convert DB ordered profiles to deterministic router candidates."""
        return tuple(Candidate(
            profile_id=str(p.profile_id), role=role, provider=p.provider,
            model=p.model_id, capabilities=frozenset(p.capabilities),
            data_scopes=frozenset(p.data_scopes), eval_passed=p.eval_passed,
        ) for p in profiles if p.enabled for role in sorted(p.roles))
