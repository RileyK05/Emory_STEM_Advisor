from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.connections.models import EvaluationEvidence, ProfileCreate
from app.connections.repository import ConcurrentProfileUpdate, ConnectionProfiles


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

