"""Ingest files into a collection.

Usage (from backend/):
    python -m scripts.ingest --collection NAME FILE [FILE ...]
"""

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

from transformers import AutoTokenizer

from app.config import load_settings
from app.db import queries
from app.embeddings import get_embedder
from app.ingest.pipeline import ingest_file
from app.ingest.provenance import ProvenanceError, provenance_from_manifest
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
    parser.add_argument("--origin-manifest", type=Path)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    origin_by_path = {}
    if args.origin_manifest:
        manifest_root = args.origin_manifest.resolve().parent
        manifest = json.loads(args.origin_manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), dict):
            parser.error("origin manifest must contain a sources object")
        for path in args.files:
            try:
                relative_path = path.resolve().relative_to(manifest_root).as_posix()
            except ValueError:
                parser.error(f"{path} is outside the origin manifest directory")
            metadata = manifest["sources"].get(relative_path)
            if metadata is None:
                parser.error(f"{relative_path} is absent from the origin manifest")
            origin_key = metadata.get("origin_key")
            expected_hash = metadata.get("content_sha256")
            if not isinstance(origin_key, str) or not origin_key.strip():
                parser.error(f"{relative_path} has an invalid origin_key")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64:
                parser.error(f"{relative_path} has no valid content_sha256")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                parser.error(f"{relative_path} does not match the origin manifest SHA-256")
            try:
                provenance = provenance_from_manifest(metadata)
            except ProvenanceError as exc:
                parser.error(f"{relative_path}: {exc}")
            origin_by_path[path.resolve()] = (origin_key, provenance)

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_id)

    def count_tokens(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    with open_connection(settings, args.database_url) as conn:
        collection_id = _collection_id(conn, args.collection)
        for path in args.files:
            origin_key, provenance = origin_by_path.get(path.resolve(), (None, None))
            source_id = ingest_file(
                conn,
                collection_id=collection_id,
                path=path,
                content_type=_content_type(path),
                count_tokens=count_tokens,
                embedder=embedder,
                embed_model=settings.embedding_model_id,
                config=config,
                origin_key=origin_key,
                provenance=provenance,
            )
            print(f"{path.name}: {source_id}")


if __name__ == "__main__":
    main()
