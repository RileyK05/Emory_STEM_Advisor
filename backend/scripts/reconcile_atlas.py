"""Build or explicitly publish the Atlas-primary course catalog from saved captures.

Run from ``backend/``. By default this validates cached evidence and writes a
reviewable canonical JSONL; pass ``--publish`` to index that exact file.
"""

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from app.atlas.authority import prepare_authoritative_courses
from app.atlas.live import IndexRecord, read_capture
from app.atlas.offerings import select_latest_offering_captures, validate_offering_captures
from app.atlas.records import load_atlas
from app.util.digest import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "data" / "atlas" / "emory_college.jsonl"
DEFAULT_CAPTURES = ROOT / "data" / "atlas" / "spring-2027-capture"
DEFAULT_OUTPUT = ROOT / "data" / "atlas" / "atlas-primary-spring-2027.jsonl"


def _snapshots(directory: Path, term: str):
    by_career: dict[str, tuple[list[dict], list[dict]]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read capture {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise TypeError(f"capture {path.name} must contain a JSON object")
        if value.get("term") != term:
            continue
        career = value.get("career")
        if not isinstance(career, str) or not career.strip():
            raise ValueError(f"capture {path.name} has no career")
        indexes, details = by_career.setdefault(career, ([], []))
        if value.get("kind") == "index":
            indexes.append(value)
        elif value.get("kind") == "detail":
            details.append(value)
        else:
            raise ValueError(f"capture {path.name} has an unknown kind")
    if not by_career:
        raise ValueError(f"no cached captures found for {term}")
    snapshots = []
    for career, (indexes, details) in sorted(by_career.items()):
        if not indexes:
            raise ValueError(f"no index capture for {career} / {term}")
        def capture_time(value):
            raw = value.get("observed_at")
            if not isinstance(raw, str):
                raise TypeError("index capture observed_at must be a timestamp")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("capture observed_at must be timezone-aware")
            return parsed

        latest_time = max(capture_time(value) for value in indexes)
        latest_indexes = [value for value in indexes if capture_time(value) == latest_time]
        latest_records = [read_capture(value) for value in latest_indexes]
        if all(isinstance(record, IndexRecord) and record.announced_count == 0
               and not record.rows for record in latest_records):
            if len({hashlib.sha256(canonical_json_bytes(value)).hexdigest()
                    for value in latest_indexes}) != 1:
                raise ValueError(f"latest empty indexes disagree for {career} / {term}")
            continue
        index, selected = select_latest_offering_captures(
            indexes, details, career=career, term=term,
        )
        snapshot = validate_offering_captures(index, selected)
        if snapshot.eligible_count:
            snapshots.append(snapshot)
    if not snapshots:
        raise ValueError(f"no eligible cached offering snapshots found for {term}")
    return snapshots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--captures", type=Path, default=DEFAULT_CAPTURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--term", default="Spring 2027")
    parser.add_argument("--collection", default="default")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--publish", action="store_true",
                        help="index the prepared JSONL with the verified Atlas importer")
    args = parser.parse_args()
    if args.output.resolve() == args.catalog.resolve():
        parser.error("--output must not overwrite the catalog fallback input")
    try:
        snapshots = _snapshots(args.captures, args.term)
        courses = prepare_authoritative_courses(load_atlas(args.catalog), snapshots,
                                                term=args.term)
        document = "".join(
            json.dumps(course.model_dump(mode="json"), ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")) + "\n"
            for course in courses
        ).encode("utf-8")
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{args.output.name}-",
                                              suffix=".tmp", dir=args.output.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, args.output)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    atlas_count = sum(course.authority == "atlas" for course in courses)
    print(f"prepared {len(courses)} courses ({atlas_count} Atlas-primary) at {args.output}")
    if not args.publish:
        print("validation only; pass --publish to index this prepared snapshot")
        return

    from app.atlas.importer import import_authoritative_atlas
    from app.config import load_settings
    from app.embeddings import get_embedder
    from app.retrieval.config import load_retrieval_config
    from scripts._db import open_connection
    from scripts.import_atlas import _collection_id

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)

    with open_connection(settings, args.database_url) as conn:
        collection_id = _collection_id(conn, args.collection)
        source_id = import_authoritative_atlas(
            conn, collection_id=collection_id, path=args.output, snapshots=snapshots,
            embedder=embedder, embed_model=settings.embedding_model_id,
            count_tokens=embedder.count_tokens, config=config,
        )
    print(f"published {args.output}: source {source_id}")


if __name__ == "__main__":
    main()
