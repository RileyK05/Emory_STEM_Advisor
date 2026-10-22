"""Deterministic test doubles. No models, no network, no randomness across runs."""

import hashlib
from datetime import UTC, datetime, timedelta

import numpy as np

from app.llm.base import GenerationResult, Usage


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

    def count_tokens(self, text: str) -> int:
        return whitespace_tokens(text)

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

    def generate(self, request) -> GenerationResult:
        self.calls += 1
        self.last_messages = [dict(m) for m in request.messages]
        words = sum(len(str(m.get("content", "")).split()) for m in self.last_messages)
        return GenerationResult(
            text=self.text, finish_reason="stop",
            usage=Usage(words, len(self.text.split()), source="estimated"),
        )


class ScriptedProvider:
    """Provider double that returns one scripted `GenerationResult` per call.

    Unlike `FakeProvider`, this implements the typed `generate` seam, so tests can
    exercise finish reasons, provider refusals, tool calls, and usage accounting
    without redefining a provider in each file. Requests are recorded for
    inspection.
    """

    def __init__(
        self,
        text: str = "An answer citing [1] the context.",
        *,
        finish_reason: str = "stop",
        refusal: str | None = None,
        tool_calls=(),
        usage: Usage | None = None,
        model: str | None = None,
    ) -> None:
        if usage is None:
            usage = Usage(prompt_tokens=8, completion_tokens=8, source="provider")
        self.result = GenerationResult(
            text=text,
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
            refusal=refusal,
            model=model,
        )
        self.requests: list = []

    def generate(self, request):
        self.requests.append(request)
        return self.result


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 10, 7, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)
