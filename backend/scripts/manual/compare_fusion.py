"""Compare pre-soft-cap and current fusion on the dated local retrieval fixture.

Run from backend with cached Granite and the configured local PostgreSQL runtime.
This manual check downloads no model weights and is deliberately outside pytest.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = BACKEND_ROOT.parent
FIXTURE_ROOT = BACKEND_ROOT / "tests/fixtures/retrieval_eval_2026_10_08"
RUNS_ROOT = (WORKSPACE_ROOT / "data/manual-eval-runs").resolve()


def _old_fuse(results, k, quota):
    """Pre-soft-cap implementation copied for this fixed comparison."""
    from app.retrieval.fusion import _normalize

    normalized = {}
    provenance = {}
    meta = {}
    for result in results:
        scores = _normalize(result.name, result.candidates)
        for candidate in result.candidates:
            chunk_id = candidate.chunk_id
            score = scores[chunk_id]
            if chunk_id not in normalized or score > normalized[chunk_id]:
                normalized[chunk_id] = score
            provenance.setdefault(chunk_id, set()).add(result.name)
            meta.setdefault(chunk_id, candidate)

    grounded = any(result.candidates and result.name in {"keyword", "prereq"} for result in results)
    if grounded:
        embed_only = sorted(
            (chunk_id for chunk_id, seams in provenance.items() if seams == {"embed"}),
            key=lambda chunk_id: (
                -normalized[chunk_id],
                str(meta[chunk_id].source_id),
                meta[chunk_id].chunk_index,
            ),
        )
        for chunk_id in embed_only[quota:]:
            del normalized[chunk_id]
            del provenance[chunk_id]
            del meta[chunk_id]

    by_source = {}
    for chunk_id in normalized:
        by_source.setdefault(meta[chunk_id].source_id, []).append(chunk_id)
    weights = {
        source: max(normalized[chunk_id] for chunk_id in chunk_ids)
        for source, chunk_ids in by_source.items()
    }
    counts = {source: len(chunk_ids) for source, chunk_ids in by_source.items()}
    order = sorted(by_source, key=lambda source: (-weights[source], str(source)))
    source_slot = {source: index + 1 for index, source in enumerate(order)}

    def largest_remainder(total):
        allocation = {source: 0 for source in order}
        if total <= 0 or not order:
            return allocation
        total_weight = sum(weights.values())
        if total_weight <= 0:
            for index, source in enumerate(order):
                allocation[source] = total // len(order) + (1 if index < total % len(order) else 0)
            return allocation
        raw = {source: total * weights[source] / total_weight for source in order}
        for source in order:
            allocation[source] = int(raw[source])
        remainder = total - sum(allocation.values())
        ranked = sorted(
            order,
            key=lambda source: (-(raw[source] - int(raw[source])), -weights[source], str(source)),
        )
        for source in ranked[:remainder]:
            allocation[source] += 1
        return allocation

    if not order:
        slots = {}
    elif k <= len(order):
        slots = {source: 0 for source in order}
        for source in order[:k]:
            slots[source] = 1
    else:
        slots = {source: 1 for source in order}
        extra = largest_remainder(k - len(order))
        for source in order:
            slots[source] += extra[source]
            slots[source] = min(slots[source], counts[source])
        capacity = {source: counts[source] - slots[source] for source in order}
        free = k - sum(slots.values())
        while free > 0:
            available = [source for source in order if capacity[source] > 0]
            if not available:
                break
            source = min(available, key=lambda item: (-weights[item], str(item)))
            slots[source] += 1
            capacity[source] -= 1
            free -= 1

    selected = []
    for source in order:
        ranked = sorted(
            by_source[source],
            key=lambda chunk_id: (
                -normalized[chunk_id],
                meta[chunk_id].chunk_index,
                str(chunk_id),
            ),
        )
        selected.extend(ranked[: slots[source]])
    if len(selected) < k:
        selected_set = set(selected)
        remaining = sorted(
            (chunk_id for chunk_id in normalized if chunk_id not in selected_set),
            key=lambda chunk_id: (
                -normalized[chunk_id],
                str(meta[chunk_id].source_id),
                meta[chunk_id].chunk_index,
            ),
        )
        selected.extend(remaining[: k - len(selected)])
    selected.sort(
        key=lambda chunk_id: (
            -normalized[chunk_id],
            str(meta[chunk_id].source_id),
            meta[chunk_id].chunk_index,
        )
    )
    return [
        (
            chunk_id,
            meta[chunk_id],
            normalized[chunk_id],
            tuple(sorted(provenance[chunk_id])),
            source_slot[meta[chunk_id].source_id],
        )
        for chunk_id in selected
    ]


def _label(conn, chunk_id, queries):
    row = conn.execute(
        queries.get("cited_chunk_details"),
        {"chunk_id": chunk_id, "workspace_key": None},
    ).fetchone()
    return row["source_name"], row["locator_label"], row["text"]


def _compare():
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from app.config import load_settings
    from app.db import queries
    from app.db.connection import open_connection
    from app.embeddings import get_embedder
    from app.ingest.pipeline import ingest_file
    from app.retrieval.config import load_retrieval_config
    from app.retrieval.retrieve import retrieve
    from app.retrieval.seams.embed import embed_seam
    from app.retrieval.seams.keyword import keyword_seam
    from app.retrieval.seams.prereq import prereq_seam
    from app.retrieval.types import Retrieved

    cases = [
        json.loads(line)
        for line in (FIXTURE_ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    resolved_runs_root = RUNS_ROOT.resolve(strict=True)
    temp_root = Path(tempfile.mkdtemp(prefix="compare-soft-cap-", dir=resolved_runs_root))
    resolved_temp_root = temp_root.resolve(strict=True)
    if (
        not resolved_temp_root.is_relative_to(resolved_runs_root)
        or resolved_temp_root == resolved_runs_root
    ):
        raise RuntimeError("Temporary comparison directory escaped its workspace-owned root")

    try:
        data_dir = resolved_temp_root / "data"
        os.environ["DATA_DIR"] = str(data_dir)
        os.environ["PGSERVER_DATA"] = str(data_dir / "pgserver")
        settings = replace(
            load_settings(),
            data_dir=data_dir,
            database_url=None,
            embedding_device="cpu",
        )
        embedder = get_embedder(settings)
        config = load_retrieval_config(settings=settings)
        with open_connection(settings) as conn:
            collection_name = "emory_eval_2026_10_08"
            conn.execute(
                queries.get("insert_collection"),
                {"collection_id": uuid4(), "name": collection_name},
            )
            conn.commit()
            collection_id = conn.execute(
                queries.get("get_collection_by_name"), {"name": collection_name}
            ).fetchone()["collection_id"]
            source_ids = {}
            for source_path in sorted(FIXTURE_ROOT.glob("*.md")):
                if source_path.name == "README.md":
                    continue
                source_ids[source_path.name] = ingest_file(
                    conn,
                    collection_id=collection_id,
                    path=source_path,
                    content_type="text/markdown",
                    count_tokens=embedder.count_tokens,
                    embedder=embedder,
                    embed_model=settings.embedding_model_id,
                    config=config,
                )
                conn.commit()

            reports = []
            for case in cases:
                allowed = (
                    [source_ids[name] for name in case["scope_sources"]]
                    if case.get("scope_sources")
                    else ([] if case["case_type"] == "empty_scope" else None)
                )
                seams = [
                    keyword_seam(
                        conn, collection_id, case["query"], config.seams.keyword, source_ids=allowed
                    ),
                    embed_seam(
                        conn,
                        collection_id,
                        case["query"],
                        settings.embedding_model_id,
                        config.seams.embed,
                        source_ids=allowed,
                        embedder=embedder,
                    ),
                    prereq_seam(
                        conn, collection_id, case["query"], config.seams.prereq, source_ids=allowed
                    ),
                ]
                before = _old_fuse(
                    seams, config.fusion.cited_set_size, config.seams.embed.only_quota
                )
                old_labels = []
                old_texts = set()
                old_returned = []
                for chunk_id, _, score, _, _ in before:
                    if config.fusion.min_score > 0 and score < config.fusion.min_score:
                        continue
                    source_name, locator, text = _label(conn, chunk_id, queries)
                    if config.fusion.dedupe and text.strip() in old_texts:
                        continue
                    old_texts.add(text.strip())
                    old_labels.append([source_name, locator])
                    old_returned.append([str(chunk_id), source_name, locator])

                outcome = retrieve(
                    conn,
                    collection_id=collection_id,
                    query=case["query"],
                    embedder=embedder,
                    embed_model=settings.embedding_model_id,
                    config=config,
                    source_ids=allowed,
                )
                new_labels = (
                    [[chunk.source_name, chunk.locator_label] for chunk in outcome.chunks]
                    if isinstance(outcome, Retrieved)
                    else []
                )
                new_returned = (
                    [
                        [str(chunk.chunk_id), chunk.source_name, chunk.locator_label]
                        for chunk in outcome.chunks
                    ]
                    if isinstance(outcome, Retrieved)
                    else []
                )
                expected = [(item["filename"], item["locator_label"]) for item in case["expected"]]
                old_hits = len(set(map(tuple, old_labels)) & set(expected))
                new_hits = len(set(map(tuple, new_labels)) & set(expected))
                reports.append(
                    {
                        "id": case["id"],
                        "case_type": case["case_type"],
                        "expected_count": len(expected),
                        "before_recall": old_hits / len(expected) if expected else None,
                        "after_recall": new_hits / len(expected) if expected else None,
                        "before_labels": old_labels,
                        "after_labels": new_labels,
                        "before_returned": old_returned,
                        "after_returned": new_returned,
                        "before_refused": not bool(old_labels),
                        "after_refused": not isinstance(outcome, Retrieved),
                        "regressed_expected": [
                            list(item)
                            for item in expected
                            if item in set(map(tuple, old_labels))
                            and item not in set(map(tuple, new_labels))
                        ],
                        "recovered_expected": [
                            list(item)
                            for item in expected
                            if item not in set(map(tuple, old_labels))
                            and item in set(map(tuple, new_labels))
                        ],
                    }
                )

        positive = [item for item in reports if item["expected_count"]]
        return {
            "case_count": len(reports),
            "positive_cases": len(positive),
            "before_fused_recall": sum(item["before_recall"] for item in positive) / len(positive),
            "after_fused_recall": sum(item["after_recall"] for item in positive) / len(positive),
            "changed_cases": [
                item
                for item in reports
                if item["before_labels"] != item["after_labels"]
                or item["before_refused"] != item["after_refused"]
            ],
            "all_cases": reports,
            "negative_cases": [item for item in reports if not item["expected_count"]],
        }
    finally:
        resolved_temp_root = temp_root.resolve(strict=True)
        if (
            not resolved_temp_root.is_relative_to(resolved_runs_root)
            or resolved_temp_root == resolved_runs_root
        ):
            raise RuntimeError(
                "Refusing to remove comparison directory outside its workspace-owned root"
            )
        shutil.rmtree(resolved_temp_root)


def main():
    print(json.dumps(_compare(), indent=2))


if __name__ == "__main__":
    main()
