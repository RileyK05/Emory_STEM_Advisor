from dataclasses import replace

import pytest

import app.runtime as runtime_module
from app.config import load_settings
from app.runtime import build_connections, build_provider, build_runtime
from tests.fakes import FakeEmbedder, FakeProvider


@pytest.fixture
def settings():
    return load_settings()


def test_provider_construction_failure_is_reported_by_type_only(monkeypatch, settings):
    def explode(_settings):
        raise RuntimeError("Authorization: Bearer sk-live-secret")

    monkeypatch.setattr(runtime_module, "get_provider", explode)
    provider, error = build_provider(settings)
    assert provider is None
    assert error == "RuntimeError"  # never the message, which could carry credentials


def test_worker_skips_the_static_provider_when_routing_serves_requests(monkeypatch, settings):
    calls = []
    monkeypatch.setattr(runtime_module, "get_provider", lambda s: calls.append(s) or FakeProvider())
    routed = replace(settings, routing_enabled=True)

    assert build_provider(routed, skip_when_routing=True) == (None, None)
    assert calls == []
    # The API still builds it (as a fallback) even with routing enabled.
    provider, error = build_provider(routed)
    assert isinstance(provider, FakeProvider) and error is None and calls == [routed]


def test_injected_parts_are_used_as_given_and_connections_are_built_once(settings):
    embedder, provider = FakeEmbedder(dim=8), FakeProvider()
    built = build_runtime(settings, embedder=embedder, provider=provider)
    assert built.embedder is embedder and built.provider is provider
    assert built.provider_error is None and built.settings is settings
    # The profile store and runtime are one pair, so callers share provider caches.
    assert built.connection_runtime is not None and built.connection_profiles is not None
    profiles, connection_runtime = build_connections(settings)
    assert profiles is not built.connection_profiles  # each build is its own pair
    assert connection_runtime is not built.connection_runtime
