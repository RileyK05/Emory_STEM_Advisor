"""The test doubles themselves must be deterministic and well-formed."""

import numpy as np

from tests.fakes import FakeEmbedder, whitespace_tokens


def test_vectors_are_deterministic_and_unit_norm():
    embedder = FakeEmbedder(dim=8)
    a1 = embedder.embed_query("graph theory")
    a2 = embedder.embed_query("graph theory")
    assert np.array_equal(a1, a2)
    assert a1.shape == (8,)
    assert a1.dtype == np.float32
    assert abs(float(np.linalg.norm(a1)) - 1.0) < 1e-6


def test_different_text_gives_different_vector():
    embedder = FakeEmbedder()
    assert not np.array_equal(
        embedder.embed_query("alpha"), embedder.embed_query("beta")
    )


def test_embed_documents_shape():
    embedder = FakeEmbedder(dim=8)
    out = embedder.embed_documents(["one", "two", "three"])
    assert out.shape == (3, 8)
    assert embedder.embed_documents([]).shape == (0, 8)


def test_whitespace_tokens():
    assert whitespace_tokens("a b c") == 3
    assert whitespace_tokens("") == 0
