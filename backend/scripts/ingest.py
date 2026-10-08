"""Ingest files into a collection.

Usage (from backend/):
    python -m scripts.ingest --collection NAME FILE [FILE ...]
"""

import argparse
import mimetypes
from pathlib import Path
from uuid import uuid4

from transformers import AutoTokenizer

from app.config import load_settings
from app.db import queries
from app.embeddings import get_embedder
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from scripts._db import open_connection

_EXTENSIONS = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def _content_type(path: Path) -> str:
    explicit = _EXTENSIONS.get(path.suffix.lower())
    if explicit:
        return explicit
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "text/plain"


def _collection_id(conn, name: str):
    row = conn.execute(queries.get("get_collection_by_name"), {"name": name}).fetchone()
    if row is not None:
        return row["collection_id"]
    collection_id = uuid4()
    conn.execute(
        queries.get("insert_collection"),
        {"collection_id": collection_id, "name": name},
    )
    conn.commit()
    row = conn.execute(
        queries.get("get_collection_by_name"), {"name": name}
    ).fetchone()
    return row["collection_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_id)

    def count_tokens(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    with open_connection(settings, args.database_url) as conn:
        collection_id = _collection_id(conn, args.collection)
        for path in args.files:
            source_id = ingest_file(
                conn,
                collection_id=collection_id,
                path=path,
                content_type=_content_type(path),
                count_tokens=count_tokens,
                embedder=embedder,
                embed_model=settings.embedding_model_id,
                config=config,
            )
            print(f"{path.name}: {source_id}")


if __name__ == "__main__":
    main()
