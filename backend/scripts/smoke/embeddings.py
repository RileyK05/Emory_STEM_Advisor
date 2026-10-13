"""Smoke test for the real Granite embedder. Loads the model (~250 MB first run)."""

from app.config import load_settings
from app.embeddings import get_embedder


def main() -> None:
    embedder = get_embedder(load_settings())
    docs = [
        "CS 221 covers discrete structures including graphs, trees, and induction.",
        "MATH 111 is a first course in calculus of one variable.",
        "BIOL 141 introduces cell structure and function.",
    ]
    doc_vecs = embedder.embed_documents(docs)
    assert doc_vecs.shape == (3, 768), doc_vecs.shape

    query_vec = embedder.embed_query("Which course teaches graph theory?")
    assert query_vec.shape == (768,), query_vec.shape

    scores = doc_vecs @ query_vec
    best = scores.argmax()
    print("scores:", [round(float(s), 3) for s in scores])
    print("top:", docs[best])
    assert best == 0, "expected CS 221 to match graph-theory query"


if __name__ == "__main__":
    main()