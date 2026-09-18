"""Config. Everything is env-driven; nothing model-specific is hardcoded."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # Which provider backend answers. Currently: "hf-local" only.
    llm_provider: str
    # Hugging Face model id for the local experimentation provider.
    hf_model_id: str
    # Context ceiling for local experiments. Deliberately far below the
    # model's max: full attention over 128k is not feasible on consumer
    # hardware. The local model validates the pipeline, not long context.
    hf_max_context: int
    hf_max_new_tokens: int


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.environ.get("LLM_PROVIDER", "hf-local"),
        hf_model_id=os.environ.get("HF_MODEL_ID", "openbmb/MiniCPM-2B-128k"),
        hf_max_context=_int("HF_MAX_CONTEXT", 4096),
        hf_max_new_tokens=_int("HF_MAX_NEW_TOKENS", 256),
    )
