"""Provider factory: the only module that imports/constructs implementations."""

from __future__ import annotations

import math
import re
from contextlib import contextmanager
from typing import Any

from app.config import Settings, with_hf_model
from app.llm.base import Provider

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 60.0


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
    )
    if name in ("openai", "openai-compatible"):
        if client is None:
            client = _sdk_client("openai", api_key, timeout_seconds, "to use this provider",
                                 base_url=base_url)
        cls = OpenAICompatibleProvider if name == "openai-compatible" else OpenAIProvider
        if name == "openai-compatible":
            return cls(client, model_id, thinking=settings.llm_compatible_thinking,
                       cache_key=settings.llm_compatible_cache_key)
        return cls(client, model_id)
    if name == "anthropic":
        if client is None:
            client = _sdk_client("anthropic", api_key, timeout_seconds, "to use this provider")
        return AnthropicProvider(client, model_id)
    if name in ("google", "gemini"):
        if client is None:
            client = _sdk_client("google", api_key, timeout_seconds, "to use this provider")
        return GoogleProvider(client, model_id)
    raise ValueError(f"unknown LLM_PROVIDER: {name!r}")


def get_decision_provider(provider: str, *, model: str, api_key: str | None = None,
                          account_id: str | None = None, endpoint: str | None = None,
                          client: Any = None,
                          timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS):
    """Build a typed decision adapter; API keys are provided by server profiles."""
    _validate_timeout(timeout_seconds)
    from app.llm.decision_adapters import CloudflareClefProvider, OpenAIDecisionProvider

    if provider == "openai":
        if client is None:
            client = _sdk_client("openai", api_key, timeout_seconds, "for Decisions")
        return OpenAIDecisionProvider(client, model)
    if provider == "cloudflare":
        if client is None:
            if not account_id or not api_key:
                raise ValueError("Cloudflare decisions require account_id and api_key")
            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError("install httpx to use Cloudflare Clef") from exc
            client = httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        if not account_id or not api_key:
            raise ValueError("Cloudflare decisions require account_id and api_key")
        return CloudflareClefProvider(client, account_id=account_id, api_token=api_key,
                                      model=model, endpoint=endpoint or "https://api.cloudflare.com/client/v4")
    raise ValueError(f"unknown decision provider: {provider!r}")


def get_batch_provider(provider: str, *, model: str, api_key: str | None = None,
                      client: Any = None,
                      timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS):
    """Build a native async batch adapter from the configured provider factory."""
    _validate_timeout(timeout_seconds)
    if provider not in {"openai", "anthropic", "google", "gemini"}:
        raise ValueError(f"unknown batch provider: {provider!r}")
    if client is None:
        client = _sdk_client("google" if provider == "gemini" else provider, api_key,
                             timeout_seconds, "to use batches")
    from app.llm.batches import AnthropicBatchProvider, GoogleBatchProvider, OpenAIBatchProvider

    if provider == "openai":
        return OpenAIBatchProvider(client, model)
    if provider == "anthropic":
        return AnthropicBatchProvider(client, model)
    if not api_key:
        raise ValueError("Google batch results require a configured API key")
    downloader = _GoogleBatchFileDownloader(api_key, timeout_seconds=timeout_seconds)
    return GoogleBatchProvider(client, model, file_downloader=downloader)


class _GoogleBatchFileDownloader:
    """Stream a Google file resource from its fixed authenticated API origin."""

    _resource = re.compile(r"^files/([a-z0-9-]{1,40})$")
    _origin = "https://generativelanguage.googleapis.com"

    def __init__(self, api_key: str, *, timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
                 client: Any = None):
        import httpx
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self._key = api_key

    @contextmanager
    def __call__(self, file_name: str, *, max_bytes: int):
        match = self._resource.fullmatch(file_name or "")
        if not match:
            raise ValueError("Google returned an invalid file resource name")
        url = f"{self._origin}/v1beta/files/{match.group(1)}:download"
        with self._client.stream("GET", url, params={"alt": "media"},
                                 headers={"x-goog-api-key": self._key,
                                          "accept-encoding": "identity"}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                raise ValueError("Google file download redirects are not allowed")
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    announced = int(length)
                except ValueError:
                    raise ValueError("Google file download has invalid Content-Length") from None
                if announced < 0 or announced > max_bytes:
                    raise ValueError(f"Google file result exceeds the {max_bytes} byte limit")
            yield response.iter_raw(chunk_size=64 * 1024)


def _validate_timeout(timeout_seconds: float) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("provider timeout must be positive and finite")
