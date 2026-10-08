"""Config. Everything is env-driven; nothing model-specific is hardcoded."""

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load `REPO_ROOT/.env` into the environment without overriding anything.

    Real environment variables and shell exports win, so a key in the shell
    always beats the file (intentional). Runs at import, before any setting is
    read. Missing file, blank lines, and `#` comments are ignored.
    """
    path = _REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


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
    # Embedding provider for the RAG index. Currently "granite" only;
    # "qwen3" is the planned fallback if retrieval quality disappoints.
    embedding_provider: str
    # Hugging Face model id for the embedding provider.
    embedding_model_id: str
    # Max tokens per embedded text. Granite's context is 512; chunkers
    # must respect this ceiling.
    embedding_max_tokens: int
    # Postgres connection string. None means the dev/test embedded database
    # (pgserver) is used; production sets DATABASE_URL.
    database_url: str | None
    # Collection the HTTP API serves when a request does not name one.
    default_collection: str
    # Where ingest keeps raw uploads (`raw/`) and the dev database
    # (`pgserver/`). Gitignored. Tests point it at a temp dir.
    data_dir: Path


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.environ.get("LLM_PROVIDER", "hf-local"),
        hf_model_id=os.environ.get("HF_MODEL_ID", "openbmb/MiniCPM-2B-128k"),
        hf_max_context=_int("HF_MAX_CONTEXT", 4096),
        hf_max_new_tokens=_int("HF_MAX_NEW_TOKENS", 256),
        embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "granite"),
        embedding_model_id=os.environ.get(
            "EMBEDDING_MODEL_ID", "ibm-granite/granite-embedding-125m-english"
        ),
        embedding_max_tokens=_int("EMBEDDING_MAX_TOKENS", 512),
        database_url=os.environ.get("DATABASE_URL") or None,
        default_collection=os.environ.get("DEFAULT_COLLECTION", "default"),
        data_dir=Path(os.environ.get("DATA_DIR") or _REPO_ROOT / "data"),
    )
