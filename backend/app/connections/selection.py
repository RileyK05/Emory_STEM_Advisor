"""Opt-in runtime selection for evaluated, data-scoped connection profiles."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from app.connections.models import ConnectionUnavailable, StoredProfile
from app.connections.registry import ConnectionRuntime
from app.connections.repository import ConnectionProfiles
from app.llm.routing import RoutingConfig, choose_route, quality_fallback

_ROUTING_PATH = Path(__file__).resolve().parents[2] / "configs" / "routing.toml"
_ROLES = frozenset({"standard", "complex", "fast_generation", "decision"})


class StaleProfileSelection(RuntimeError):
    """The selected profile changed while a provider request was in flight."""


@dataclass(frozen=True)
class ResolvedConnection:
    provider: Any
    settings: Any
    role: str
    profile_id: UUID
    profile_revision: int
    reason: str
    profile: StoredProfile
    _profiles: ConnectionProfiles

    def assert_current(self, conn, *, lock: bool = True, commit: bool = False) -> None:
        """Check revision before publication, optionally fencing it until caller commit.

        With `lock=True`, the caller must commit or rollback after writing usage and
        publishing any durable result. That transaction retains a share lock on the
        selected profile so concurrent profile edits cannot overtake publication.
        """
        current = self._profiles.get_current_revision(conn, self.profile_id, lock=lock)
        if commit:
            conn.commit()
        if current is None or not current[0] or current[1] != self.profile_revision:
            raise StaleProfileSelection("selected model connection changed during generation")


def _routing_config(path: str | Path | None = None) -> RoutingConfig:
    """The validated routing config; re-read only when the file changes."""
    resolved = Path(path or _ROUTING_PATH)
    return _load_routing_config(resolved, resolved.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_routing_config(path: Path, _mtime_ns: int) -> RoutingConfig:
    config = RoutingConfig.load(path)
    if config.default_role not in _ROLES or config.uncertain_role not in _ROLES:
        raise ValueError("routing roles must be supported connection roles")
    if config.complex_token_threshold <= 0:
        raise ValueError("routing complex_token_threshold must be positive")
    if any(not isinstance(value, str) or not value for value in (
        *config.complex_terms, *config.fast_job_kinds, *config.decision_job_kinds
    )):
        raise ValueError("routing signals must be non-empty strings")
    return config


def resolve(settings, conn, query: str, *, job_kind: str | None = None,
            difficulty: str | None = None, data_scope: str,
            required_capabilities=frozenset(), required_data_scopes=None,
            profiles: ConnectionProfiles | None = None,
            runtime: ConnectionRuntime | None = None,
            config_path: str | Path | None = None) -> ResolvedConnection | None:
    """Select an eval-approved profile when dynamic routing is explicitly enabled.

    `None` means routing is disabled and the caller may use the statically
    configured provider already wired by the app. When enabled, absence of an
    eligible evaluated profile is an error; there is no environment-key fallback.
    """
    if not getattr(settings, "routing_enabled", False):
        return None
    suite_hash = getattr(settings, "routing_eval_suite_hash", None)
    if not suite_hash:
        raise ConnectionUnavailable("routing requires a configured evaluation suite hash")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("routing query must contain text")
    if data_scope not in {"institutional_corpus", "user_supplied_data", "private_workspace"}:
        raise ValueError("unsupported routing data scope")
    required = frozenset(required_capabilities)
    required_scopes = frozenset(required_data_scopes) if required_data_scopes is not None else frozenset({data_scope})
    if not required_scopes <= {"institutional_corpus", "user_supplied_data", "private_workspace"}:
        raise ValueError("unsupported required routing data scope")
    routing = _routing_config(config_path)
    repository = profiles or ConnectionProfiles(getattr(settings, "profile_encryption_key", None))
    ordered_profiles = repository.list_routes(conn, suite_hash=suite_hash)
    # End the read transaction before the router decrypts credentials or builds SDK clients.
    conn.commit()
    candidates = ConnectionRuntime.candidates(ordered_profiles)
    try:
        decision = choose_route(routing, candidates, query=query, difficulty=difficulty,
                                job_kind=job_kind, required_capabilities=required,
                                data_scope=data_scope, required_data_scopes=required_scopes)
    except LookupError as exc:
        raise ConnectionUnavailable(str(exc)) from None
    selected = next((p for p in ordered_profiles
                     if str(p.profile_id) == decision.candidate.profile_id), None)
    if selected is None:
        raise ConnectionUnavailable("selected connection profile is no longer available")
    selected_runtime = runtime or ConnectionRuntime(settings, repository)
    if decision.role == "decision":
        provider = selected_runtime.decision_provider(
            selected, data_scope=data_scope, required_capabilities=required)
        settings_provider = selected.provider
    else:
        provider = selected_runtime.generation_provider(
            selected, role=decision.role, data_scope=data_scope,
            required_capabilities=required)
        settings_provider = selected.provider
    try:
        selected_settings = replace(settings, llm_provider=settings_provider,
                                    llm_model_id=selected.model_id,
                                    llm_sampling_policy=selected.provider_options.get("sampling_policy", "greedy"),
                                    llm_reasoning_effort=selected.provider_options.get("reasoning_effort"))
    except TypeError:
        values = vars(settings).copy()
        values.update(llm_provider=settings_provider, llm_model_id=selected.model_id,
                      llm_sampling_policy=selected.provider_options.get("sampling_policy", "greedy"),
                      llm_reasoning_effort=selected.provider_options.get("reasoning_effort"))
        selected_settings = type("ResolvedSettings", (), values)()
    return ResolvedConnection(provider=provider, settings=selected_settings,
        role=decision.role, profile_id=selected.profile_id,
        profile_revision=selected.revision, reason=decision.reason,
        profile=selected, _profiles=repository)


def resolve_quality_fallback(settings, conn, query: str, *, chosen: ResolvedConnection,
                             data_scope: str, required_capabilities=frozenset(),
                             required_data_scopes=None,
                             profiles: ConnectionProfiles | None = None,
                             runtime: ConnectionRuntime | None = None,
                             config_path: str | Path | None = None) -> ResolvedConnection | None:
    """Resolve one distinct, eval-approved complex route under the same scope gate."""
    routing = _routing_config(config_path)
    if routing.max_quality_escalations == 0 or chosen.role != "standard":
        return None
    suite_hash = getattr(settings, "routing_eval_suite_hash", None)
    if not suite_hash:
        return None
    required = frozenset(required_capabilities)
    required_scopes = (frozenset(required_data_scopes) if required_data_scopes is not None
                       else frozenset({data_scope}))
    repository = profiles or chosen._profiles
    ordered_profiles = repository.list_routes(conn, suite_hash=suite_hash)
    conn.commit()
    candidates = ConnectionRuntime.candidates(ordered_profiles)
    chosen_candidate = next((candidate for candidate in candidates
                             if candidate.profile_id == str(chosen.profile_id)
                             and candidate.role == chosen.role), None)
    if chosen_candidate is None:
        return None
    candidates = tuple(candidate for candidate in candidates
                       if candidate.profile_id != str(chosen.profile_id))
    fallback = quality_fallback(
        routing, chosen_candidate, candidates,
        required_capabilities=required, data_scope=data_scope,
        required_data_scopes=required_scopes,
    )
    if fallback is None:
        return None
    selected = next((profile for profile in ordered_profiles
                     if str(profile.profile_id) == fallback.profile_id), None)
    if selected is None:
        return None
    selected_runtime = runtime or ConnectionRuntime(settings, repository)
    provider = selected_runtime.generation_provider(
        selected, role="complex", data_scope=data_scope,
        required_capabilities=required,
    )
    values = {
        "llm_provider": selected.provider,
        "llm_model_id": selected.model_id,
        "llm_sampling_policy": selected.provider_options.get("sampling_policy", "greedy"),
        "llm_reasoning_effort": selected.provider_options.get("reasoning_effort"),
    }
    try:
        selected_settings = replace(settings, **values)
    except TypeError:
        settings_values = vars(settings).copy()
        settings_values.update(values)
        selected_settings = type("ResolvedSettings", (), settings_values)()
    return ResolvedConnection(
        provider=provider, settings=selected_settings, role="complex",
        profile_id=selected.profile_id, profile_revision=selected.revision,
        reason="one bounded quality escalation after output-integrity rejection",
        profile=selected, _profiles=repository,
    )
