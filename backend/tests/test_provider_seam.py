"""Unit tests for the provider factory. These must stay fast: no model loading."""

from types import SimpleNamespace

import pytest

from app.config import load_settings
from app.llm import get_provider


def test_factories_use_default_model_configuration_without_loading_weights(monkeypatch):
    from app.embeddings import get_embedder
    from app.embeddings.granite import GraniteEmbedder
    from app.llm.hf_local import HFLocalProvider

    for key in ("LLM_PROVIDER", "HF_MODEL_ID", "HF_MAX_CONTEXT", "EMBEDDING_PROVIDER",
                "EMBEDDING_MODEL_ID", "EMBEDDING_MAX_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert settings.llm_provider == "hf-local"
    assert settings.hf_model_id == "openbmb/MiniCPM-2B-128k"
    assert 0 < settings.hf_max_context < 128_000
    assert isinstance(get_provider(settings), HFLocalProvider)
    assert settings.embedding_provider == "granite"
    assert settings.embedding_model_id == "ibm-granite/granite-embedding-125m-english"
    assert 0 < settings.embedding_max_tokens <= 512
    assert isinstance(get_embedder(settings), GraniteEmbedder)


def test_factory_honors_hf_model_override_and_model_specific_stops(monkeypatch):
    monkeypatch.delenv("HF_STOP_TOKEN_STRINGS", raising=False)
    monkeypatch.setenv("HF_MODEL_ID", "openbmb/MiniCPM-2B-128k")
    settings = load_settings()
    same_model = get_provider(settings, provider="hf-local", model=settings.hf_model_id)
    other_model = get_provider(settings, provider="hf-local", model="another/local-model")
    assert same_model._settings.hf_stop_token_strings == ("<|im_end|>",)
    assert other_model._settings.hf_model_id == "another/local-model"
    assert other_model._settings.hf_stop_token_strings == ()


def test_factory_accepts_an_injected_client_and_keeps_secrets_out_of_repr():
    from app.llm.adapters import OpenAIProvider

    settings = SimpleNamespace(llm_provider="openai", llm_model_id="model")
    provider = get_provider(settings, api_key="secret", client=SimpleNamespace(responses=None))
    assert isinstance(provider, OpenAIProvider)
    assert "secret" not in repr(provider)


@pytest.mark.parametrize("kind", ["llm", "embedding"])
def test_factory_rejects_unknown_provider(kind):
    from app.embeddings import get_embedder

    settings = load_settings()
    field = f"{kind}_provider"
    settings = type(settings)(**{field: "nope"}, **{
        k: getattr(settings, k) for k in settings.__dataclass_fields__ if k != field
    })
    with pytest.raises(ValueError, match=f"unknown {kind.upper()}_PROVIDER"):
        (get_provider if kind == "llm" else get_embedder)(settings)
