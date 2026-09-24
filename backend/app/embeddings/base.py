"""Embedder interface. Every embedding call in the system goes through this.

Queries and documents are separate methods: some retrievers embed the two
sides differently (instruction prefixes, prompt names), and collapsing them
into one method makes that distinction easy to lose.
"""

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed corpus texts. Returns (n, dim) float32, L2-normalized."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Embed one search query. Returns (dim,) float32, L2-normalized."""
        ...