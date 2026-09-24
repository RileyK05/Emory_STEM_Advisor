from app import embeddings


def test_package_exports_factory():
    assert hasattr(embeddings, "get_embedder")