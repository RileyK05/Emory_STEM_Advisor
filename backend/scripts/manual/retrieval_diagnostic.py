"""Manual, read-only retrieval diagnostics over frozen calibration cases.

Run from ``backend/`` after the Granite embedding snapshot is cached. This
runner creates an isolated local Postgres/data directory, ingests the frozen
source summaries, and never changes production retrieval configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from app.util.digest import file_sha256, json_digest

sha256 = file_sha256
_canonical_hash = json_digest


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval_2026_10_08"
DEFAULT_CASES = REPO_ROOT / "backend" / "tests" / "fixtures" / "retrieval_calibration_2026_10_08" / "cases.jsonl"
CONFIG_PATH = REPO_ROOT / "backend" / "configs" / "retrieval.toml"
FLOOR_SWEEP = (-1.0, -0.5, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
LABELS = frozenset({"answerable", "partial", "unanswerable"})
PARTITIONS = frozenset({"calibration", "holdout"})
SOURCE_NAMES = frozenset(path.name for path in FIXTURE.glob("*.md") if path.name != "README.md")
SOURCE_LABELS = {
    path.name: frozenset(
        line.lstrip("#").strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    )
    for path in FIXTURE.glob("*.md") if path.name != "README.md"
}
RUNS_ROOT = (REPO_ROOT / "data" / "manual-eval-runs").resolve()


@dataclass(frozen=True)
class DiagnosticCase:
    case_id: str
    partition: str
    query: str
    answerability: str
    support: tuple[tuple[str, str | None], ...]
    related_sections: tuple[tuple[str, str | None], ...]
    label_reason: str


def _parse_sections(case_id: str, raw, field: str):
    if not isinstance(raw, list):
        raise TypeError(f"case {case_id!r} {field} must be a list")
    sections = []
    for section in raw:
        if not isinstance(section, dict):
            raise TypeError(f"case {case_id!r} has a malformed {field} section")
        filename = section.get("filename")
        label = section.get("locator_label")
        if not isinstance(filename, str) or not filename.strip():
            raise TypeError(f"case {case_id!r} section filename must be non-empty")
        if filename not in SOURCE_NAMES:
            raise ValueError(f"case {case_id!r} names a source outside the frozen corpus")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise TypeError(f"case {case_id!r} section locator_label must be text or null")
        if label is not None and label not in SOURCE_LABELS[filename]:
            raise ValueError(f"case {case_id!r} names an unknown heading in {filename!r}: {label!r}")
        sections.append((filename, label))
    return tuple(sections)


def load_diagnostic_cases(path: Path) -> list[DiagnosticCase]:
    cases = []
    seen = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            case_id = record["id"]
            partition = record["partition"]
            query = record["query"]
            answerability = record["answerability"]
            raw_support = record["support"]
            raw_related = record["related_sections"]
            label_reason = record["label_reason"]
            review_status = record["review_status"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid calibration case on line {line_no}: {exc}") from None
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"case id on line {line_no} must be unique non-empty text")
        if (not isinstance(partition, str) or partition not in PARTITIONS
                or not isinstance(answerability, str) or answerability not in LABELS):
            raise ValueError(f"case {case_id!r} has an unknown partition or answerability")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"case {case_id!r} has an empty query")
        support = _parse_sections(case_id, raw_support, "support")
        related = _parse_sections(case_id, raw_related, "related_sections")
        if not isinstance(label_reason, str) or not label_reason.strip():
            raise ValueError(f"case {case_id!r} label_reason must be non-empty text")
        if review_status != "pending_human_review":
            raise ValueError(f"case {case_id!r} is not marked pending_human_review")
        if answerability != "unanswerable" and not support:
            raise ValueError(f"case {case_id!r} needs support sections for a positive label")
        if answerability == "unanswerable" and support:
            raise ValueError(f"negative case {case_id!r} must not list support sections")
        seen.add(case_id)
        cases.append(DiagnosticCase(case_id, partition, query, answerability,
                                    support, related, label_reason))
    if not cases:
        raise ValueError("calibration case file is empty")
    return cases


def _section_match(candidate, relevant: tuple[tuple[str, str | None], ...]) -> bool:
    filename, locator = candidate
    return any(filename == expected_file and (expected_label is None or locator == expected_label)
               for expected_file, expected_label in relevant)


def _candidate_leaders(result, metadata, relevant, *, limit=5):
    leaders = []
    ranks = {}
    for rank, candidate in enumerate(result.candidates, 1):
        row = metadata.get(candidate.chunk_id)
        if row is None:
            continue
        label = (row["source_name"], row["locator_label"])
        for section in relevant:
            key = f"{section[0]}::{section[1] or '*'}"
            if key not in ranks and _section_match(label, (section,)):
                ranks[key] = rank
        if len(leaders) < limit:
            leaders.append({"rank": rank, "filename": label[0], "locator_label": label[1],
                            "raw_score": candidate.raw_score})
    for section in relevant:
        ranks.setdefault(f"{section[0]}::{section[1] or '*'}", None)
    return {"leader_score": result.candidates[0].raw_score if result.candidates else None,
            "candidate_count": len(result.candidates), "top": leaders,
            "relevant_section_ranks": ranks}


def _selected(fused, metadata, config):
    by_text = set()
    out = []
    for item in fused:
        if item.normalized_score < config.fusion.min_score:
            continue
        row = metadata.get(item.chunk_id)
        if row is None:
            continue
        text = row["text"].strip()
        if config.fusion.dedupe and text in by_text:
            continue
        by_text.add(text)
        out.append({"filename": row["source_name"], "locator_label": row["locator_label"],
                    "chunk_id": str(item.chunk_id), "score": item.normalized_score,
                    "seams": item.seams})
    return out


def _case_metrics(case, selected):
    diagnostic_sections = case.related_sections if case.answerability == "unanswerable" else case.support
    matching = [row for row in selected
                if _section_match((row["filename"], row["locator_label"]), diagnostic_sections)]
    if case.answerability == "unanswerable":
        # Relevant sections on a negative case are topical distractors only.
        topical = bool(matching)
        coverage = None
    else:
        expected = set(case.support)
        found = {(row["filename"], row["locator_label"]) for row in matching}
        denominator = len(expected) or 1
        coverage = len({section for section in expected
                        if any(_section_match(label, (section,)) for label in found)}) / denominator
        topical = None
    return {
        "id": case.case_id, "partition": case.partition,
        "answerability": case.answerability,
        "label_reason": case.label_reason,
        "review_status": "pending_human_review",
        "returned_chunks": len(selected), "refused": not selected,
        "retrieved_relevant_section_coverage": coverage,
        "negative_topical_match": topical,
        "negative_related_sections_present": (
            bool(case.related_sections) if case.answerability == "unanswerable" else None
        ),
        "returned_sections": sorted({(row["filename"], row["locator_label"])
                                      for row in selected}),
    }


def _aggregate(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["partition"], row["answerability"])].append(row)
    summary = {}
    for (partition, answerability), cases in sorted(grouped.items()):
        positive = answerability != "unanswerable"
        summary[f"{partition}:{answerability}"] = {
            "cases": len(cases),
            "refusal_rate": sum(row["refused"] for row in cases) / len(cases),
            "mean_returned_chunks": sum(row["returned_chunks"] for row in cases) / len(cases),
            "mean_retrieved_relevant_section_coverage": (
                sum(row["retrieved_relevant_section_coverage"] for row in cases) / len(cases)
                if positive else None
            ),
            "negative_topical_match_rate": (
                sum(row["negative_topical_match"] for row in cases) / len(cases)
                if not positive else None
            ),
        }
    negatives = [row for row in rows if row["answerability"] == "unanswerable"]
    related_negatives = [row for row in negatives if row["negative_related_sections_present"]]
    unrelated_negatives = [row for row in negatives if not row["negative_related_sections_present"]]
    summary["negative_cases_overall"] = _negative_summary(negatives)
    summary["negative_cases_topically_related_sections_present"] = _negative_summary(related_negatives)
    summary["negative_cases_unrelated"] = _negative_summary(unrelated_negatives)
    return summary


def _negative_summary(cases):
    return {
        "cases": len(cases),
        "refusal_rate": sum(row["refused"] for row in cases) / len(cases) if cases else None,
        "mean_returned_chunks": (sum(row["returned_chunks"] for row in cases) / len(cases)
                                 if cases else None),
        "topical_match_rate": (sum(row["negative_topical_match"] for row in cases) / len(cases)
                               if cases else None),
    }


def _runtime_versions():
    packages = {}
    for name in ("sentence-transformers", "transformers", "torch", "huggingface-hub", "pgvector"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return packages


def _model_snapshot_hash(model_id: str) -> tuple[str, str]:
    from huggingface_hub import snapshot_download

    snapshot = Path(snapshot_download(repo_id=model_id, local_files_only=True)).resolve()
    digest = hashlib.sha256()
    for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
        relative = path.relative_to(snapshot).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(sha256(path.resolve())))
    return snapshot.name, digest.hexdigest()


def run() -> dict:
    """Execute once against an isolated local corpus; only raw retrieval runs."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from app.config import load_settings
    from app.db import queries
    from app.db.connection import open_connection
    from app.embeddings import get_embedder
    from app.ingest.pipeline import ingest_file
    from app.retrieval.config import load_retrieval_config
    from app.retrieval.fusion import fuse
    from app.retrieval.seams.embed import embed_seam
    from app.retrieval.seams.keyword import keyword_seam
    from app.retrieval.seams.prereq import prereq_seam

    cases = load_diagnostic_cases(DEFAULT_CASES)
    sources = sorted(path for path in FIXTURE.glob("*.md") if path.name != "README.md")
    source_hashes = {path.name: sha256(path) for path in sources}
    source_manifest_hash = _canonical_hash(source_hashes)
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    resolved_runs_root = RUNS_ROOT.resolve(strict=True)
    temp_root = Path(tempfile.mkdtemp(prefix="retrieval-answerability-", dir=resolved_runs_root))
    resolved_temp_root = temp_root.resolve(strict=True)
    if (not resolved_temp_root.is_relative_to(resolved_runs_root)
            or resolved_temp_root == resolved_runs_root
            or not resolved_temp_root.name.startswith("retrieval-answerability-")):
        raise RuntimeError("Temporary diagnostic directory escaped its guarded workspace root")
    try:
        data_dir = resolved_temp_root / "data"
        os.environ["DATA_DIR"] = str(data_dir)
        os.environ["PGSERVER_DATA"] = str(data_dir / "pgserver")
        settings = load_settings()
        settings = replace(settings, data_dir=data_dir, database_url=None, embedding_device="cpu")
        if settings.embedding_provider != "granite":
            raise ValueError("diagnostic requires the configured Granite embedder")
        config = load_retrieval_config(settings=settings)
        embedder = get_embedder(settings)
        model_snapshot, model_snapshot_hash = _model_snapshot_hash(settings.embedding_model_id)

        with open_connection(settings) as conn:
            collection_name = "emory_retrieval_calibration_2026_10_08"
            conn.execute(queries.get("insert_collection"), {
                "collection_id": uuid4(), "name": collection_name,
            })
            conn.commit()
            collection_id = conn.execute(queries.get("get_collection_by_name"), {
                "name": collection_name,
            }).fetchone()["collection_id"]
            source_chunks = {}
            for source in sources:
                source_id = ingest_file(
                    conn, collection_id=collection_id, path=source,
                    content_type="text/markdown", count_tokens=embedder.count_tokens,
                    embedder=embedder, embed_model=settings.embedding_model_id, config=config,
                )
                conn.commit()
                source_chunks[source_id] = conn.execute(queries.get("list_chunks_for_source"), {
                    "source_id": source_id,
                }).fetchall()
            chunk_count = sum(map(len, source_chunks.values()))
            chunk_ids = [row["chunk_id"] for rows in source_chunks.values() for row in rows]
            labels = conn.execute(queries.get("chunk_labels"), {"chunk_ids": chunk_ids}).fetchall()
            labels_by_id = {row["chunk_id"]: row for row in labels}
            metadata = {}
            for source_id, rows in source_chunks.items():
                for row in rows:
                    label = labels_by_id[row["chunk_id"]]
                    metadata[row["chunk_id"]] = {
                        "source_name": label["source_name"],
                        "locator_label": label["locator_label"], "text": row["text"],
                    }

            all_results = []
            floor_cases = {floor: [] for floor in FLOOR_SWEEP}
            for case in cases:
                keyword_all = keyword_seam(
                    conn, collection_id, case.query,
                    replace(config.seams.keyword, enabled=True, limit=chunk_count),
                )
                embed_all = embed_seam(
                    conn, collection_id, case.query, settings.embedding_model_id,
                    replace(config.seams.embed, enabled=True, limit=chunk_count,
                            min_similarity=-1.0), embedder=embedder,
                )
                prereq = prereq_seam(conn, collection_id, case.query, config.seams.prereq)
                keyword_diagnostic = _candidate_leaders(
                    keyword_all, metadata, case.related_sections if case.answerability == "unanswerable"
                    else case.support,
                )
                embed_diagnostic = _candidate_leaders(
                    embed_all, metadata, case.related_sections if case.answerability == "unanswerable"
                    else case.support,
                )

                current_results = _simulate_results(
                    config, keyword_all, embed_all, prereq, floor=config.seams.embed.min_similarity,
                )
                baseline = _case_metrics(case, _selected(
                    fuse(current_results, config.fusion.cited_set_size,
                         config.seams.embed.only_quota), metadata, config,
                ))
                baseline["keyword"] = keyword_diagnostic
                baseline["embed"] = embed_diagnostic
                all_results.append(baseline)

                for floor in FLOOR_SWEEP:
                    results = _simulate_results(config, keyword_all, embed_all, prereq, floor=floor)
                    selected = _selected(
                        fuse(results, config.fusion.cited_set_size,
                             config.seams.embed.only_quota), metadata, config,
                    )
                    floor_cases[floor].append(_case_metrics(case, selected))
    finally:
        cleanup_target = temp_root.resolve(strict=True)
        if (not cleanup_target.is_relative_to(resolved_runs_root)
                or cleanup_target == resolved_runs_root
                or not cleanup_target.name.startswith("retrieval-answerability-")):
            raise RuntimeError("Refusing to remove diagnostic directory outside guarded workspace root")
        shutil.rmtree(cleanup_target)

    environment = {
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": _runtime_versions(), "embedding_provider": settings.embedding_provider,
        "embedding_model_id": settings.embedding_model_id,
        "embedding_device": settings.embedding_device,
        "embedding_max_tokens": settings.embedding_max_tokens,
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        "transformers_offline": os.environ.get("TRANSFORMERS_OFFLINE"),
    }
    return {
        "status": "manual_local_diagnostic",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "script_sha256": sha256(Path(__file__).resolve()),
        "cases_sha256": sha256(DEFAULT_CASES),
        "source_hashes": source_hashes,
        "source_manifest_sha256": source_manifest_hash,
        "config_sha256": sha256(CONFIG_PATH),
        "config_version": config.version,
        "model": {"id": settings.embedding_model_id,
                  "snapshot_revision": model_snapshot,
                  "snapshot_sha256": model_snapshot_hash},
        "environment": environment,
        "environment_sha256": _canonical_hash(environment),
        "diagnostics": all_results,
        "aggregate": _aggregate(all_results),
        "embed_floor_sweep": {
            str(floor): {"method": "offline simulation from one captured broad candidate set",
                         "aggregate": _aggregate(rows), "cases": rows}
            for floor, rows in floor_cases.items()
        },
        "guardrails": [
            "Read-only diagnostic: no production config or retrieval floors are changed.",
            "Raw section overlap and fused retrieval coverage are not answer correctness.",
            "Unanswerable related_sections are topical diagnostics only, never positive evidence.",
            "No empty-source scope is used to represent an unanswerable query.",
            "Calibration and holdout results are reported separately; no floor is selected here.",
            "No generation model, answer validation, prerequisite editing, or network access is used.",
        ],
    }


def _simulate_results(config, keyword_all, embed_all, prereq, *, floor):
    keyword = replace(keyword_all, enabled=config.seams.keyword.enabled,
                      candidates=(keyword_all.candidates[:config.seams.keyword.limit]
                                  if config.seams.keyword.enabled else []))
    embed_candidates = [candidate for candidate in embed_all.candidates
                        if candidate.raw_score >= floor][:config.seams.embed.limit]
    embed = replace(embed_all, enabled=config.seams.embed.enabled,
                    candidates=embed_candidates if config.seams.embed.enabled else [])
    prerequisite = replace(prereq, candidates=(prereq.candidates
                                               if config.seams.prereq.enabled else []),
                           enabled=config.seams.prereq.enabled)
    return [keyword, embed, prerequisite]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
