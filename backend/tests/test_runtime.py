from dataclasses import replace

import pytest

import app.runtime as runtime_module
from app.config import load_settings
from app.runtime import build_provider
from tests.fakes import FakeProvider


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
