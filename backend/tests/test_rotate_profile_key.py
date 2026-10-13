from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.connections.models import EvaluationEvidence, ProfileCreate
from app.connections.repository import ConnectionProfiles
from scripts.rotate_profile_key import main, rotate


def _profile(db, key, name="rotate-cli"):
    store = ConnectionProfiles(key)
    profile = store.create(db, ProfileCreate(
        name=name, provider="openai", model_id="test-model",
        roles=frozenset({"standard"}), capabilities=frozenset({"structured_output"}),
        data_scopes=frozenset({"institutional_corpus"}), secret={"api_key": "cli-secret"},
    ))
    store.record_evaluation(db, EvaluationEvidence(
        evaluation_id=uuid4(), profile_id=profile.profile_id, profile_revision=profile.revision,
        provider=profile.provider, model_id=profile.model_id, suite_hash="c" * 64,
        status="passed", metrics={"accuracy": 1.0},
    ))
    db.commit()
    return profile


def _ciphertext(db, profile_id):
    return bytes(db.execute("SELECT secret_ciphertext FROM connection_profiles WHERE profile_id = %s",
                            (profile_id,)).fetchone()["secret_ciphertext"])


def test_dry_run_verifies_every_secret_and_writes_nothing(db):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    profile = _profile(db, old)
    before = _ciphertext(db, profile.profile_id)

    assert rotate(db, old, new, dry_run=True) >= 1

    assert _ciphertext(db, profile.profile_id) == before
    assert ConnectionProfiles(old).credentials(ConnectionProfiles(old).get(db, profile.profile_id)) == {
        "api_key": "cli-secret"}


def test_rotation_switches_the_key_and_keeps_the_profile_approved(db):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    profile = _profile(db, old)

    rotate(db, old, new, dry_run=False)

    store = ConnectionProfiles(new)
    rotated = store.get(db, profile.profile_id)
    assert store.credentials(rotated) == {"api_key": "cli-secret"}
    assert rotated.revision == profile.revision
    assert next(p for p in store.list_routes(db, suite_hash="c" * 64)
                if p.profile_id == profile.profile_id).eval_passed


def test_identical_keys_are_rejected_before_touching_the_database(db):
    key = Fernet.generate_key().decode()
    profile = _profile(db, key)
    before = _ciphertext(db, profile.profile_id)
    with pytest.raises(ValueError, match="must differ"):
        rotate(db, key, key, dry_run=False)
    assert _ciphertext(db, profile.profile_id) == before


def test_wrong_current_key_fails_without_changing_any_secret(db):
    real, wrong, new = (Fernet.generate_key().decode() for _ in range(3))
    profile = _profile(db, real)
    before = _ciphertext(db, profile.profile_id)
    with pytest.raises(Exception):  # noqa: B017 - credential unreadable under the wrong key
        rotate(db, wrong, new, dry_run=False)
    db.rollback()
    assert _ciphertext(db, profile.profile_id) == before


def test_cli_reports_counts_never_key_material_and_fails_cleanly(db, migrated_dsn, monkeypatch, capsys):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    _profile(db, old)
    monkeypatch.setenv("DATABASE_URL", migrated_dsn)
    monkeypatch.setenv("PROFILE_ENCRYPTION_KEY", old)
    monkeypatch.setenv("NEW_PROFILE_ENCRYPTION_KEY", new)

    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would re-encrypt" in out and old not in out and new not in out

    monkeypatch.setenv("PROFILE_ENCRYPTION_KEY", Fernet.generate_key().decode())  # wrong current key
    assert main([]) == 1
    captured = capsys.readouterr()
    assert "nothing was changed" in captured.err and old not in captured.err + captured.out

    monkeypatch.delenv("NEW_PROFILE_ENCRYPTION_KEY")
    assert main([]) == 2

