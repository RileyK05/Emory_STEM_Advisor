"""Unit tests for the embedding seam. These must stay fast: no model loading."""

import pytest

from app.config import load_settings
from app.embeddings import get_embedder
from app.embeddings.granite import GraniteEmbedder


def test_default_settings_are_granite_config(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL_ID", raising=False)
    settings = load_settings()
    assert settings.embedding_provider == "granite"
    assert settings.embedding_model_id == "ibm-granite/granite-embedding-125m-english"
    assert settings.embedding_max_tokens <= 512  # Granite context ceiling


def test_factory_returns_granite_by_default():
    assert isinstance(get_embedder(load_settings()), GraniteEmbedder)


def test_factory_rejects_unknown_provider():
    settings = load_settings()
    settings = type(settings)(embedding_provider="nope", **{
        k: getattr(settings, k) for k in settings.__dataclass_fields__ if k != "embedding_provider"
    })
    with pytest.raises(ValueError, match="unknown EMBEDDING_PROVIDER"):
        get_embedder(settings)