"""Deterministic test doubles. No models, no network, no randomness across runs."""

import hashlib

import numpy as np


class FakeEmbedder:
    """Implements the Embedder protocol. Vectors are deterministic from text."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def _vector(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
        rng = np.random.default_rng(int.from_bytes(digest, "big"))
        vec = rng.standard_normal(self.dim).astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            vec[0] = 1.0
            norm = 1.0
        return (vec / norm).astype(np.float32)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.vstack([self._vector(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


def embed_other_model(text: str, dim: int) -> np.ndarray:
    """A second, differently-dimensioned embedding for cross-model tests."""
    digest = hashlib.sha256(("other:" + text).encode("utf-8")).digest()[:8]
    rng = np.random.default_rng(int.from_bytes(digest, "big"))
    vec = rng.standard_normal(dim).astype(np.float32)
    return (vec / np.linalg.norm(vec)).astype(np.float32)


def whitespace_tokens(text: str) -> int:
    return len(text.split())


class FakeProvider:
    """Deterministic stand-in for the LLM provider. Records the last messages."""

    def __init__(self, text: str = "An answer citing [1] the context.") -> None:
        self.text = text
        self.last_messages: list[dict[str, str]] = []
        self.calls = 0

    def chat(self, messages, *, max_new_tokens: int | None = None) -> str:
        self.calls += 1
        self.last_messages = [dict(m) for m in messages]
        return self.text
