"""IBM Granite embedding provider (default: granite-embedding-125m-english).

Loads via sentence-transformers and embeds through the Embedder interface.
Weights download from Hugging Face on first use and are cached in the
standard HF cache (~250 MB), so there is no per-machine install step beyond
pip.

Both methods share one encode path: the model has no instruction scheme, and
L2 normalization means cosine and dot are interchangeable downstream.
"""

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from app.config import Settings
from app.embeddings.base import Embedder

# Lazy singleton; the model loads on first use, not at import.
_model: SentenceTransformer | None = None


def _load(settings: Settings) -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(
            settings.embedding_model_id,
            device="cuda" if torch.cuda.is_available() else "cpu",
        )
        _model.max_seq_length = settings.embedding_max_tokens
    return _model


class GraniteEmbedder:
    def __init__(self, settings: Settings):
        self._settings = settings

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        model = _load(self._settings)
        return model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        model = _load(self._settings)
        return model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)