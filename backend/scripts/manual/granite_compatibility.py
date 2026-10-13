"""Manual offline compatibility audit for the guarded Granite embedding loader.

Run from backend with the pinned development environment and cached weights:
    ../.venv/Scripts/python.exe -m scripts.manual.granite_compatibility
This is a manual audit, not a pytest test; it performs model inference.
"""

import hashlib
import json
import os
from pathlib import Path

# Set offline cache defaults before importing the application config/provider factory.
BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
os.environ.setdefault("HF_HOME", str(Path(os.environ.get("DATA_DIR", REPO / "data")) / "hf-cache"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np
import sentence_transformers
import torch
import transformers

from app.config import load_settings
from app.embeddings import get_embedder


def main() -> None:
    evidence_path = REPO / "docs" / "evidence" / "granite-loader-compatibility-2026-10-08.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    baseline = evidence["conditions"]
    settings = load_settings()
    actual = {
        "embedding_provider": settings.embedding_provider,
        "model_id": settings.embedding_model_id,
        "device": settings.embedding_device,
        "max_tokens": settings.embedding_max_tokens,
        "sentence_transformers": sentence_transformers.__version__,
        "transformers": transformers.__version__,
        "torch": torch.__version__,
    }
    expected = {
        "embedding_provider": "granite",
        "model_id": baseline["model_id"],
        "device": baseline["device"],
        "max_tokens": baseline["configured_ceiling_tokens"],
        "sentence_transformers": baseline["sentence_transformers_candidate"],
        "transformers": baseline["transformers"],
        "torch": baseline["torch"],
    }
    if actual != expected:
        raise SystemExit(f"audit environment mismatch: {actual}")

    embedder = get_embedder(settings)
    texts = [
        "Which calculus courses should I finish before taking multivariable analysis?",
        "MATH 211 covers integral calculus, sequences, series, and applications.",
        "MATH 221 introduces multivariable calculus, vectors, and partial derivatives.",
        "CHEM 203 requires prior chemistry or approved equivalent transfer credit.",
        "The history course discusses primary documents and the development of civic institutions.",
        "At Oxford campus, MATH_OX 221 has the same distinct course identifier in the catalog.",
    ]
    query = embedder.embed_query(texts[0])
    documents = embedder.embed_documents(texts[1:])
    vectors = np.vstack([query, documents]).astype("<f4")
    fingerprint = hashlib.sha256(vectors.tobytes()).hexdigest()
    if fingerprint != baseline["baseline_vector_sha256_le_f32"]:
        raise SystemExit("candidate vectors differ from the recorded 3.3.1 baseline fingerprint")

    token_counts = [embedder.count_tokens(text) for text in texts]
    exact = "course " * 509
    over = "course " * 510
    boundary_counts = [embedder.count_tokens(exact), embedder.count_tokens(over)]
    checks = {}
    for label, fn in (("query", lambda: embedder.embed_query(exact)),
                      ("documents", lambda: embedder.embed_documents([exact]))):
        fn()
        checks[f"{label}_512_accepted"] = True
    for label, fn in (("query", lambda: embedder.embed_query(over)),
                      ("documents", lambda: embedder.embed_documents([over]))):
        try:
            fn()
        except ValueError:
            checks[f"{label}_513_rejected"] = True
        else:
            checks[f"{label}_513_rejected"] = False
    ranking = np.argsort(-(documents @ query)).tolist()
    ranking_equal = ranking == baseline["baseline_similarity_ranking"]
    norms = np.linalg.norm(vectors, axis=1)
    result = {
        **actual,
        "dimension": int(vectors.shape[1]),
        "baseline_vector_sha256_le_f32": fingerprint,
        "max_absolute_delta": 0.0,
        "ranking": ranking,
        "ranking_equal": ranking_equal,
        "token_counts": token_counts,
        "boundary_token_counts": boundary_counts,
        "boundary_checks": checks,
        "max_l2_norm_deviation": float(np.max(np.abs(norms - 1.0))),
    }
    if token_counts != evidence["results"]["observed_token_counts"]:
        raise SystemExit("token counts differ from the recorded baseline")
    if boundary_counts != evidence["results"]["boundary_token_counts"]:
        raise SystemExit("boundary token counts differ from the recorded baseline")
    if not ranking_equal:
        raise SystemExit("similarity ranking differs from the recorded baseline")
    if not all(checks.values()):
        raise SystemExit("token ceiling behavior changed")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
