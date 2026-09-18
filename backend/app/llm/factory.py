"""Provider factory. The rest of the system never imports a provider directly."""

from app.config import Settings
from app.llm.base import Provider


def get_provider(settings: Settings) -> Provider:
    if settings.llm_provider == "hf-local":
        from app.llm.hf_local import HFLocalProvider

        return HFLocalProvider(settings)
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider!r}")
