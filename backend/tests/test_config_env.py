"""Settings loading: the optional repo-root `.env`, DATA_DIR resolution, HF stop tokens."""

import json
import os
from dataclasses import replace

import pytest

from app.config import (
    _load_dotenv,
    default_hf_stop_token_strings,
    load_settings,
    with_hf_model,
)

MINICPM = "openbmb/MiniCPM-2B-128k"


@pytest.mark.parametrize(("target", "app", "valid"), [
    ("postgresql://user:secret@localhost/advisor", None, False),
    ("host=localhost user=advisor", None, False),
    ("postgresql://user:secret@localhost/advisor_test", None, True),
    ("dbname=advisor_test", "postgresql://other@127.0.0.1/advisor_test", False),
    ("dbname=advisor_test", "dbname=advisor", True),
])
def test_external_test_database_must_be_disposable_and_separate(target, app, valid):
    from tests.conftest import _validate_test_database

    if valid:
        _validate_test_database(target, app)
    else:
        with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
            _validate_test_database(target, app)


@pytest.fixture
def repo_root(monkeypatch, tmp_path):
    # _load_dotenv reads REPO_ROOT/.env; point the module at a temp root.
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "_REPO_ROOT", tmp_path)
    return tmp_path


def test_dotenv_fills_gaps_strips_quotes_and_never_overrides_the_shell(repo_root, monkeypatch):
    for key in ("ADVISOR_FILE", "ADVISOR_Q", "ADVISOR_E"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ADVISOR_SHELL", "from-shell")
    (repo_root / ".env").write_text(
        "# a comment\nADVISOR_FILE=from-file\n\nADVISOR_SHELL=from-file\n"
        'ADVISOR_Q="quoted value"\nexport ADVISOR_E=exported\n', encoding="utf-8")
    _load_dotenv()
    assert os.environ["ADVISOR_FILE"] == "from-file"
    assert os.environ["ADVISOR_SHELL"] == "from-shell"
    assert os.environ["ADVISOR_Q"] == "quoted value"
    assert os.environ["ADVISOR_E"] == "exported"
    for key in ("ADVISOR_FILE", "ADVISOR_Q", "ADVISOR_E"):
        monkeypatch.delenv(key)  # _load_dotenv wrote these outside monkeypatch's tracking


def test_missing_dotenv_is_a_noop(repo_root, monkeypatch):
    monkeypatch.delenv("ADVISOR_NOPE", raising=False)
    _load_dotenv()
    assert "ADVISOR_NOPE" not in os.environ


def test_relative_data_dir_uses_same_database_from_root_and_backend(repo_root, monkeypatch):
    monkeypatch.setenv("DATA_DIR", "./data")
    backend_dir = repo_root / "backend"
    backend_dir.mkdir()
    for working_dir in (repo_root, backend_dir):
        monkeypatch.chdir(working_dir)
        assert load_settings().data_dir == repo_root / "data"
    absolute_dir = repo_root / "custom-store"
    monkeypatch.setenv("DATA_DIR", str(absolute_dir))
    assert load_settings().data_dir == absolute_dir


def test_hf_stop_tokens_default_per_model_and_honor_an_explicit_empty_list(monkeypatch):
    monkeypatch.delenv("HF_STOP_TOKEN_STRINGS", raising=False)
    monkeypatch.setenv("HF_MODEL_ID", MINICPM)
    assert load_settings().hf_stop_token_strings == ("<|im_end|>",)
    assert replace(load_settings(), hf_stop_token_strings=None).hf_stop_token_strings == (
        "<|im_end|>",)
    monkeypatch.setenv("HF_MODEL_ID", "another/local-model")
    assert load_settings().hf_stop_token_strings == ()
    monkeypatch.setenv("HF_MODEL_ID", MINICPM)
    monkeypatch.setenv("HF_STOP_TOKEN_STRINGS", "[]")
    assert load_settings().hf_stop_token_strings == ()


@pytest.mark.parametrize("value", [
    "", "<|im_end|>", "{}", '["x", 1]',  # not a JSON list of strings
    json.dumps(["duplicate", "duplicate"]),
    json.dumps([f"token-{index}" for index in range(9)]),
    json.dumps(["x" * 129]),
    json.dumps(["   "]),
    json.dumps(["nul\u0000token"]),
])
def test_hf_stop_token_setting_rejects_malformed_or_out_of_bounds_values(value, monkeypatch):
    monkeypatch.setenv("HF_STOP_TOKEN_STRINGS", value)
    with pytest.raises(ValueError, match="HF_STOP_TOKEN_STRINGS"):
        load_settings()


def test_hf_model_override_preserves_same_model_and_resets_for_new_model(monkeypatch):
    monkeypatch.delenv("HF_STOP_TOKEN_STRINGS", raising=False)
    monkeypatch.setenv("HF_MODEL_ID", MINICPM)
    settings = load_settings()
    explicit = replace(settings, hf_stop_token_strings=("<|custom|>",))
    assert with_hf_model(explicit, explicit.hf_model_id).hf_stop_token_strings == ("<|custom|>",)
    assert with_hf_model(settings, settings.hf_model_id) is settings
    other = with_hf_model(settings, "another/local-model")
    assert other.hf_model_id == "another/local-model"
    assert other.hf_stop_token_strings == ()
    back = with_hf_model(other, MINICPM)
    assert back.hf_stop_token_strings == default_hf_stop_token_strings(back.hf_model_id)
