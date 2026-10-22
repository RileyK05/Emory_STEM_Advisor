from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.connections.crypto import decrypt_credentials, encrypt_credentials
from app.connections.models import (
    ConnectionUnavailable,
    CredentialUnreadable,
    EvaluationEvidence,
    MissingCredential,
    ProfileCreate,
    ProfileValidationError,
    StoredProfile,
    validate_profile,
)
from app.connections.registry import ConnectionRuntime
from app.connections.repository import ConcurrentProfileUpdate, ConnectionProfiles, _view

KEY = Fernet.generate_key()


def _profile(**kwargs):
    values = {
        "profile_id": uuid4(), "name": "primary", "provider": "openai", "model_id": "example-model",
        "base_url": None, "account_id": None,
        "secret_ciphertext": encrypt_credentials({"api_key": "secret-key"}, KEY, provider="openai"),
        "provider_options": {}, "roles": frozenset({"standard"}),
        "capabilities": frozenset({"structured_output"}), "data_scopes": frozenset({"institutional_corpus"}),
        "enabled": True, "priority": 10, "revision": 1, "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC), "eval_passed": True,
    }
    values.update(kwargs)
    return StoredProfile(**values)

def test_credentials_are_authenticated_and_provider_bound():
    ciphertext = encrypt_credentials({"api_key": "never-display"}, KEY, provider="openai")
    assert decrypt_credentials(ciphertext, KEY, provider="openai")["api_key"] == "never-display"
    with pytest.raises(CredentialUnreadable):
        decrypt_credentials(ciphertext, Fernet.generate_key(), provider="openai")
    with pytest.raises(CredentialUnreadable):
        decrypt_credentials(ciphertext, KEY, provider="anthropic")


def test_profile_validation_requires_compatible_endpoint_and_safe_url():
    args = {"name": "router", "provider": "openai-compatible", "model_id": "model",
            "roles": frozenset({"standard"}), "capabilities": frozenset(),
            "data_scopes": frozenset({"institutional_corpus"})}
    with pytest.raises(ProfileValidationError):
        validate_profile(ProfileCreate(**args))
    with pytest.raises(ProfileValidationError):
        validate_profile(ProfileCreate(**args, base_url="https://user:pass@example.com/v1"))


def test_repository_reports_missing_credential_separately():
    store = ConnectionProfiles(KEY)
    profile = _profile(secret_ciphertext=None)
    assert store.diagnose(profile).status == "missing"
    with pytest.raises(MissingCredential):
        store.credentials(profile)
    assert store.diagnose(_profile(secret_ciphertext=b"bad")).status == "unreadable"


def test_runtime_enforces_evaluation_scope_role_and_capabilities_before_factory(monkeypatch):
    store = ConnectionProfiles(KEY)
    runtime = ConnectionRuntime(SimpleNamespace(), store)
    profile = _profile()
    built = []
    monkeypatch.setattr("app.connections.registry.get_provider", lambda *a, **k: built.append(k) or object())
    with pytest.raises(ConnectionUnavailable):
        runtime.generation_provider(replace(profile, eval_passed=False), role="standard",
                                    data_scope="institutional_corpus")
    with pytest.raises(ConnectionUnavailable):
        runtime.generation_provider(profile, role="complex", data_scope="institutional_corpus")
    with pytest.raises(ConnectionUnavailable):
        runtime.generation_provider(profile, role="standard", data_scope="private_workspace")
    with pytest.raises(ConnectionUnavailable):
        runtime.generation_provider(profile, role="standard", data_scope="institutional_corpus",
                                    required_capabilities=frozenset({"tools"}))
    assert not built


def test_runtime_caches_client_by_profile_revision_and_invalidates(monkeypatch):
    store = ConnectionProfiles(KEY)
    runtime = ConnectionRuntime(SimpleNamespace(), store)
    built = []
    monkeypatch.setattr("app.connections.registry.get_provider", lambda *a, **k: built.append(k) or object())
    profile = _profile()
    first = runtime.generation_provider(profile, role="standard", data_scope="institutional_corpus")
    assert runtime.generation_provider(profile, role="standard", data_scope="institutional_corpus") is first
    assert len(built) == 1
    revised = replace(profile, revision=2)
    second = runtime.generation_provider(revised, role="standard", data_scope="institutional_corpus")
    assert second is not first and len(built) == 2
    runtime.invalidate(profile.profile_id)
    third = runtime.generation_provider(revised, role="standard", data_scope="institutional_corpus")
    assert third is not second and len(built) == 3


def test_public_profile_view_and_request_repr_never_include_secret():
    create = ProfileCreate(
        name="safe", provider="openai", model_id="test", roles=frozenset({"standard"}),
        capabilities=frozenset(), data_scopes=frozenset({"institutional_corpus"}),
        secret={"api_key": "do-not-print"},
    )
    assert "do-not-print" not in repr(create)
    view = _view(_profile())
    assert view.secret_configured
    assert "secret" not in view.__dataclass_fields__
    assert "secret-key" not in repr(view)


def test_profiles_mask_secrets_and_only_route_after_matching_evaluation(db):
    store = ConnectionProfiles(Fernet.generate_key())
    profile = store.create(db, ProfileCreate(
        name="db-test", provider="openai", model_id="test-model",
        roles=frozenset({"standard"}), capabilities=frozenset({"structured_output"}),
        data_scopes=frozenset({"institutional_corpus"}),
        secret={"api_key": "test-secret"},
    ))
    assert profile.secret_configured
    assert not hasattr(profile, "secret")
    suite = "a" * 64
    assert not store.list_routes(db, suite_hash=suite)[0].eval_passed
    evidence = EvaluationEvidence(
        evaluation_id=uuid4(), profile_id=profile.profile_id, profile_revision=profile.revision,
        provider=profile.provider, model_id=profile.model_id, suite_hash=suite,
        status="passed", metrics={"accuracy": 1.0},
    )
    store.record_evaluation(db, evidence)
    db.commit()
    routed = next(p for p in store.list_routes(db, suite_hash=suite)
                  if p.profile_id == profile.profile_id)
    assert routed.eval_passed
    assert store.diagnose(routed).status == "configured"
    assert store.get_current_revision(db, routed.profile_id, lock=True) == (True, routed.revision)
    cleared = store.clear_secret(db, routed.profile_id, expected_revision=routed.revision)
    assert not cleared.secret_configured and cleared.revision == routed.revision + 1
    assert store.diagnose(store.get(db, routed.profile_id)).status == "missing"
    db.commit()

    stale = EvaluationEvidence(
        evaluation_id=uuid4(), profile_id=profile.profile_id, profile_revision=profile.revision,
        provider=profile.provider, model_id="some-other-model", suite_hash=suite,
        status="passed", metrics={"accuracy": 1.0},
    )
    with pytest.raises(ConcurrentProfileUpdate):
        store.record_evaluation(db, stale)
