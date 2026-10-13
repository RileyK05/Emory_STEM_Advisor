"""Manual bounded live generation from oracle, embedding, and fused contexts.

Oracle passages are explicit diagnostic labels, never a production retrieval
fallback. Uses the same question and generation prompt at each layer. Saves
rejected output too so citation failure is distinguishable from missing facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.db import queries
from app.db.connection import connect
from app.embeddings import get_embedder
from app.generation import answer as answer_module
from app.generation.answer import GenerationRejected, answer_from_context
from app.llm import get_provider
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import retrieve
from app.retrieval.seams.embed import embed_seam
from app.retrieval.types import CitedChunk, Retrieved
from scripts.manual.advising_layers import FAMILIES, ROOT
from scripts.smoke.live_advising import FORMAT_GUIDANCE, RecordingProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON capture; otherwise print answers")
    parser.add_argument("--group", action="append", choices=[f[0] for f in FAMILIES])
    parser.add_argument("--format-guidance", action="store_true")
    parser.add_argument("--repair-citations", action="store_true")
    parser.add_argument("--max-calls", type=int, default=9)
    parser.add_argument("--token-budget", type=int, default=100000)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output already exists")
    settings = load_settings()
    if settings.llm_provider == "hf-local" or not settings.llm_api_key:
        parser.error("configure the real cloud route locally")
    if not settings.database_url:
        port = (ROOT / "data/pgserver/postmaster.pid").read_text().splitlines()[3]
        settings = replace(settings, database_url=f"postgresql://postgres:@127.0.0.1:{port}/postgres")
    settings = replace(settings, llm_timeout_seconds=120,
                       llm_max_output_tokens=args.max_output_tokens)
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    provider = RecordingProvider(get_provider(settings), max_calls=args.max_calls,
                                 token_budget=args.token_budget)
    if args.format_guidance:
        answer_module.SYSTEM_PROMPT += FORMAT_GUIDANCE
        answer_module.PROMPT_VERSION += "-layer-format"
    report = {"started_at": datetime.now(UTC).isoformat(), "cases": [],
              "prompt_version": answer_module.PROMPT_VERSION,
              "prompt_sha256": hashlib.sha256(answer_module.SYSTEM_PROMPT.encode()).hexdigest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "retrieval_config_sha256": hashlib.sha256(
                  (ROOT / "backend/configs/retrieval.toml").read_bytes()).hexdigest(),
              "limits": {"calls": args.max_calls, "accounted_tokens": args.token_budget,
                         "output_tokens": args.max_output_tokens},
              "tools_enabled": False, "oracle_is_diagnostic_only": True}
    report["citation_repair_enabled"] = args.repair_citations
    report["compatible_thinking"] = settings.llm_compatible_thinking
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report["provider_attempts"] = provider.calls
        report["accounted_tokens"] = provider.tokens
        if args.output:
            args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    provider.on_update = save
    try:
        with connect(settings.database_url) as conn:
            collection_id = conn.execute(queries.get("get_collection_by_name"),
                {"name": settings.default_collection}).fetchone()["collection_id"]
            rows = conn.execute(queries.get("diagnostic_current_chunks"), {
                "collection_id": collection_id, "model": settings.embedding_model_id,
                "workspace_key": "development"}).fetchall()
            metadata = {r["chunk_id"]: r for r in rows}
            report["catalog_sha256"] = hashlib.sha256(json.dumps([
                (str(r["chunk_id"]), r["text"]) for r in rows]).encode()).hexdigest()
            conn.commit()

            def cite(row, rank):
                return CitedChunk(row["chunk_id"], row["source_id"], row["chunk_index"],
                    row["source_name"], row["locator_label"], row["text"],
                    ("diagnostic",), 1., 1, rank)

            for group, targets, questions in FAMILIES:
                if group not in (args.group or ["macro", "data", "biotech"]):
                    continue
                query = questions[0]
                oracle = [cite(r, i + 1) for i, r in enumerate(rows) if r["code"] in targets]
                embedded = embed_seam(conn, collection_id, query, settings.embedding_model_id,
                    config.seams.embed, embedder=embedder, workspace_key="development")
                embedding = [cite(metadata[c.chunk_id], i + 1)
                             for i, c in enumerate(embedded.candidates[:6])]
                retrieved = retrieve(conn, collection_id=collection_id, query=query,
                    embedder=embedder, embed_model=settings.embedding_model_id, config=config,
                    workspace_key="development")
                fused = retrieved.chunks if isinstance(retrieved, Retrieved) else []
                conn.commit()  # No open DB transaction during model I/O.
                for layer, chunks in [("oracle", oracle), ("embedding", embedding), ("fused", fused)]:
                    provider.case_id = f"{group}-{layer}"
                    row = {"id": provider.case_id, "query": query, "layer": layer,
                           "context": [{"id": str(c.chunk_id), "label": c.locator_label,
                                        "text": c.text} for c in chunks]}
                    report["cases"].append(row)
                    try:
                        answer = answer_from_context(chunks, query, provider=provider,
                            max_new_tokens=args.max_output_tokens, timeout_seconds=120,
                            repair_citations=args.repair_citations)
                        row.update({"accepted": True, "text": answer.text})
                    except GenerationRejected as exc:
                        row.update({"accepted": False, "reason": str(exc), "text": exc.answer.text})
                    except Exception as exc:
                        row.update({"accepted": False, "error_type": type(exc).__name__})
                        raise
                    finally:
                        save()
                    print(json.dumps({"id": row["id"], "accepted": row["accepted"],
                                      "text": row.get("text"), "reason": row.get("reason")}),
                          flush=True)
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    main()
