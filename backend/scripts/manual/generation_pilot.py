"""Manual offline generation pilot on three frozen calibration cases.

This runs real retrieval and the configured local greedy model. It is not a
quality benchmark; each candidate requires human semantic and citation review.
Run from backend after the Granite and MiniCPM snapshots are already cached.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.util.digest import file_sha256, json_digest

sha256 = file_sha256
canonical_sha256 = json_digest


REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "backend/tests/fixtures/retrieval_eval_2026_10_08"
CASES_PATH = REPO_ROOT / "backend/tests/fixtures/retrieval_calibration_2026_10_08/cases.jsonl"
CONFIG_PATH = REPO_ROOT / "backend/configs/retrieval.toml"
RUNS_ROOT = (REPO_ROOT / "data/manual-eval-runs").resolve()
DEFAULT_OUTPUT = REPO_ROOT / "docs/evidence/local-generation-pilot-stop-tokens-2026-10-08.json"
CASE_IDS = ("cal-pos-02", "cal-neg-01", "cal-part-01")
MAX_OUTPUT_TOKENS = 256
PER_CALL_TIMEOUT_SECONDS = 180.0
PER_CASE_BUDGET_SECONDS = 240.0
MAX_WORKER_SECONDS = 780.0
WORKER_REAP_SECONDS = 5.0
MIN_FREE_RAM_GIB = 9.0
_WORKER_ARG = "--offline-worker"
_DATABASE_URL_ENV = "GENERATION_PILOT_DATABASE_URL"
_EXPECTED_BEHAVIOR = {
    "cal-pos-02": "Draft expectation: answer from the supported major/minor excerpt with citations.",
    "cal-neg-01": "Draft expectation: refuse to state live CHEM 203 seat counts absent from this corpus.",
    "cal-part-01": (
        "Draft expectation: state the catalog credit minimum, but explain that personal credits "
        "completed or missing are unavailable from this corpus."
    ),
}


def _safe_error_text(value: object, database_url: str | None = None) -> str:
    rendered = str(value)
    if database_url:
        rendered = rendered.replace(database_url, "[redacted local database URL]")
    return rendered[:500]


def snapshot_info(model_id: str) -> dict:
    """Resolve a complete cached snapshot using local cache metadata only."""
    from huggingface_hub.constants import HF_HUB_CACHE

    owner, repo = model_id.split("/", 1)
    cache_repo = Path(HF_HUB_CACHE) / f"models--{owner}--{repo}"
    ref_file = cache_repo / "refs" / "main"
    if not ref_file.is_file():
        raise RuntimeError(f"configured model has no cached main ref: {model_id}")
    revision = ref_file.read_text(encoding="utf-8").strip()
    snapshot = (cache_repo / "snapshots" / revision).resolve(strict=True)
    required = ("config.json", "tokenizer.json", "tokenizer_config.json",
                "configuration_minicpm.py", "modeling_minicpm.py")
    missing = [name for name in required if not (snapshot / name).is_file()]
    weight_files = [p for p in snapshot.glob("*.safetensors") if p.is_file()]
    weight_files += [p for p in snapshot.glob("pytorch_model*.bin") if p.is_file()]
    if missing or not weight_files:
        raise RuntimeError(f"cached model snapshot is incomplete; missing {missing or 'weights'}")
    files = sorted(p for p in snapshot.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        rel = path.relative_to(snapshot).as_posix()
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(sha256(path)))
        total_bytes += path.stat().st_size
    return {"id": model_id, "revision": revision, "snapshot_sha256": digest.hexdigest(),
            "snapshot_bytes": total_bytes, "snapshot_files": len(files),
            "weight_bytes": sum(path.stat().st_size for path in weight_files)}


def provider_code_hashes() -> dict[str, str]:
    paths = (
        REPO_ROOT / "backend/app/config.py",
        REPO_ROOT / "backend/app/llm/hf_local.py",
        REPO_ROOT / "backend/app/llm/factory.py",
        REPO_ROOT / "backend/app/llm/base.py",
    )
    return {path.relative_to(REPO_ROOT).as_posix(): sha256(path) for path in paths}


def _write_output(path: Path, payload: dict) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(rendered, encoding="utf-8")
    temp.replace(path)


def _validated_run_dir(path: Path) -> Path:
    root = RUNS_ROOT.resolve(strict=True)
    repository = REPO_ROOT.resolve(strict=True)
    if not root.is_relative_to(repository) or root == repository:
        raise RuntimeError("pilot run root escaped the workspace")
    run_dir = path.resolve(strict=True)
    if (not run_dir.is_relative_to(root) or run_dir.parent != root
            or not run_dir.name.startswith("generation-pilot-stop-tokens-")):
        raise RuntimeError("refusing to use a pilot directory outside its guarded workspace root")
    return run_dir


def _disable_python_network() -> None:
    """Block Python outbound sockets; psycopg/libpq connects through its native client."""

    def denied(*_args, **_kwargs):
        raise RuntimeError("network access is disabled in this offline generation pilot")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied


def _worker_process(command, *, cwd: Path, env: dict[str, str], timeout: float,
                    popen=subprocess.Popen) -> dict:
    """Run a worker under a hard parent wall-clock bound, without exposing its DSN in logs."""
    started_utc = datetime.now(UTC).isoformat()
    started = time.monotonic()
    process = popen(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    timed_out = False
    reap_failed = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=WORKER_REAP_SECONDS)
        except subprocess.TimeoutExpired:
            reap_failed = True
    return {
        "started_at_utc": started_utc,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "maximum_seconds": timeout,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "child_may_be_alive": reap_failed,
        "reap_timeout_seconds": WORKER_REAP_SECONDS if timed_out else None,
    }


def _mark_parent_timeout(payload: dict, *, elapsed: float) -> dict:
    """Keep finished attempts intact and make the interrupted attempt explicitly unknown."""
    current_case = payload.get("current_case")
    for case in payload.get("cases", []):
        generation = case.get("generation", {})
        if generation.get("status") == "generating":
            generation.update({
                "status": "parent_timeout",
                "candidate_text": None,
                "candidate_disposition": "unknown_no_candidate_returned",
                "structural_validation": "not_completed",
                "error_type": "ParentTimeout",
                "error": "The supervised model worker exceeded its wall-clock limit.",
                "usage": {"prompt_tokens": None, "completion_tokens": None,
                          "total_tokens": None, "source": "unknown"},
            })
            case["parent_process_elapsed_seconds"] = elapsed
            case["completed_at_utc"] = datetime.now(UTC).isoformat()
    if isinstance(current_case, dict) and current_case.get("stage") != "generating":
        active = next((case for case in payload.get("cases", [])
                       if case.get("id") == current_case.get("id")), None)
        if active is None:
            active = {
                "id": current_case["id"],
                "label_review_status": current_case.get("label_review_status", "pending_human_review"),
                "query": current_case.get("query"),
                "draft_expected_behavior": current_case.get("draft_expected_behavior"),
                "started_at_utc": current_case.get("started_at_utc"),
                "retrieval": {"status": "result_not_checkpointed_before_timeout"},
                "generation": {},
            }
            payload.setdefault("cases", []).append(active)
        active["generation"].update({
            "status": "parent_timeout_before_generation", "candidate_text": None,
            "candidate_disposition": "no_model_call_started",
            "structural_validation": "not_run",
            "usage": {"prompt_tokens": None, "completion_tokens": None,
                      "total_tokens": None, "source": "unknown"},
            "semantic_adjudication": "pending_human_review",
        })
        active["parent_process_elapsed_seconds"] = elapsed
    payload["status"] = "process_timeout"
    payload["completed_at_utc"] = datetime.now(UTC).isoformat()
    return payload


def _mark_worker_exit_failure(payload: dict) -> dict:
    """Close any active checkpoint as unknown when a worker exited without a result."""
    for case in payload.get("cases", []):
        generation = case.get("generation", {})
        if generation.get("status") == "generating":
            generation.update({
                "status": "worker_error", "candidate_text": None,
                "candidate_disposition": "unknown_no_candidate_returned",
                "structural_validation": "not_completed",
                "error_type": "WorkerExited",
                "error": "The model worker exited before recording a terminal case result.",
                "usage": {"prompt_tokens": None, "completion_tokens": None,
                          "total_tokens": None, "source": "unknown"},
            })
            case["completed_at_utc"] = datetime.now(UTC).isoformat()
    return payload


class _CapturingProvider:
    """Capture provider-neutral result metadata while preserving answer validation."""

    def __init__(self, provider):
        self.provider = provider
        self.last_result = None

    def generate(self, request):
        result = self.provider.generate(request)
        self.last_result = result
        return result


def _case_record(case, retrieval, *, started_utc: str, source_manifest: dict) -> dict:
    from app.generation.answer import SYSTEM_PROMPT, _context_block
    from app.retrieval.types import Retrieved

    chunks = retrieval.chunks if isinstance(retrieval, Retrieved) else []
    user_prompt = f"{_context_block(chunks)}\n\nQuestion: {case['query']}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    source_by_file = {item["file"]: item for item in source_manifest.get("sources", [])}
    return {
        "id": case["id"], "partition": case["partition"],
        "answerability_label": case["answerability"],
        "label_review_status": case["review_status"], "query": case["query"],
        "label_reason": case["label_reason"],
        "support": case["support"], "related_sections": case["related_sections"],
        "draft_expected_behavior": _EXPECTED_BEHAVIOR[case["id"]],
        "started_at_utc": started_utc,
        "retrieval": {
            "status": "retrieved" if isinstance(retrieval, Retrieved) else "refusal",
            "refused": not isinstance(retrieval, Retrieved),
            "trace_id": str(retrieval.trace.trace_id),
            "config_version": retrieval.trace.config_version,
            "chunks": [{
                "rank": chunk.rank, "chunk_id": str(chunk.chunk_id),
                "source_id": str(chunk.source_id), "chunk_index": chunk.chunk_index,
                "filename": chunk.source_name, "locator_label": chunk.locator_label,
                "source_provenance": source_by_file.get(chunk.source_name),
                "score": chunk.normalized_score, "seams": list(chunk.seams),
                "text": chunk.text,
            } for chunk in chunks],
        },
        "prompt": {
            "messages": messages,
            "sha256": canonical_sha256(messages),
            "serialized_chars": sum(len(message["content"]) for message in messages),
            "hash_scope": "canonical JSON array of exact system/user messages before provider templating",
        },
        "generation": {
            "status": "not_started", "candidate_text": None,
            "candidate_disposition": "unverified_not_released",
            "structural_validation": "not_run", "structural_rejection_reason": None,
            "finish_reason": None, "provider_refusal": None, "model": None,
            "usage": {"prompt_tokens": None, "completion_tokens": None,
                      "total_tokens": None, "source": "unknown"},
            "elapsed_seconds": None, "error_type": None, "error": None,
            "semantic_adjudication": "pending_human_review",
        },
    }


def _run_worker(output: Path, run_path: Path, database_url: str) -> dict:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    _disable_python_network()

    resolved_run_dir = _validated_run_dir(run_path)

    import psutil

    from app.config import load_settings
    from app.db import queries
    from app.db.connection import open_connection
    from app.embeddings import get_embedder
    from app.generation.answer import GenerationRejected, answer_from_context
    from app.generation.operation import GenerationOperation
    from app.ingest.pipeline import ingest_file
    from app.llm import get_provider
    from app.operations.budget import OperationBudgetError
    from app.retrieval.config import load_retrieval_config
    from app.retrieval.retrieve import retrieve
    from app.retrieval.types import Retrieved

    records = {item["id"]: item for item in
               (json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines())}
    if any(case_id not in records for case_id in CASE_IDS):
        raise ValueError("one or more frozen pilot case IDs are missing")
    source_paths = sorted(p for p in CORPUS.glob("*.md") if p.name != "README.md")
    source_hashes = {path.name: sha256(path) for path in source_paths}
    source_manifest_hash = canonical_sha256(source_hashes)
    source_manifest_path = CORPUS / "sources.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))

    settings = load_settings()
    if settings.llm_provider != "hf-local":
        raise RuntimeError("pilot requires configured hf-local provider")
    model = snapshot_info(settings.hf_model_id)
    memory_before = psutil.virtual_memory()
    free_gib = memory_before.available / 1024**3
    model["preflight_free_ram_gib"] = round(free_gib, 2)
    model["preflight_min_free_ram_gib"] = MIN_FREE_RAM_GIB
    if free_gib < MIN_FREE_RAM_GIB:
        raise RuntimeError(
            f"insufficient memory headroom ({free_gib:.2f} GiB available; "
            f"requires at least {MIN_FREE_RAM_GIB:.1f} GiB); model was not loaded"
        )

    payload = {
        "status": "worker_running",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_scope": "draft answerability check on dated public Emory summary fixtures only",
        "script_sha256": sha256(Path(__file__).resolve()),
        "cases_sha256": sha256(CASES_PATH),
        "corpus_source_hashes": source_hashes,
        "corpus_manifest_sha256": source_manifest_hash,
        "source_manifest": {
            "path": str(source_manifest_path.relative_to(REPO_ROOT)),
            "sha256": sha256(source_manifest_path),
            "status": source_manifest.get("status"),
            "usage": source_manifest.get("usage"),
            "sources": source_manifest.get("sources", []),
        },
        "retrieval_config_sha256": sha256(CONFIG_PATH),
        "retrieval_config_version": None,
        "prompt_version": "3",
        "system_prompt_sha256": hashlib.sha256(
            __import__("app.generation.answer", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "model": model,
        "provider_code_sha256": provider_code_hashes(),
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "embedding_provider": settings.embedding_provider,
            "embedding_model_id": settings.embedding_model_id,
            "embedding_device": "cpu", "llm_provider": settings.llm_provider,
            "hf_model_id": settings.hf_model_id, "hf_max_context": settings.hf_max_context,
            "hf_stop_token_strings": list(settings.hf_stop_token_strings or ()),
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "generation_sampling": "greedy", "per_call_timeout_seconds": PER_CALL_TIMEOUT_SECONDS,
            "per_case_budget_seconds": PER_CASE_BUDGET_SECONDS,
            "hf_hub_offline": os.environ["HF_HUB_OFFLINE"],
            "transformers_offline": os.environ["TRANSFORMERS_OFFLINE"],
            "available_ram_gib_before_model_load": round(free_gib, 2),
            "available_ram_gib_after_retrieval": None,
        },
        "case_order": list(CASE_IDS), "cases": [],
        "interpretation": [
            "Generated text is an unverified model candidate; it is not approved advice.",
            "Structural validation only checks provider status, completeness, and citation syntax/presence.",
            "Semantic answer quality and whether citations support each claim require human adjudication.",
            "Answerability labels and draft expected behaviors are pending human review; no correctness claim or quality gate is established.",
            "Corpus inputs are manually written public-source summaries, not authoritative page captures; verify current primary sources before advising.",
        ],
    }
    result_by_id: dict[str, dict] = {}
    _write_output(output, payload)

    data_dir = resolved_run_dir / "worker-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(data_dir)
    isolated = load_settings()
    from dataclasses import replace
    isolated = replace(isolated, data_dir=data_dir, database_url=database_url,
                       embedding_device="cpu")
    operation_settings = replace(
        isolated, operation_timeout_seconds=PER_CASE_BUDGET_SECONDS,
        operation_max_calls=1, llm_timeout_seconds=PER_CALL_TIMEOUT_SECONDS,
        llm_max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    embedder = get_embedder(isolated)
    config = load_retrieval_config(settings=isolated)
    payload["retrieval_config_version"] = config.version
    payload["retrieval_config_sha256"] = sha256(CONFIG_PATH)
    provider = None

    with open_connection(isolated, database_url=database_url) as conn:
            collection_name = "emory_generation_pilot_2026_10_08"
            conn.execute(queries.get("insert_collection"), {
                "collection_id": uuid4(), "name": collection_name,
            })
            conn.commit()
            collection_id = conn.execute(queries.get("get_collection_by_name"), {
                "name": collection_name,
            }).fetchone()["collection_id"]
            for source in source_paths:
                ingest_file(conn, collection_id=collection_id, path=source,
                            content_type="text/markdown", count_tokens=embedder.count_tokens,
                            embedder=embedder, embed_model=isolated.embedding_model_id, config=config)
                conn.commit()

            for case_id in CASE_IDS:
                case = records[case_id]
                case_start = time.monotonic()
                started_utc = datetime.now(UTC).isoformat()
                payload["current_case"] = {
                    "id": case_id, "stage": "retrieving", "query": case["query"],
                    "label_review_status": case["review_status"],
                    "draft_expected_behavior": _EXPECTED_BEHAVIOR[case_id],
                    "started_at_utc": started_utc,
                }
                _write_output(output, payload)
                operation_id = uuid4()
                operation = GenerationOperation(
                    conn, operation_settings, operation_id,
                    f"manual-local-generation-pilot:{case_id}",
                )
                retrieval = retrieve(conn, collection_id=collection_id, query=case["query"],
                                     embedder=embedder,
                                     embed_model=isolated.embedding_model_id, config=config)
                item = _case_record(case, retrieval, started_utc=started_utc,
                                    source_manifest=source_manifest)
                item["generation"]["operation_id"] = str(operation_id)
                item["generation"]["operation_limits"] = {
                    "active_budget_seconds": PER_CASE_BUDGET_SECONDS,
                    "max_calls": 1, "per_call_timeout_seconds": PER_CALL_TIMEOUT_SECONDS,
                    "per_call_output_cap": MAX_OUTPUT_TOKENS,
                }
                result_by_id[case_id] = item
                payload["cases"] = [result_by_id[key] for key in CASE_IDS if key in result_by_id]
                payload["current_case"]["stage"] = "preparing_generation"
                _write_output(output, payload)

                if not isinstance(retrieval, Retrieved):
                    item["generation"]["status"] = "not_run_retrieval_refusal"
                    item["generation"]["structural_validation"] = "not_applicable"
                    item["completed_at_utc"] = datetime.now(UTC).isoformat()
                    item["case_elapsed_seconds"] = round(time.monotonic() - case_start, 3)
                    payload["current_case"] = None
                    _write_output(output, payload)
                    continue

                gc.collect()
                available_after_retrieval = psutil.virtual_memory().available / 1024**3
                item["memory_available_gib_after_retrieval"] = round(available_after_retrieval, 2)
                if provider is None and available_after_retrieval < MIN_FREE_RAM_GIB:
                    item["generation"].update({
                        "status": "skipped_insufficient_memory",
                        "structural_validation": "not_run_model_not_loaded",
                        "error": (f"{available_after_retrieval:.2f} GiB available after retrieval; "
                                  f"requires at least {MIN_FREE_RAM_GIB:.1f} GiB"),
                    })
                    _write_output(output, payload)
                    raise RuntimeError("insufficient headroom to load generation model")

                if provider is None:
                    provider = get_provider(isolated, provider="hf-local")
                remaining = PER_CASE_BUDGET_SECONDS - (time.monotonic() - case_start)
                if remaining <= 1:
                    item["generation"].update({"status": "case_budget_exhausted_before_model_call",
                                               "structural_validation": "not_run"})
                    item["completed_at_utc"] = datetime.now(UTC).isoformat()
                    item["case_elapsed_seconds"] = round(time.monotonic() - case_start, 3)
                    payload["current_case"] = None
                    _write_output(output, payload)
                    continue
                item["generation"]["status"] = "generating"
                payload["current_case"]["stage"] = "generating"
                _write_output(output, payload)
                call_start = time.monotonic()
                capture = _CapturingProvider(provider)
                rejection = None
                failure = None
                answer = None

                def prepare_request(request, op=operation, result_item=item):
                    from app.operations.budget import estimate_prompt_reservation

                    prompt_estimate = estimate_prompt_reservation(request)
                    prepared = op.prepare(request)
                    result_item["generation"]["prepared_request"] = {
                        "timeout_seconds": prepared.timeout_seconds,
                        "max_output_tokens": prepared.max_output_tokens,
                    }
                    result_item["generation"]["usage_estimate"] = {
                        "prompt_reserved_tokens": prompt_estimate,
                        "source": "serialized-byte reservation estimate",
                        "measured_usage_recorded_separately": True,
                    }
                    _write_output(output, payload)
                    return prepared

                try:
                    answer = answer_from_context(
                        retrieval.chunks, case["query"], provider=capture,
                        max_new_tokens=MAX_OUTPUT_TOKENS,
                        timeout_seconds=PER_CALL_TIMEOUT_SECONDS,
                        sampling_policy="greedy", prepare_request=prepare_request,
                        on_attempt=operation.account,
                    )
                except GenerationRejected as exc:
                    rejection = exc
                except Exception as exc:  # noqa: BLE001 - persist every provider failure per case
                    failure = exc
                item["generation"]["elapsed_seconds"] = round(time.monotonic() - call_start, 3)
                fence_error = None
                try:
                    operation.assert_publishable()
                except OperationBudgetError as exc:
                    fence_error = exc

                captured = capture.last_result
                candidate_text = (captured.text if captured is not None
                                  else rejection.answer.text if rejection is not None else None)
                if fence_error is not None or time.monotonic() - case_start > PER_CASE_BUDGET_SECONDS:
                    item["generation"].update({
                        "status": "late_candidate_withheld" if candidate_text is not None
                        else "operation_budget_exhausted",
                        "candidate_text": candidate_text,
                        "structural_validation": (
                            "rejected" if rejection is not None else
                            "passed_but_withheld_late" if answer is not None else
                            "not_completed_before_budget_fence"),
                        "structural_rejection_reason": str(rejection) if rejection else None,
                        "budget_error": str(fence_error) if fence_error else "case deadline exceeded",
                        "error_type": type(failure).__name__ if failure else None,
                        "error": _safe_error_text(failure, database_url) if failure else None,
                    })
                elif rejection is not None:
                    item["generation"].update({
                        "status": "candidate_withheld_structural_rejection",
                        "candidate_text": rejection.answer.text,
                        "structural_validation": "rejected",
                        "structural_rejection_reason": str(rejection),
                    })
                elif failure is not None:
                    item["generation"].update({
                        "status": "generation_error", "error_type": type(failure).__name__,
                        "error": _safe_error_text(failure, database_url),
                        "structural_validation": "not_completed",
                    })
                else:
                    item["generation"].update({
                        "status": "structurally_valid_unverified_candidate",
                        "candidate_text": answer.text,
                        "structural_validation": "passed",
                        "citations": [{"filename": c.source_name, "locator_label": c.locator_label,
                                       "chunk_id": str(c.chunk_id)} for c in answer.citations],
                    })
                if capture.last_result is not None:
                    result = capture.last_result
                    item["generation"].update({
                        "finish_reason": result.finish_reason,
                        "provider_refusal": result.refusal,
                        "model": result.model,
                        "usage": {"prompt_tokens": result.usage.prompt_tokens,
                                  "completion_tokens": result.usage.completion_tokens,
                                  "total_tokens": result.usage.total_tokens,
                                  "source": result.usage.source},
                    })
                elif failure is not None:
                    error_usage = getattr(failure, "usage", None)
                    if error_usage is not None:
                        item["generation"]["usage"] = {
                            "prompt_tokens": error_usage.prompt_tokens,
                            "completion_tokens": error_usage.completion_tokens,
                            "total_tokens": error_usage.total_tokens,
                            "source": error_usage.source,
                        }
                item["completed_at_utc"] = datetime.now(UTC).isoformat()
                item["case_elapsed_seconds"] = round(time.monotonic() - case_start, 3)
                payload["current_case"] = None
                _write_output(output, payload)
    failure_statuses = {
        "generation_error", "skipped_insufficient_memory", "case_budget_exhausted_before_model_call",
        "operation_budget_exhausted", "late_candidate_withheld",
    }
    payload["status"] = (
        "completed_with_generation_errors"
        if any(case.get("generation", {}).get("status") in failure_statuses
               for case in payload["cases"])
        else "completed_manual_local_pilot"
    )
    payload["completed_at_utc"] = datetime.now(UTC).isoformat()
    _write_output(output, payload)
    return payload


def run_parent(output: Path = DEFAULT_OUTPUT, *, server_factory=None, connect_factory=None,
               migration_runner=None, worker_runner=None) -> dict:
    """Own Postgres and supervise the offline model process under a hard deadline."""
    import pgserver

    from app.db.connection import connect
    from app.db.migrate import apply_migrations

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUNS_ROOT.resolve(strict=True)
    repository = REPO_ROOT.resolve(strict=True)
    if not root.is_relative_to(repository) or root == repository:
        raise RuntimeError("pilot run root escaped the workspace")
    run_dir = root / f"generation-pilot-stop-tokens-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(exist_ok=False)
    run_dir = _validated_run_dir(run_dir)
    pgdata = run_dir / "pgdata"
    checkpoint = run_dir / "worker-checkpoint.json"
    server_factory = server_factory or (
        lambda path: pgserver.get_server(path, cleanup_mode="stop")
    )
    connect_factory = connect_factory or connect
    migration_runner = migration_runner or apply_migrations
    worker_runner = worker_runner or _worker_process
    server = None
    worker = None
    database_url = None
    server_stopped = False
    setup_started = time.monotonic()
    report = {
        "status": "parent_starting",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_directory": str(run_dir.relative_to(REPO_ROOT)),
        "worker_wall_clock_limit_seconds": MAX_WORKER_SECONDS,
        "worker_scope": "includes child corpus/model hashing, model load, retrieval, and generation; excludes parent PostgreSQL startup/migrations",
        "worker_network_policy": "HF offline; Python socket connection APIs blocked; database access uses the parent's local psycopg/libpq DSN",
        "cases": [],
    }
    try:
        frozen_cases = {
            case["id"]: case for case in
            (json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines())
        }
        report["cases"] = [{
            "id": case_id,
            "partition": frozen_cases[case_id]["partition"],
            "answerability_label": frozen_cases[case_id]["answerability"],
            "label_review_status": frozen_cases[case_id]["review_status"],
            "query": frozen_cases[case_id]["query"],
            "draft_expected_behavior": _EXPECTED_BEHAVIOR[case_id],
            "generation": {
                "status": "not_started", "candidate_text": None,
                "candidate_disposition": "unverified_not_released",
                "structural_validation": "not_run",
                "usage": {"prompt_tokens": None, "completion_tokens": None,
                          "total_tokens": None, "source": "unknown"},
                "semantic_adjudication": "pending_human_review",
            },
        } for case_id in CASE_IDS]
        _write_output(checkpoint, report)
    except Exception as exc:  # noqa: BLE001 - seed only bounded checkpoint metadata
        report["checkpoint_seed_error"] = type(exc).__name__
    parent_metadata = dict(report)
    try:
        server = server_factory(pgdata)
        database_url = server.get_uri()
        conn = connect_factory(database_url)
        try:
            migration_runner(conn)
            conn.commit()
        finally:
            conn.close()
        report["postgres_setup_elapsed_seconds"] = round(time.monotonic() - setup_started, 3)
        parent_metadata["postgres_setup_elapsed_seconds"] = report["postgres_setup_elapsed_seconds"]
        env = os.environ.copy()
        env.update({
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1", _DATABASE_URL_ENV: database_url,
        })
        command = [sys.executable, "-m", "scripts.manual.generation_pilot",
                   _WORKER_ARG, str(run_dir), str(checkpoint)]
        worker = worker_runner(
            command, cwd=REPO_ROOT / "backend", env=env, timeout=MAX_WORKER_SECONDS,
        )
        if checkpoint.is_file():
            report = {
                **parent_metadata,
                **json.loads(checkpoint.read_text(encoding="utf-8")),
            }
            report["run_directory"] = str(run_dir.relative_to(REPO_ROOT))
        report["worker_process"] = worker
        if worker.get("child_may_be_alive"):
            if worker["timed_out"]:
                report = _mark_parent_timeout(report, elapsed=worker["elapsed_seconds"])
            else:
                report = _mark_worker_exit_failure(report)
            report.update({
                "status": "worker_reap_error",
                "worker_error": "Worker did not exit within the bounded reap grace after termination.",
                "completed_at_utc": datetime.now(UTC).isoformat(),
            })
        elif worker["timed_out"]:
            report = _mark_parent_timeout(report, elapsed=worker["elapsed_seconds"])
        elif (worker["exit_code"] != 0
              and report.get("status") != "completed_with_generation_errors"):
            report = _mark_worker_exit_failure(report)
            report.update({
                "status": "worker_error",
                "worker_error": "Worker exited before completing; details are unavailable because child logs are suppressed.",
                "completed_at_utc": datetime.now(UTC).isoformat(),
            })
        elif worker["exit_code"] != 0:
            report = _mark_worker_exit_failure(report)
        elif (worker["exit_code"] == 0
              and report.get("status") != "completed_manual_local_pilot"):
            report.update({
                "status": "worker_error",
                "worker_error": "Worker exited without writing a terminal checkpoint.",
                "completed_at_utc": datetime.now(UTC).isoformat(),
            })
        report.setdefault("status", "worker_error")
    except Exception as exc:  # noqa: BLE001 - keep a bounded manual run report on setup errors
        report.update({
            "status": "parent_setup_error",
            "error_type": type(exc).__name__,
            "error": _safe_error_text(exc, database_url),
            "completed_at_utc": datetime.now(UTC).isoformat(),
        })
    finally:
        if server is not None:
            try:
                server.cleanup()
                server_stopped = True
            except Exception as exc:  # noqa: BLE001 - retain data if stop is uncertain
                report["server_cleanup_error"] = type(exc).__name__
                report["server_cleanup_error_detail"] = _safe_error_text(exc, database_url)[:300]
                report["status"] = "server_cleanup_error"
        child_may_be_alive = bool(worker and worker.get("child_may_be_alive"))
        if (server is None or server_stopped) and not child_may_be_alive:
            cleanup_target = run_dir.resolve(strict=True)
            if (not cleanup_target.is_relative_to(root) or cleanup_target.parent != root
                    or not cleanup_target.name.startswith("generation-pilot-stop-tokens-")):
                report["cleanup_error"] = "refusing to clean pilot data outside guarded workspace root"
            else:
                try:
                    shutil.rmtree(cleanup_target)
                    report["run_directory_cleaned"] = True
                except Exception as exc:  # noqa: BLE001 - persist cleanup failure evidence
                    report["status"] = "scratch_cleanup_error"
                    report["cleanup_error"] = type(exc).__name__
        else:
            report["run_directory_retained_for_manual_cleanup"] = str(
                run_dir.relative_to(REPO_ROOT)
            )
        report["completed_at_utc"] = datetime.now(UTC).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_output(output, report)
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(_WORKER_ARG, nargs=2, metavar=("RUN_DIR", "CHECKPOINT"))
    args = parser.parse_args()
    if args.offline_worker:
        database_url = os.environ.pop(_DATABASE_URL_ENV, "")
        if not database_url:
            raise SystemExit("offline worker requires the parent's private database connection")
        payload = _run_worker(
            Path(args.offline_worker[1]), Path(args.offline_worker[0]), database_url
        )
        raise SystemExit(0 if payload["status"] == "completed_manual_local_pilot" else 2)
    payload = run_parent(args.output)
    print(json.dumps({"status": payload["status"], "output": str(args.output),
                      "cases": [{"id": c["id"], "generation": c["generation"]["status"],
                                "case_elapsed_seconds": c.get("case_elapsed_seconds")}
                                for c in payload.get("cases", [])]}, indent=2))
    if payload["status"] != "completed_manual_local_pilot":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
