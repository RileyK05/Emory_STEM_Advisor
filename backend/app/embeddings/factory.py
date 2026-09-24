"""Embedder factory. The rest of the system never imports an embedder directly."""

from app.config import Settings
from app.embeddings.base import Embedder


def get_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "granite":
        from app.embeddings.granite import GraniteEmbedder

        return GraniteEmbedder(settings)
    raise ValueError(f"unknown EMBEDDING_PROVIDER: {settings.embedding_provider!r}")