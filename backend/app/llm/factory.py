"""Provider factory: the only module that imports/constructs implementations."""

from __future__ import annotations

import math
from typing import Any

from app.config import Settings, with_hf_model
from app.llm.base import Provider

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 60.0
DEFAULT_LLM_MAX_CONCURRENCY = 8


def _sdk_client(sdk: str, api_key: str | None, timeout_seconds: float, purpose: str,
                *, base_url: str | None = None) -> Any:
    """Build an official SDK client (single attempt; retries belong to the job layer)."""
    try:
        if sdk == "openai":
            from openai import OpenAI
        elif sdk == "anthropic":
            from anthropic import Anthropic
        else:
            from google import genai
    except ImportError as exc:
        package = "google-genai" if sdk == "google" else sdk
        raise RuntimeError(f"install the optional {package} SDK {purpose}") from exc
    if sdk == "openai":
        kwargs = {"api_key": api_key, "timeout": timeout_seconds, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)
    if sdk == "anthropic":
        return Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    from google.genai import types
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=max(1, round(timeout_seconds * 1000)),
        retry_options=types.HttpRetryOptions(attempts=1)))


def get_provider(settings: Settings, *, provider: str | None = None,
                 model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, client: Any = None,
                 timeout_seconds: float | None = None) -> Provider:
    """Build a provider. Credentials are passed in and never logged here."""
    name = provider or settings.llm_provider
    api_key = api_key or getattr(settings, "llm_api_key", None)
    base_url = base_url or getattr(settings, "llm_base_url", None)
    if timeout_seconds is None:
        timeout_seconds = getattr(settings, "llm_timeout_seconds", DEFAULT_PROVIDER_TIMEOUT_SECONDS)
    _validate_timeout(timeout_seconds)
    if name not in {"hf-local", "openai", "openai-compatible", "anthropic", "google", "gemini"}:
        raise ValueError(f"unknown LLM_PROVIDER: {name!r}")
    if name == "hf-local":
        from app.llm.hf_local import HFLocalProvider
        local_settings = with_hf_model(settings, model) if model else settings
        return HFLocalProvider(local_settings, timeout_seconds)
    model_id = model or settings.llm_model_id
    if not model_id:
        raise ValueError(f"model must be configured for provider {name!r}")
    from app.llm.adapters import (
        AnthropicProvider,
        GoogleProvider,
        OpenAICompatibleProvider,
        OpenAIProvider,
        provider_gate,
    )
    max_concurrency = getattr(settings, "llm_max_concurrency", DEFAULT_LLM_MAX_CONCURRENCY)
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency < 1:
        raise ValueError("LLM_MAX_CONCURRENCY must be a positive integer")
    provider_key = "google" if name == "gemini" else name
    gate = provider_gate(
        (provider_key, (base_url or "").rstrip("/")),
        max_concurrency,
        provider=provider_key,
    )
    if name in ("openai", "openai-compatible"):
        if client is None:
            client = _sdk_client("openai", api_key, timeout_seconds, "to use this provider",
                                 base_url=base_url)
        cls = OpenAICompatibleProvider if name == "openai-compatible" else OpenAIProvider
        if name == "openai-compatible":
            return cls(client, model_id, thinking=settings.llm_compatible_thinking,
                       cache_key=settings.llm_compatible_cache_key, gate=gate)
        return cls(client, model_id, gate=gate)
    if name == "anthropic":
        if client is None:
            client = _sdk_client("anthropic", api_key, timeout_seconds, "to use this provider")
        return AnthropicProvider(client, model_id, gate=gate)
    if name in ("google", "gemini"):
        if client is None:
            client = _sdk_client("google", api_key, timeout_seconds, "to use this provider")
        return GoogleProvider(client, model_id, gate=gate)
    raise ValueError(f"unknown LLM_PROVIDER: {name!r}")


def _validate_timeout(timeout_seconds: float) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("provider timeout must be positive and finite")
