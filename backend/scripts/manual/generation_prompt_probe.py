"""Compare a citation-format prompt variant on frozen public pilot contexts.

This manual probe reuses the exact user messages and chunks from the completed
three-case pilot. It does not retrieve, read a database, or change production
prompt policy. Its output remains unverified and requires human review.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.util.digest import file_sha256, json_digest

sha256_file = file_sha256
canonical_sha256 = json_digest


REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "backend/tests/fixtures/retrieval_eval_2026_10_08"
CASES_PATH = REPO_ROOT / "backend/tests/fixtures/retrieval_calibration_2026_10_08/cases.jsonl"
CONFIG_PATH = REPO_ROOT / "backend/configs/retrieval.toml"
PILOT_PATH = REPO_ROOT / "docs/evidence/local-generation-pilot-stop-tokens-2026-10-08.json"
EVIDENCE_PATH = REPO_ROOT / "docs/evidence/generation-prompt-probe-citations-2026-10-08.json"
MAX_WORKER_SECONDS = 780.0
MAX_OUTPUT_TOKENS = 256
PER_CALL_TIMEOUT_SECONDS = 180.0
MIN_FREE_RAM_GIB = 9.0
CASE_IDS = ("cal-pos-02", "cal-neg-01", "cal-part-01")
PROBE_PROMPT_VERSION = "citation-format-probe-1"
PROBE_PROMPT_SUFFIX = (
    "\n\nCitation-format probe instructions: For an answerable question, give one concise, "
    "direct answer. Put the exact supporting context marker after every factual sentence, "
    "using the existing numbered form such as [1]. Example syntax only: `Supported statement "
    "[1].` Do not add facts beyond the question or cite a chunk that does not support the claim. "
    "For a partially answerable question, state the supported part with its citation and plainly "
    "say which requested information the supplied context does not provide. For an unanswerable "
    "question, say only that the supplied context does not provide the requested information; "
    "do not guess, give outside advice, or cite a merely related chunk."
)
_WORKER_ARG = "--offline-worker"


def probe_system_prompt(production_prompt: str) -> str:
    """Return an experiment-only system prompt without altering production text."""
    return production_prompt + PROBE_PROMPT_SUFFIX


def prepare_probe_request(request, captured: dict):
    """Replace only the system message and capture the exact resulting request."""
    messages = [dict(message) if isinstance(message, dict) else message
                for message in request.messages]
    if len(messages) != 2 or messages[0].get("role") != "system" or messages[1].get("role") != "user":
        raise ValueError("probe expected the standard system/user answer request")
    messages[0]["content"] = probe_system_prompt(messages[0]["content"])
    captured["messages"] = messages
    captured["messages_sha256"] = canonical_sha256(messages)
    captured["max_output_tokens"] = request.max_output_tokens
    captured["timeout_seconds"] = request.timeout_seconds
    captured["temperature"] = request.temperature
    return replace(request, messages=tuple(messages))


def build_frozen_chunks(case_record: dict):
    """Recreate CitedChunks from the frozen retrieval record without re-retrieval."""
    from app.retrieval.types import CitedChunk

    chunks = []
    for item in case_record["retrieval"]["chunks"]:
        chunks.append(CitedChunk(
            chunk_id=UUID(item["chunk_id"]),
            source_id=UUID(item["source_id"]),
            chunk_index=int(item["chunk_index"]),
            source_name=item["filename"],
            locator_label=item["locator_label"],
            text=item["text"],
            seams=tuple(item["seams"]),
            normalized_score=float(item["score"]),
            source_slot=int(item["rank"]),
            rank=int(item["rank"]),
        ))
    return chunks


def validate_frozen_inputs(pilot: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Require current public fixture hashes and metadata to match the completed pilot."""
    from app.generation.answer import SYSTEM_PROMPT, _context_block
    from app.ingest.normalize import decode_text, normalize_text

    if pilot.get("status") != "completed_manual_local_pilot":
        raise ValueError("source pilot is not a completed run")
    if pilot.get("case_order") != list(CASE_IDS):
        raise ValueError("source pilot case order differs from the frozen prompt probe")
    case_hash = sha256_file(CASES_PATH)
    if case_hash != pilot.get("cases_sha256"):
        raise ValueError("checked-in calibration cases changed since the source pilot")
    source_files = sorted(path for path in CORPUS_DIR.glob("*.md") if path.name != "README.md")
    source_hashes = {path.name: sha256_file(path) for path in source_files}
    if source_hashes != pilot.get("corpus_source_hashes"):
        raise ValueError("checked-in public corpus changed since the source pilot")
    if canonical_sha256(source_hashes) != pilot.get("corpus_manifest_sha256"):
        raise ValueError("public corpus manifest hash does not match the source pilot")
    if sha256_file(CONFIG_PATH) != pilot.get("retrieval_config_sha256"):
        raise ValueError("retrieval config changed since the source pilot")
    if hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest() != pilot.get("system_prompt_sha256"):
        raise ValueError("production system prompt changed since the source pilot")
    source_manifest_path = CORPUS_DIR / "sources.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if sha256_file(source_manifest_path) != pilot["source_manifest"].get("sha256"):
        raise ValueError("source provenance manifest changed since the source pilot")
    source_by_file = {item["file"]: item for item in source_manifest.get("sources", [])}
    normalized_sources = {
        path.name: normalize_text(decode_text(path.read_bytes()))
        for path in source_files
    }
    case_records = {}
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            case_records[record["id"]] = record
    pilot_cases = {item["id"]: item for item in pilot.get("cases", [])}
    for case_id in CASE_IDS:
        expected = case_records.get(case_id)
        actual = pilot_cases.get(case_id)
        if expected is None or actual is None:
            raise ValueError(f"frozen case missing: {case_id}")
        for source_key, pilot_key in (("query", "query"), ("answerability", "answerability_label"),
                                      ("review_status", "label_review_status"),
                                      ("support", "support"), ("related_sections", "related_sections"),
                                      ("label_reason", "label_reason")):
            if expected.get(source_key) != actual.get(pilot_key):
                raise ValueError(f"case metadata changed for {case_id}: {source_key}")
        if actual["generation"].get("status") not in {
            "candidate_withheld_structural_rejection", "candidate_withheld",
            "candidate_accepted_structurally",
        }:
            raise ValueError(f"source pilot case is not complete: {case_id}")
        prompt = actual.get("prompt", {})
        messages = prompt.get("messages", [])
        if len(messages) != 2 or messages[1].get("role") != "user":
            raise ValueError(f"source pilot user message missing for {case_id}")
        if messages[0].get("content") != SYSTEM_PROMPT:
            raise ValueError(f"source pilot system prompt differs for {case_id}")
        if canonical_sha256(messages) != prompt.get("sha256"):
            raise ValueError(f"source pilot prompt hash mismatch for {case_id}")
        for chunk in actual["retrieval"]["chunks"]:
            filename = chunk.get("filename")
            if filename not in source_hashes or filename not in source_by_file:
                raise ValueError(f"chunk has unknown public source provenance: {filename}")
            provenance = chunk.get("source_provenance", {})
            expected_source = source_by_file[filename]
            if (provenance.get("file") != filename
                    or any(expected_source.get(key) != value
                           for key, value in provenance.items())):
                raise ValueError(f"chunk provenance mismatch for {case_id}: {filename}")
            if chunk["text"] not in normalized_sources[filename]:
                raise ValueError(
                    f"frozen chunk text is not present in checked-in source: {case_id}/{filename}"
                )
        reconstructed_user = f"{_context_block(build_frozen_chunks(actual))}\n\nQuestion: {expected['query']}"
        if reconstructed_user != messages[1]["content"]:
            raise ValueError(f"reconstructed frozen context differs for {case_id}")
    return pilot_cases, source_by_file


