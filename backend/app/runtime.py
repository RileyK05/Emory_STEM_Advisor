"""The one place the API and the job worker build their model, embedding, and connection seams.

Everything goes through the provider and embedder factories; nothing here (or
in callers) imports a concrete provider. Construction failures of the static
provider are captured as an exception *type name* only: SDK exception text can
contain request headers or credentials and must never be surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, load_settings
from app.connections.registry import ConnectionRuntime
from app.connections.repository import ConnectionProfiles
from app.embeddings import get_embedder
from app.embeddings.base import Embedder
from app.llm import get_provider
from app.llm.base import Provider
from app.retrieval.config import RetrievalConfig, load_retrieval_config


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    config: RetrievalConfig
    embedder: Embedder
    # None when no static provider is configured or its construction failed
    # (see `provider_error`); evaluated connection routing may still serve requests.
    provider: Provider | None
    provider_error: str | None
    connection_profiles: ConnectionProfiles
    connection_runtime: ConnectionRuntime


def build_connections(settings: Settings) -> tuple[ConnectionProfiles, ConnectionRuntime]:
    profiles = ConnectionProfiles(settings.profile_encryption_key)
    return profiles, ConnectionRuntime(settings, profiles)


def build_provider(settings: Settings, *, skip_when_routing: bool = False) -> tuple[Provider | None, str | None]:
    """The statically configured provider and, if building it failed, the error's type name.

    A missing or unreadable selected connection must leave import, export, source
    inspection, and connection settings usable, so a failure is reported, not raised.
    `skip_when_routing` is for the worker, which serves only through evaluated routing
    when that is enabled.
    """
    if skip_when_routing and settings.routing_enabled:
        return None, None
    try:
        return get_provider(settings), None
    except Exception as exc:  # noqa: BLE001 - SDK construction boundary stays feature-local
        return None, type(exc).__name__


def build_runtime(
    settings: Settings | None = None,
    *,
    config: RetrievalConfig | None = None,
    embedder: Embedder | None = None,
    provider: Provider | None = None,
    skip_provider_when_routing: bool = False,
) -> Runtime:
    """Assemble the shared seams; anything passed in (tests, embedding callers) is used as given."""
    settings = settings or load_settings()
    provider_error = None
    if provider is None:
        provider, provider_error = build_provider(settings, skip_when_routing=skip_provider_when_routing)
    profiles, connection_runtime = build_connections(settings)
    return Runtime(
        settings=settings,
        config=config or load_retrieval_config(settings=settings),
        embedder=embedder or get_embedder(settings),
        provider=provider,
        provider_error=provider_error,
        connection_profiles=profiles,
        connection_runtime=connection_runtime,
    )
