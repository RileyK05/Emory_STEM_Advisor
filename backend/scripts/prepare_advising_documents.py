"""Prepare a reviewed advising batch without importing it or using the network.

From backend/:
    python -m scripts.prepare_advising_documents
    python -m scripts.prepare_advising_documents --check

Only the manifest's documents are ingestion inputs. preparation-lineage.json
records the original hashes and normalization; it is never a source document.
"""

import argparse
import hashlib
import json
import tomllib
from collections import Counter
from pathlib import Path

from app.config import load_settings
from app.ingest.advising_documents import prepare_documents
from app.util.atomic import write_bytes_atomic, write_text_atomic

DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "configs" / "advising_sources.toml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="validate without writing files")
    args = parser.parse_args()
    try:
        registry_bytes = args.registry.read_bytes()
        registry = tomllib.loads(registry_bytes.decode("utf-8"))
        data = load_settings().data_dir
        input_root = (args.input_root or data / registry["input_subdir"]).resolve()
        batch = prepare_documents(registry, input_root=input_root,
                                  registry_sha256=hashlib.sha256(registry_bytes).hexdigest())
        output = (args.output or data / "staging" / f"advising-pilot-{registry['acquired_at']}")
        output = output.resolve()
        if output.is_relative_to(input_root) or input_root.is_relative_to(output):
            raise ValueError("input and output directories must not overlap")
        expected = {doc.filename for doc in batch.documents}
        documents = output / "documents"
        if not documents.resolve().is_relative_to(output):
            raise ValueError("output documents directory escapes --output")
        if documents.exists() and (not documents.is_dir() or any(
            path.name not in expected or not path.is_file() for path in documents.iterdir()
        )):
            raise ValueError("output documents has unlisted entries; choose a fresh --output")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    counts = Counter(record["disposition"] for record in registry["document"])
    print("reviewed dispositions: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    print(f"validated {len(batch.documents)} canonical documents from {input_root}")
    if args.check:
        return
    for document in batch.documents:
        write_bytes_atomic(documents / document.filename, document.content)
    write_text_atomic(output / "preparation-lineage.json",
                      json.dumps(batch.lineage, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    write_text_atomic(output / "ingest-manifest.json",
                      json.dumps(batch.manifest(), indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"prepared {len(batch.documents)} documents in {documents}")
    print(f"exact-SHA manifest: {output / 'ingest-manifest.json'}")
    print("No files were ingested; review extraction and existing source identities before import.")


if __name__ == "__main__":
    main()