def _disable_network() -> None:
    def denied(*_args, **_kwargs):
        raise RuntimeError("network is disabled for the prompt-only generation probe")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied


def _atomic_write(path: Path, payload: dict) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _model_snapshot_info(model_id: str, expected_revision: str) -> dict:
    from huggingface_hub.constants import HF_HUB_CACHE

    owner, repo = model_id.split("/", 1)
    model_cache = Path(HF_HUB_CACHE) / f"models--{owner}--{repo}"
    main_ref = model_cache / "refs" / "main"
    if not main_ref.is_file() or main_ref.read_text(encoding="utf-8").strip() != expected_revision:
        raise RuntimeError("cached model main ref differs from the source pilot revision")
    snapshot = model_cache / "snapshots" / expected_revision
    if not snapshot.is_dir():
        raise RuntimeError("the source model snapshot is not present in the local cache")
    files = sorted(path for path in snapshot.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    total_bytes = 0
    weight_bytes = 0
    for path in files:
        relative = path.relative_to(snapshot).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
        size = path.stat().st_size
        total_bytes += size
        if path.name.endswith((".safetensors", ".bin")):
            weight_bytes += size
    return {"id": model_id, "revision": expected_revision,
            "snapshot_sha256": digest.hexdigest(), "snapshot_bytes": total_bytes,
            "snapshot_files": len(files), "weight_bytes": weight_bytes}


def _provider_code_hashes() -> dict[str, str]:
    relative_paths = (
        "backend/app/config.py", "backend/app/llm/hf_local.py",
        "backend/app/llm/factory.py", "backend/app/llm/base.py",
        "backend/app/generation/answer.py",
    )
    return {path: sha256_file(REPO_ROOT / path) for path in relative_paths}


class _CaptureProvider:
    def __init__(self, provider):
        self.provider = provider
        self.result = None

    def generate(self, request):
        self.result = self.provider.generate(request)
        return self.result


def _usage_payload(usage) -> dict:
    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None,
                "total_tokens": None, "source": "unknown"}
    return {"prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens, "source": usage.source}


def _final_worker_status(cases: list[dict]) -> str:
    if any(case.get("status") == "provider_or_runner_error" for case in cases):
        return "completed_with_generation_errors"
    return "completed_prompt_format_probe"


def _mark_active_unknown(report: dict, *, status: str, reason: str) -> None:
    active = report.get("current_case")
    if not isinstance(active, dict):
        report["current_case"] = None
        return
    record = next((item for item in report.get("cases", [])
                   if item.get("id") == active.get("id")), None)
    if record is not None and record.get("status") == "generating":
        record.update({
            "status": status,
            "candidate_disposition": "unknown_no_candidate_returned",
            "provider_result": {"candidate_text_raw": None,
                                "candidate_disposition": "unknown_no_candidate_returned",
                                "finish_reason": None, "model": None,
                                "provider_refusal": None, "tool_call_count": None,
                                "usage": {"prompt_tokens": None, "completion_tokens": None,
                                          "total_tokens": None, "source": "unknown"}},
            "structural_validation": {"status": "not_completed", "reason": reason},
            "error": reason,
            "completed_at_utc": datetime.now(UTC).isoformat(),
        })
    report["current_case"] = None


def _supervise_child(process, output: Path, *, timeout_seconds: float) -> dict:
    """Wait for the one owned worker and normalize timeout/nonzero terminal states."""
    started = time.monotonic()
    timed_out = False
    reap_failed = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            reap_failed = True
        except OSError:
            reap_failed = True
    report = json.loads(output.read_text(encoding="utf-8"))
    report["process"] = {
        "wall_clock_seconds": round(time.monotonic() - started, 3),
        "maximum_seconds": timeout_seconds,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "child_may_be_alive": reap_failed,
    }
    if reap_failed:
        report["status"] = "worker_reap_error"
        _mark_active_unknown(report, status="worker_reap_error",
                             reason="worker exceeded its deadline and could not be reaped")
    elif timed_out:
        report["status"] = "process_timeout"
        _mark_active_unknown(report, status="parent_timeout",
                             reason="worker exceeded its wall-clock limit")
    elif process.returncode != 0:
        if report.get("status") == "worker_running":
            report["status"] = "worker_error"
        _mark_active_unknown(report, status="worker_error",
                             reason="worker exited nonzero before the active case completed")
    elif report.get("status") == "worker_running":
        report["status"] = "worker_exit_without_terminal"
        _mark_active_unknown(report, status="worker_error",
                             reason="worker exited without writing a terminal result")
    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    _atomic_write(output, report)
    return report


def _exit_code(report: dict) -> int:
    status = report.get("status")
    if status == "completed_prompt_format_probe":
        return 0
    if status == "process_timeout":
        return 124
    if status == "worker_reap_error":
        return 125
    return 2


def _run_case(case_id: str, source_case: dict, settings, provider, payload: dict) -> dict:
    from app.generation.answer import GenerationRejected, answer_from_context
    from app.generation.citations import InvalidCitation, citation_numbers

    chunks = build_frozen_chunks(source_case)
    query = source_case["query"]
    captured = {}
    capture_provider = _CaptureProvider(provider)
    started_utc = datetime.now(UTC).isoformat()
    started = time.monotonic()
    attempt = {"result": None, "error": None}

    def on_attempt(result, error):
        attempt["result"] = result
        attempt["error"] = error

    item = {
        "id": case_id,
        "answerability_label": source_case["answerability_label"],
        "label_review_status": source_case["label_review_status"],
        "query": query,
        "source_pilot_prompt_sha256": source_case["prompt"]["sha256"],
        "status": "generating",
        "candidate_disposition": "unverified_not_released",
        "semantic_adjudication": "pending_human_review",
        "durable_operation_recorded": False,
        "request": None,
        "provider_result": None,
        "structural_validation": {"status": "not_run", "reason": None},
        "citation_syntax_audit": None,
        "started_at_utc": started_utc,
    }
    payload["current_case"] = {"id": case_id, "stage": "generating", "started_at_utc": started_utc}
    payload["cases"].append(item)
    _atomic_write(EVIDENCE_PATH, payload)
    try:
        answer = answer_from_context(
            chunks, query, provider=capture_provider,
            max_new_tokens=MAX_OUTPUT_TOKENS,
            timeout_seconds=PER_CALL_TIMEOUT_SECONDS,
            sampling_policy="greedy",
            on_attempt=on_attempt,
            prepare_request=lambda request: prepare_probe_request(request, captured),
        )
        item["status"] = "candidate_returned"
        item["structural_validation"] = {
            "status": "accepted_by_current_structural_validator",
            "reason": None,
            "resolved_citations": [
                {"source_name": citation.source_name, "locator_label": citation.locator_label,
                 "chunk_id": str(citation.chunk_id)} for citation in answer.citations
            ],
        }
    except GenerationRejected as exc:
        item["status"] = "candidate_withheld_structural_rejection"
        item["structural_validation"] = {"status": "rejected", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - bounded manual diagnostic boundary
        item["status"] = "provider_or_runner_error"
        item["structural_validation"] = {
            "status": "not_completed", "reason": type(exc).__name__,
        }
        item["error"] = str(exc)[:500]

    result = attempt["result"] or capture_provider.result
    if result is not None:
        item["provider_result"] = {
            "candidate_text_raw": result.text,
            "candidate_disposition": "unverified_not_released",
            "finish_reason": result.finish_reason,
            "model": result.model,
            "provider_refusal": result.refusal,
            "tool_call_count": len(result.tool_calls),
            "usage": _usage_payload(result.usage),
        }
        try:
            numbers = citation_numbers(result.text, len(chunks))
            item["citation_syntax_audit"] = {
                "status": "valid_markers" if numbers else "no_markers",
                "numbers": list(numbers), "context_chunk_count": len(chunks),
            }
        except InvalidCitation as exc:
            item["citation_syntax_audit"] = {
                "status": "invalid_marker", "reason": str(exc),
                "context_chunk_count": len(chunks),
            }
    else:
        error = attempt["error"]
        usage = getattr(error, "usage", None)
        item["provider_result"] = {
            "candidate_text_raw": None, "candidate_disposition": "no_candidate_returned",
            "finish_reason": None, "model": None,
            "provider_refusal": None, "tool_call_count": None,
            "usage": _usage_payload(usage),
        }
    item["request"] = {
        "messages": captured.get("messages"),
        "messages_sha256": captured.get("messages_sha256"),
        "max_output_tokens": captured.get("max_output_tokens"),
        "timeout_seconds": captured.get("timeout_seconds"),
        "temperature": captured.get("temperature"),
        "sampling_policy": "greedy",
    }
    item["elapsed_seconds"] = round(time.monotonic() - started, 3)
    item["completed_at_utc"] = datetime.now(UTC).isoformat()
    payload["current_case"] = None
    _atomic_write(EVIDENCE_PATH, payload)
    return item


def _worker(output: Path) -> int:
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "HF_DATASETS_OFFLINE": "1"})
    _disable_network()
    started = time.monotonic()
    payload = None
    try:
        pilot = json.loads(PILOT_PATH.read_text(encoding="utf-8"))
        pilot_cases, _source_by_file = validate_frozen_inputs(pilot)
        from app.config import load_settings

        settings = load_settings()
        baseline_provider_hashes = pilot.get("provider_code_sha256", {})
        current_provider_hashes = _provider_code_hashes()
        baseline_matches = {
            path: current_provider_hashes.get(path) == expected
            for path, expected in baseline_provider_hashes.items()
        }
        if not baseline_matches or not all(baseline_matches.values()):
            raise RuntimeError(
                "provider code differs from the source pilot: "
                + ", ".join(path for path, matches in baseline_matches.items() if not matches)
            )
        old_environment = pilot["environment"]
        if (settings.llm_provider != "hf-local" or settings.hf_model_id != pilot["model"]["id"]
                or settings.hf_max_context != old_environment["hf_max_context"]
                or list(settings.hf_stop_token_strings or ()) != old_environment["hf_stop_token_strings"]
                or settings.llm_sampling_policy != "greedy"):
            raise RuntimeError("current model/config/stop/sampling settings differ from the source pilot")

        model = _model_snapshot_info(settings.hf_model_id, pilot["model"]["revision"])
        if model["snapshot_sha256"] != pilot["model"]["snapshot_sha256"]:
            raise RuntimeError("cached model snapshot hash differs from the source pilot")
        import psutil
        import tokenizers
        import torch
        import transformers

        free_ram_gib = psutil.virtual_memory().available / 1024**3
        if free_ram_gib < MIN_FREE_RAM_GIB:
            raise RuntimeError("insufficient memory headroom; model was not loaded")
        from app.generation.answer import SYSTEM_PROMPT
        from app.llm import get_provider

        payload = json.loads(output.read_text(encoding="utf-8"))
        payload.update({
            "status": "worker_running",
            "pilot_source": {"path": str(PILOT_PATH.relative_to(REPO_ROOT)),
                             "sha256": sha256_file(PILOT_PATH),
                             "script_sha256": pilot.get("script_sha256")},
            "cases_fixture": {"path": str(CASES_PATH.relative_to(REPO_ROOT)),
                              "sha256": sha256_file(CASES_PATH)},
            "corpus_source_hashes": pilot["corpus_source_hashes"],
            "corpus_manifest_sha256": pilot["corpus_manifest_sha256"],
            "source_manifest": pilot["source_manifest"],
            "retrieval_config_sha256": pilot["retrieval_config_sha256"],
            "model": model,
            "prompt": {"version": PROBE_PROMPT_VERSION,
                       "production_prompt_version": pilot["prompt_version"],
                       "system_prompt": probe_system_prompt(SYSTEM_PROMPT),
                       "system_prompt_sha256": hashlib.sha256(
                           probe_system_prompt(SYSTEM_PROMPT).encode("utf-8")
                       ).hexdigest()},
            "provider_code_sha256": _provider_code_hashes(),
            "source_pilot_provider_code_sha256": baseline_provider_hashes,
            "environment": {
                "python": platform.python_version(), "platform": platform.platform(),
                "torch": torch.__version__, "transformers": transformers.__version__,
                "tokenizers": tokenizers.__version__,
                "llm_provider": settings.llm_provider, "hf_model_id": settings.hf_model_id,
                "hf_max_context": settings.hf_max_context,
                "hf_stop_token_strings": list(settings.hf_stop_token_strings or ()),
                "previous_pilot_environment": pilot["environment"],
                "max_output_tokens": MAX_OUTPUT_TOKENS, "sampling_policy": "greedy",
                "per_call_timeout_seconds": PER_CALL_TIMEOUT_SECONDS,
                "worker_timeout_seconds": MAX_WORKER_SECONDS,
                "available_ram_gib_before_model_load": round(free_ram_gib, 2),
                "hf_hub_offline": os.environ["HF_HUB_OFFLINE"],
                "transformers_offline": os.environ["TRANSFORMERS_OFFLINE"],
                "python_socket_apis_blocked": True,
            },
            "interpretation": [
                "This is a separate prompt-format experiment; production prompt behavior is unchanged.",
                "Each candidate is raw model output, unverified and not released as advice.",
                "Structural validation and marker syntax are reported separately from semantic support.",
                "Answerability labels and semantic correctness remain pending human review.",
                "No citation is automatically assigned, inserted, or repaired by this diagnostic.",
                "No durable operation or shared budget reservation is recorded by this prompt-only diagnostic.",
                "Sources are manually written public summaries, not authoritative captures; verify current primary sources before advising.",
            ],
        })
        _atomic_write(output, payload)
        provider = get_provider(settings, provider="hf-local", timeout_seconds=PER_CALL_TIMEOUT_SECONDS)
        for case_id in CASE_IDS:
            _run_case(case_id, pilot_cases[case_id], settings, provider, payload)
        payload["status"] = _final_worker_status(payload["cases"])
        payload["current_case"] = None
        payload["worker_elapsed_seconds"] = round(time.monotonic() - started, 3)
        payload["completed_at_utc"] = datetime.now(UTC).isoformat()
        _atomic_write(output, payload)
        return _exit_code(payload)
    except Exception as exc:  # noqa: BLE001 - bounded manual diagnostic boundary
        try:
            if payload is None:
                payload = json.loads(output.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - retain a terminal report even on parse failure
            payload = {"status": "worker_error", "cases": [], "interpretation": []}
        _mark_active_unknown(payload, status="worker_error",
                             reason=f"{type(exc).__name__}: {str(exc)[:300]}")
        payload["status"] = "worker_error"
        payload["error_type"] = type(exc).__name__
        payload["error"] = str(exc)[:500]
        payload["worker_elapsed_seconds"] = round(time.monotonic() - started, 3)
        payload["completed_at_utc"] = datetime.now(UTC).isoformat()
        _atomic_write(output, payload)
        return 2


def run_parent() -> dict:
    if EVIDENCE_PATH.exists():
        raise FileExistsError(f"refusing to overwrite existing evidence: {EVIDENCE_PATH}")
    created_at = datetime.now(UTC).isoformat()
    initial = {
        "status": "worker_running", "created_at_utc": created_at,
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "run_scope": "separate prompt-format probe on exact frozen public pilot contexts; no retrieval or database",
        "worker_wall_clock_limit_seconds": MAX_WORKER_SECONDS,
        "case_order": list(CASE_IDS), "cases": [], "current_case": None,
        "semantic_quality": "pending_human_review",
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(EVIDENCE_PATH, initial)
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"})
    command = [sys.executable, "-m", "scripts.manual.generation_prompt_probe",
               _WORKER_ARG, str(EVIDENCE_PATH)]
    try:
        process = subprocess.Popen(command, cwd=REPO_ROOT / "backend", env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001 - report bounded child startup failures
        initial.update({"status": "worker_spawn_error", "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                        "completed_at_utc": datetime.now(UTC).isoformat()})
        _atomic_write(EVIDENCE_PATH, initial)
        return initial
    return _supervise_child(process, EVIDENCE_PATH, timeout_seconds=MAX_WORKER_SECONDS)


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == _WORKER_ARG:
        if len(sys.argv) != 3:
            raise SystemExit("offline worker requires its evidence path")
        raise SystemExit(_worker(Path(sys.argv[2])))
    report = run_parent()
    print(json.dumps({"status": report.get("status"),
                      "evidence": str(EVIDENCE_PATH.relative_to(REPO_ROOT)),
                      "wall_clock_seconds": report.get("process", {}).get("wall_clock_seconds"),
                      "timed_out": report.get("process", {}).get("timed_out")},
                     ensure_ascii=False, allow_nan=False))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
