"""Unit tests for the provider seam. These must stay fast: no model loading."""

import pytest

from app.config import load_settings
from app.llm import get_provider
from app.llm.hf_local import HFLocalProvider


def test_default_settings_are_local_experiment_config(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("HF_MODEL_ID", raising=False)
    settings = load_settings()
    assert settings.llm_provider == "hf-local"
    assert settings.hf_model_id == "openbmb/MiniCPM-2B-128k"
    assert settings.hf_max_context < 128_000  # deliberately below model max


def test_factory_returns_hf_local_by_default():
    assert isinstance(get_provider(load_settings()), HFLocalProvider)


def test_factory_rejects_unknown_provider():
    settings = load_settings()
    settings = type(settings)(llm_provider="nope", **{
        k: getattr(settings, k) for k in settings.__dataclass_fields__ if k != "llm_provider"
    })
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_provider(settings)
