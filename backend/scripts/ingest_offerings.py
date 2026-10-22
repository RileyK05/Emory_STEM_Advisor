"""Validate and explicitly publish term-scoped Atlas captures.

Usage (from backend/):
    python -m scripts.ingest_offerings --captures ../data/atlas/spring-2027-capture \
        --career "Graduate Allied Health" --collection emory --publish

Without ``--publish`` this only validates the capture set; it does not load a
model, write a staging document, or touch the database.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.atlas.offerings import (
    capture_slug,
    publish_offerings,
    render_offerings_markdown,
    select_latest_offering_captures,
    validate_offering_captures,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURES = ROOT / "data" / "atlas" / "spring-2027-capture"
DEFAULT_STAGING = ROOT / "data" / "atlas" / "offerings"


def _load_captures(directory: Path, career: str, term: str) -> tuple[dict, list[dict]]:
    index_values: list[dict] = []
    detail_values: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read capture {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise TypeError(f"capture {path.name} must contain a JSON object")
        if value.get("career") != career or value.get("term") != term:
            continue
        if value.get("kind") == "index":
            index_values.append(value)
        elif value.get("kind") == "detail":
            detail_values.append(value)
        else:
            raise ValueError(f"capture {path.name} has an unknown kind")
    return select_latest_offering_captures(
        index_values, detail_values, career=career, term=term,
    )


def _collection_id(conn, name: str):
    from app.db import queries

    row = conn.execute(queries.get("get_collection_by_name"), {"name": name}).fetchone()
    if row is not None:
        return row["collection_id"]
    collection_id = uuid4()
    conn.execute(queries.get("insert_collection"), {
        "collection_id": collection_id,
        "name": name,
    })
    conn.commit()
    row = conn.execute(queries.get("get_collection_by_name"), {"name": name}).fetchone()
    return row["collection_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, default=DEFAULT_CAPTURES)
    parser.add_argument("--career", required=True)
    parser.add_argument("--term", default="Spring 2027")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--publish", action="store_true",
                        help="write the deterministic staging document and index it")
    args = parser.parse_args()

    try:
        index_value, detail_values = _load_captures(args.captures, args.career, args.term)
        snapshot = validate_offering_captures(index_value, detail_values)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    print(
        f"validated {snapshot.term} / {snapshot.career}: "
        f"{snapshot.eligible_count} searchable result row(s), "
        f"{snapshot.held_count} held placeholder row(s), "
        f"{len(snapshot.records)} complete result links; "
        f"index SHA-256 {snapshot.index_evidence_hash}"
    )
    if not args.publish:
        print("dry validation only; pass --publish to stage and index this snapshot")
        return
    if snapshot.eligible_count == 0:
        parser.error("all result rows are held; no ordinary advising source will be published")

    from transformers import AutoTokenizer

    from app.config import load_settings
    from app.embeddings import get_embedder
    from app.retrieval.config import load_retrieval_config
    from scripts._db import open_connection

    output = DEFAULT_STAGING / f"{capture_slug(snapshot.career)}-{capture_slug(snapshot.term)}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(render_offerings_markdown(snapshot))

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_id)

    def count_tokens(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    with open_connection(settings, args.database_url) as conn:
        collection_id = _collection_id(conn, args.collection)
        source_id = publish_offerings(
            conn,
            collection_id=collection_id,
            snapshot=snapshot,
            path=output,
            data_dir=settings.data_dir,
            embedder=embedder,
            embed_model=settings.embedding_model_id,
            count_tokens=count_tokens,
            config=config,
        )
    print(f"published {output}: source {source_id}")


if __name__ == "__main__":
    main()
