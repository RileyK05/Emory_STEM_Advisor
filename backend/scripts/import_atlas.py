"""Import a course atlas into a collection.

Usage (from backend/):
    python -m scripts.import_atlas --collection NAME FILE
"""

import argparse
from pathlib import Path
from uuid import uuid4

from transformers import AutoTokenizer

from app.atlas.importer import import_atlas, unresolved_courses
from app.atlas.records import load_atlas
from app.config import load_settings
from app.db import queries
from app.embeddings import get_embedder
from app.retrieval.config import load_retrieval_config
from scripts._db import open_connection


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
    return collection_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_id)

    def count_tokens(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    with open_connection(settings, args.database_url) as conn:
        collection_id = _collection_id(conn, args.collection)
        import_atlas(
            conn,
            collection_id=collection_id,
            path=args.file,
            embedder=embedder,
            embed_model=settings.embedding_model_id,
            count_tokens=count_tokens,
            config=config,
        )
        for course in unresolved_courses(load_atlas(args.file)):
            print(f"[{course.code}] {course.requisites!r}")


if __name__ == "__main__":
    main()
