"""Embedder interface. Every embedding call in the system goes through this.

Queries and documents are separate methods: some retrievers embed the two
sides differently (instruction prefixes, prompt names), and collapsing them
into one method makes that distinction easy to lose.
"""

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    def count_tokens(self, text: str) -> int:
        """Count actual embedding tokens, including special tokens, without truncation."""
        ...

    def chunk_tokens(self, text: str) -> int:
        """Count the tokens a chunk's text occupies, without special tokens.

        This is the chunking measure, and it must be the same on every ingest
        path: a source's chunk boundaries have to be a function of its bytes
        alone. A ``chunk_id`` addresses a chunk by index, so a path that counts
        differently would put the same index on different text.
        """
        ...

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed corpus texts. Returns (n, dim) float32, L2-normalized."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Embed one search query. Returns (dim,) float32, L2-normalized."""
        ...