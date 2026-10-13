"""Manual catalog-backed, grouped k-fold probes; no generation calls by default.

Captures storage, vector integrity, broad keyword/embedding ranks, production
fusion, and candidate scoring ablations from the existing catalog. Course target
labels are explicit draft relevance judgments, not career-outcome evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

import numpy as np

from app.config import load_settings
from app.db import queries
from app.db.connection import connect
from app.embeddings import get_embedder
from app.retrieval import fusion as fusion_module
from app.retrieval.config import load_retrieval_config
from app.retrieval.fusion import fuse
from app.retrieval.seams import keyword as keyword_module
from app.retrieval.seams.embed import embed_seam
from app.retrieval.seams.prereq import prereq_seam
from app.retrieval.types import Candidate

ROOT = Path(__file__).resolve().parents[3]
FAMILIES = [
    ("macro", ["ECON 112", "ECON 410", "ECON 422"], [
        "I want a career in macroeconomics. Which courses in the current catalog should I explore?",
        "I'm interested in inflation, central banks, and economic growth. What should I take?",
        "Use the deterministic sequence tool to order ECON 101 and ECON 112. I have completed neither.",
        "I have completed ECON 101. What is the next dependency stage for ECON 112?",
    ]),
    ("data", ["CS 377", "DATASCI 250", "DATASCI 350"], [
        "I want to become a data engineer. Suggest relevant courses from this catalog.",
        "I want to build reliable data pipelines and databases. Which catalog courses cover relevant topics?",
    ]),
    ("security", ["CS 453"], [
        "I am interested in cybersecurity. Which courses here would help me explore it?",
        "Find courses on protecting computer systems, cryptography, and network security.",
    ]),
    ("biotech", ["BIOL 368L", "BIOL 465"], [
        "I want to explore biotechnology. Recommend courses supported by their catalog descriptions.",
        "Which lab teaches gene cloning, recombinant protein purification, and cell culture?",
    ]),
    ("climate", ["PHYS 125", "SUST 125", "ENVS 326"], [
        "Which courses should I explore if I am interested in climate science?",
        "Where can I study the science of global warming and environmental change?",
    ]),
    ("pde", ["MATH 351", "MATH 352"], [
        "Which courses teach partial differential equations and their applications?",
        "I want to model waves and diffusion with PDEs. What courses should I explore?",
    ]),
    ("genetics", ["BIOL 264", "BIOL 441"], [
        "I want to understand genes and genomics. Which courses should I explore?",
        "Find catalog courses in human genetics and molecular evolution.",
    ]),
    ("biochemistry", ["BIOL 301", "CHEM 340"], [
        "Which courses study proteins, metabolism, and the chemistry of living systems?",
        "I want a foundation in biochemistry. What courses are relevant here?",
    ]),
    ("neuroscience", ["PSYC 335", "NBB 302", "NBB 317", "NBB 220", "MUS 220"], [
        "What should I take to explore cognitive neuroscience and brain mechanisms?",
        "Find courses on the brain systems involved in perception and memory.",
    ]),
    ("machine_learning", ["CS 323", "CS 211", "CS 334", "CS 325"], [
        "I want to study machine learning and artificial intelligence. Suggest courses.",
        "Which courses teach regression, classification, clustering, and neural networks?",
    ]),
    ("calculus", ["MATH 112"], [
        "What are the prerequisite alternatives listed for MATH 112?",
        "I completed MATH 111 and MATH 119. Use the deterministic sequence tool to check MATH 112.",
    ]),
]
# Query scaffolding is not topical evidence. Keep domain terms and course codes.
SCAFFOLD = keyword_module.QUERY_SCAFFOLD


def legacy_tokens(query, minimum):
    return list(dict.fromkeys(t for t in re.findall(r"[a-z0-9]+", query.lower())
                             if len(t) >= minimum or t.isdigit()))


def grouped_folds(groups: list[str], k: int) -> dict[str, int]:
    if k < 2 or k > len(set(groups)):
        raise ValueError("k must be between two and the number of independent families")
    ordered = sorted(set(groups), key=lambda group: hashlib.sha256(group.encode()).hexdigest())
    return {group: index % k for index, group in enumerate(ordered)}


def vector_array(value):
    return value.to_numpy() if hasattr(value, "to_numpy") else np.asarray(value, dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON capture; otherwise print results")
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output already exists")
    settings = load_settings()
    if not settings.database_url:
        port = (ROOT / "data/pgserver/postmaster.pid").read_text().splitlines()[3]
        settings = replace(settings, database_url=f"postgresql://postgres:@127.0.0.1:{port}/postgres")
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    folds = grouped_folds([family[0] for family in FAMILIES], args.folds)
    cached = {r["code"]: r for r in map(json.loads,
              (ROOT / "data/atlas/emory_college.jsonl").read_text(encoding="utf-8").splitlines())}
    production_tokens = keyword_module._tokens
    original_tokens = legacy_tokens
    original_normalize = fusion_module._normalize
    report = {"started_at": datetime.now(UTC).isoformat(), "folds": folds,
              "labels": "draft catalog topic relevance; no employment-outcome claims",
              "cases_previously_seen": True,
              "label_corrections": "AI relevance includes CS 334 (Machine Learning) and CS 325 (Artificial Intelligence); the narrower draft label incorrectly treated their retrieval as a miss.",
              "limitations": "Grouped CV separates families for scoring selection; these exploratory cases are not a pristine untouched benchmark.",
              "embedding_model": settings.embedding_model_id, "cases": []}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        if args.output:
            args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    try:
        with connect(settings.database_url) as conn:
            collection_id = conn.execute(queries.get("get_collection_by_name"),
                                         {"name": settings.default_collection}).fetchone()["collection_id"]
            rows = conn.execute(queries.get("diagnostic_current_chunks"), {
                "collection_id": collection_id, "model": settings.embedding_model_id,
                "workspace_key": "development"}).fetchall()
            metadata = {r["chunk_id"]: r for r in rows}
            vector_rows = [r for r in rows if r["embedding"] is not None]
            vectors = np.stack([vector_array(r["embedding"]) for r in vector_rows])
            norms = np.linalg.norm(vectors, axis=1)
            report["storage"] = {"chunks": len(rows), "missing_embeddings": len(rows)-len(vector_rows),
                "dimension": vectors.shape[1], "nonfinite_vectors": int((~np.isfinite(vectors).all(axis=1)).sum()),
                "norm_min": float(norms.min()), "norm_max": float(norms.max()),
                "catalog_records": len({r["code"] for r in rows if r["code"]})}
            report["description_only_requirements"] = sorted(r["code"] for r in rows
                if r["code"] and not r["requisites_raw"]
                and re.search(r"\b(?:pre|co)[ -]?requisites?\s*:", r["text"], re.IGNORECASE))
            report["catalog_sha256"] = hashlib.sha256(json.dumps([
                (str(r["chunk_id"]), r["text"]) for r in rows]).encode()).hexdigest()
            report["runner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            report["retrieval_config_sha256"] = hashlib.sha256(
                (ROOT / "backend/configs/retrieval.toml").read_bytes()).hexdigest()
            targets = {code for _, codes, _ in FAMILIES for code in codes}
            target_rows = [r for r in rows if r["code"] in targets]
            report["target_storage"] = [{"code": code, "chunks": len(found),
                "cached_record_present": code in cached,
                "requisites_agree": all(r["requisites_raw"] == cached.get(code,{}).get("requisites") for r in found),
                "description_present": any(cached.get(code,{}).get("description", "") in r["text"] for r in found),
                "parse_status": sorted({r["parse_status"] for r in found})}
                for code in sorted(targets) for found in [[r for r in target_rows if r["code"] == code]]]
            sample = [r for r in target_rows if r["embedding"] is not None][:8]
            rebuilt = embedder.embed_documents([r["text"] for r in sample])
            report["vector_reproduction"] = [{"code": r["code"],
                "cosine": float(np.dot(vector_array(r["embedding"]), v)),
                "max_absolute_difference": float(np.max(np.abs(vector_array(r["embedding"])-v)))}
                for r,v in zip(sample,rebuilt,strict=True)]
            conn.commit()
            for group, targets, questions in FAMILIES:
                for index, query in enumerate(questions):
                    expected = targets
                    if group == "biotech" and index == 1:
                        expected = ["BIOL 368L"]
                    elif group == "neuroscience" and index == 1:
                        expected = ["PSYC 335", "NBB 220", "MUS 220"]
                    qvec = embedder.embed_query(query)
                    embedding = embed_seam(conn, collection_id, query, settings.embedding_model_id,
                        replace(config.seams.embed, limit=len(rows), min_similarity=-1), query_vec=qvec,
                        workspace_key="development")
                    prereq = prereq_seam(conn, collection_id, query, config.seams.prereq,
                                          workspace_key="development")
                    keyword_module._tokens = original_tokens
                    keyword = keyword_module.keyword_seam(conn, collection_id, query, config.seams.keyword,
                                                          workspace_key="development")
                    keyword_module._tokens = lambda text, minimum: [t for t in original_tokens(text, minimum) if t not in SCAFFOLD]
                    content = keyword_module.keyword_seam(conn, collection_id, query, config.seams.keyword,
                                                          workspace_key="development")
                    limited = replace(embedding, candidates=[c for c in embedding.candidates
                        if c.raw_score >= config.seams.embed.min_similarity][:config.seams.embed.limit])
                    variants = {}
                    keyword_variants = [("baseline",keyword),("content_tokens",content)]
                    tokens = keyword_module._tokens(query,config.seams.keyword.min_token_len)
                    for weight in (1,2,4):
                        weighted_rows = conn.execute(queries.get("diagnostic_keyword_weighted"), {
                            "collection_id":collection_id,"workspace_key":"development",
                            "q":" | ".join(tokens),"title_weight":weight,"limit":config.seams.keyword.limit,
                        }).fetchall() if tokens else []
                        kw = replace(content,candidates=[Candidate(r["chunk_id"],r["source_id"],
                                     r["chunk_index"],"keyword",float(r["score"])) for r in weighted_rows])
                        keyword_variants.append((f"title_weight_{weight}",kw))
                    for weight in (0, 1, 4):
                        weighted_rows = conn.execute(queries.get("diagnostic_keyword_idf"), {
                            "collection_id": collection_id, "workspace_key": "development",
                            "tokens": tokens, "title_weight": weight,
                            "limit": config.seams.keyword.limit,
                        }).fetchall() if tokens else []
                        kw = replace(content, candidates=[Candidate(r["chunk_id"], r["source_id"],
                            r["chunk_index"], "keyword", float(r["score"])) for r in weighted_rows])
                        keyword_variants.append((f"idf_title_{weight}", kw))
                    for name, kw in keyword_variants:
                        selected = fuse([kw,limited,prereq],config.fusion.cited_set_size,config.seams.embed.only_quota)
                        selected = [c for c in selected if c.normalized_score >= config.fusion.min_score]
                        labels = [metadata[c.chunk_id]["locator_label"] for c in selected]
                        variants[name] = {"labels": labels, "target_hit": any(label in expected for label in labels),
                            "target_count": len(set(labels) & set(expected)),
                            "target_recall": len(set(labels) & set(expected)) / len(expected),
                            "course_count": sum(metadata[c.chunk_id]["code"] is not None for c in selected)}
                    for name, kw in keyword_variants:
                        def normalize(seam, candidates):
                            if seam != "keyword" or not candidates:
                                return original_normalize(seam, candidates)
                            high = max(c.raw_score for c in candidates)
                            return {c.chunk_id: c.raw_score / high if high else 0
                                    for c in candidates}
                        fusion_module._normalize = normalize
                        try:
                            selected = fuse([kw, limited, prereq], config.fusion.cited_set_size,
                                            config.seams.embed.only_quota)
                        finally:
                            fusion_module._normalize = original_normalize
                        selected = [c for c in selected if c.normalized_score >= config.fusion.min_score]
                        labels = [metadata[c.chunk_id]["locator_label"] for c in selected]
                        variants[name + "_max_scale"] = {
                            "labels": labels, "target_hit": any(label in expected for label in labels),
                            "target_count": len(set(labels) & set(expected)),
                            "target_recall": len(set(labels) & set(expected)) / len(expected),
                            "course_count": sum(metadata[c.chunk_id]["code"] is not None for c in selected)}
                    clean_query = " ".join(t for t in tokens if t not in {
                        "to", "for", "from", "in", "of", "by", "their", "me", "my", "am",
                        "the", "an", "and", "or", "with", "this", "it", "on", "are", "if",
                    })
                    clean_embedding = embed_seam(conn, collection_id, clean_query or query,
                        settings.embedding_model_id, config.seams.embed,
                        embedder=embedder, workspace_key="development")
                    for name, kw in [("clean_embedding", keyword), ("clean_embedding_content", content)]:
                        selected = fuse([kw, clean_embedding, prereq], config.fusion.cited_set_size,
                                        config.seams.embed.only_quota)
                        selected = [c for c in selected if c.normalized_score >= config.fusion.min_score]
                        labels = [metadata[c.chunk_id]["locator_label"] for c in selected]
                        variants[name] = {"labels": labels,
                            "target_hit": any(label in expected for label in labels),
                            "target_count": len(set(labels) & set(expected)),
                            "target_recall": len(set(labels) & set(expected)) / len(expected),
                            "course_count": sum(metadata[c.chunk_id]["code"] is not None for c in selected)}
                    ranks = {code: next((n for n,c in enumerate(embedding.candidates,1)
                        if metadata[c.chunk_id]["code"] == code), None) for code in targets}
                    row = {"id":f"{group}-{index}","group":group,"fold":folds[group],"query":query,
                           "expected_any":expected,"embedding_ranks":ranks,
                           "keyword_tokens":original_tokens(query,config.seams.keyword.min_token_len),
                           "content_tokens":keyword_module._tokens(query,config.seams.keyword.min_token_len),
                           "clean_embedding_query": clean_query,
                           "embedding_top10":[{"label":metadata[c.chunk_id]["locator_label"],"score":c.raw_score}
                                              for c in embedding.candidates[:10]],
                           "keyword_leaders":{name:[{"label":metadata[c.chunk_id]["locator_label"],"score":c.raw_score}
                                                     for c in kw.candidates[:6]] for name,kw in keyword_variants},
                           "variants":variants}
                    report["cases"].append(row)
                    conn.commit()
                    save()
                    print(json.dumps({"id":row["id"],"embedding_target_rank":min((r for r in ranks.values() if r),default=None),
                                      "baseline_hit":variants["baseline"]["target_hit"],
                                      "content_hit":variants["content_tokens"]["target_hit"]}),flush=True)
    finally:
        keyword_module._tokens = production_tokens
        fusion_module._normalize = original_normalize
        report["finished_at"] = datetime.now(UTC).isoformat()
        if report["cases"]:
            names = list(report["cases"][0]["variants"])
            report["aggregate"] = {name:{"target_hit_rate":mean(c["variants"][name]["target_hit"] for c in report["cases"]),
                "mean_target_recall":mean(c["variants"][name]["target_recall"] for c in report["cases"]),
                "mean_course_chunks":mean(c["variants"][name]["course_count"] for c in report["cases"])} for name in names}
            report["cross_validation"] = []
            for fold in range(args.folds):
                train = [c for c in report["cases"] if c["fold"] != fold]
                test = [c for c in report["cases"] if c["fold"] == fold]
                if not train or not test:
                    continue
                chosen = max(names,key=lambda name:(mean(c["variants"][name]["target_hit"] for c in train),
                                                    mean(c["variants"][name]["target_recall"] for c in train),name))
                report["cross_validation"].append({"fold":fold,"training_case_count":len(train),
                    "heldout_case_count":len(test),"selected_using_training_only":chosen,
                    "heldout_target_hit_rate":mean(c["variants"][chosen]["target_hit"] for c in test),
                    "heldout_target_recall":mean(c["variants"][chosen]["target_recall"] for c in test),
                    "baseline_heldout_target_recall":mean(c["variants"]["baseline"]["target_recall"] for c in test),
                    "baseline_heldout_target_hit_rate":mean(c["variants"]["baseline"]["target_hit"] for c in test)})
        save()
        if not args.output and report.get("aggregate"):
            print(json.dumps(report["aggregate"]), flush=True)


if __name__ == "__main__":
    main()
