"""Bounded offline probe for the configured cached HF-local streaming provider.

This manual diagnostic performs exactly one trivial, public generation request.
The parent process owns the 120-second wall-clock bound so model loading is
included; the child is killed only by its parent if the bound is exceeded.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.util.digest import file_sha256

_sha256_file = file_sha256


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_ROOT = (REPO_ROOT / "data" / "manual-generation-probes").resolve()
EVIDENCE_PATH = (
    REPO_ROOT / "docs" / "evidence" / "hf-local-stream-probe-stop-tokens-2026-10-08.json"
)
MAX_PROCESS_SECONDS = 120.0
EXPECTED_MODEL = "openbmb/MiniCPM-2B-128k"
_WORKER_ARG = "--offline-worker"


def _json_line(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
    sys.stdout.flush()


def _disable_network() -> None:
    """Block Python socket access in the model worker in addition to HF offline mode."""
    import socket

    def denied(*_args, **_kwargs):
        raise RuntimeError("network access is disabled in this offline model probe")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied


def _worker(run_dir: Path) -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    _disable_network()

    stage = "preflight"
    started = time.monotonic()
    try:
        from huggingface_hub import try_to_load_from_cache

        from app.config import load_settings

        settings = load_settings()
        if settings.llm_provider != "hf-local":
            raise RuntimeError("configured provider is not hf-local")
        if settings.hf_model_id != EXPECTED_MODEL:
            raise RuntimeError("configured hf-local model is not the cached MiniCPM snapshot")
        if settings.hf_max_context > 4096:
            raise RuntimeError("configured local context exceeds the probe's 4096-token ceiling")
        _json_line({"type": "progress", "stage": stage, "model_id": settings.hf_model_id,
                    "context_ceiling": settings.hf_max_context, "offline": True})

        config_file = try_to_load_from_cache(settings.hf_model_id, "config.json")
        if not config_file:
            raise RuntimeError("configured model config is not present in the local HF cache")
        snapshot = Path(config_file).parent
        if not snapshot.is_dir():
            raise RuntimeError("resolved local model snapshot is missing")
        required = (
            "config.json", "configuration_minicpm.py", "modeling_minicpm.py",
            "pytorch_model.bin", "tokenizer.json", "tokenizer.model",
            "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
        )
        missing = [name for name in required if not (snapshot / name).is_file()]
        if missing:
            raise RuntimeError("cached model snapshot is incomplete: " + ", ".join(missing))

        stage = "hashing_cached_model_files"
        _json_line({"type": "progress", "stage": stage,
                    "snapshot_revision": snapshot.name, "file_count": len(required)})
        model_files = []
        for name in required:
            path = snapshot / name
            model_files.append({"name": name, "size_bytes": path.stat().st_size,
                                "sha256": _sha256_file(path)})
            _json_line({"type": "progress", "stage": stage, "file_hashed": name})
        model_hash_payload = json.dumps(
            model_files, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        model_snapshot_sha256 = hashlib.sha256(model_hash_payload).hexdigest()

        import tokenizers
        import torch
        import transformers

        safe_environment = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "tokenizers": tokenizers.__version__,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "provider": settings.llm_provider,
            "model_id": settings.hf_model_id,
            "hf_max_context": settings.hf_max_context,
            "hf_max_new_tokens_configured": settings.hf_max_new_tokens,
            "hf_stop_token_strings": list(settings.hf_stop_token_strings),
            "sampling_policy": settings.llm_sampling_policy,
            "request_max_output_tokens": 4,
            "request_timeout_seconds": MAX_PROCESS_SECONDS,
            "offline_flags": ["HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1",
                              "HF_DATASETS_OFFLINE=1", "Python socket APIs blocked"],
        }
        environment_sha256 = hashlib.sha256(json.dumps(
            safe_environment, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()
        code_files = [
            REPO_ROOT / "backend" / "app" / "llm" / "hf_local.py",
            REPO_ROOT / "backend" / "app" / "llm" / "factory.py",
            REPO_ROOT / "backend" / "app" / "llm" / "base.py",
            REPO_ROOT / "backend" / "app" / "config.py",
        ]
        code_hashes = {str(path.relative_to(REPO_ROOT)): _sha256_file(path)
                       for path in code_files}
        manifest = {
            "model_id": settings.hf_model_id,
            "snapshot_revision": snapshot.name,
            "snapshot_sha256": model_snapshot_sha256,
            "files": model_files,
            "environment": safe_environment,
            "environment_sha256": environment_sha256,
            "provider_code_sha256": code_hashes,
        }
        (run_dir / "preflight-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )

        from app.llm.base import GenerationRequest, TextMessage
        from app.llm.factory import get_provider

        stage = "streaming"
        provider = get_provider(settings, timeout_seconds=MAX_PROCESS_SECONDS)
        prompt = "Reply with one word: hello"
        generation_started = time.monotonic()
        first_delta_seconds = None
        pieces = []
        terminal = None
        request = GenerationRequest(
            messages=(TextMessage(role="user", content=prompt),),
            max_output_tokens=4,
            temperature=0,
            timeout_seconds=MAX_PROCESS_SECONDS,
        )
        for event in provider.stream(request):
            now = time.monotonic()
            if event.type == "text_delta":
                if first_delta_seconds is None:
                    first_delta_seconds = now - generation_started
                pieces.append(event.text)
                _json_line({"type": "delta", "text": event.text,
                            "elapsed_seconds": now - generation_started,
                            "first_delta_seconds": first_delta_seconds})
            elif event.type == "completed" and event.result is not None:
                terminal = {
                    "type": "terminal",
                    "status": "completed",
                    "generation_elapsed_seconds": now - generation_started,
                    "first_delta_seconds": first_delta_seconds,
                    "delta_count": len(pieces),
                    "model_output_unverified": "".join(pieces),
                    "usage": {
                        "prompt_tokens": event.result.usage.prompt_tokens,
                        "completion_tokens": event.result.usage.completion_tokens,
                        "total_tokens": event.result.usage.total_tokens,
                        "source": event.result.usage.source,
                    },
                    "finish_reason": event.result.finish_reason,
                    "model": event.result.model,
                    "semantic_quality": "not_assessed",
                }
                _json_line(terminal)
        if terminal is None:
            _json_line({"type": "terminal", "status": "stream_ended_without_completion",
                        "usage": {"source": "unknown"},
                        "model_output_unverified": "".join(pieces),
                        "semantic_quality": "not_assessed"})
            return 2
        return 0
    except Exception as exc:  # noqa: BLE001 - bounded manual diagnostic boundary
        payload = {"type": "terminal", "status": "error", "stage": stage,
                   "error_type": type(exc).__name__, "error": str(exc)[:500],
                   "elapsed_seconds": time.monotonic() - started,
                   "semantic_quality": "not_assessed"}
        usage = getattr(exc, "usage", None)
        if usage is not None:
            payload["usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "source": getattr(usage, "source", "unknown"),
            }
        else:
            payload["usage"] = {"source": "unknown"}
        _json_line(payload)
        return 2


def _pump(pipe, messages: queue.Queue, label: str) -> None:
    try:
        for line in pipe:
            messages.put((label, line))
    finally:
        messages.put((label, None))


def run_parent() -> dict:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS_ROOT / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(exist_ok=False)
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1"})
    command = [sys.executable, "-m", "scripts.manual.hf_local_stream_probe", _WORKER_ARG,
               str(run_dir)]
    started_utc = datetime.now(UTC).isoformat()
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=REPO_ROOT / "backend", env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", bufsize=1,
    )
    messages: queue.Queue = queue.Queue()
    stdout_thread = threading.Thread(target=_pump, args=(process.stdout, messages, "stdout"), daemon=True)
    stderr_thread = threading.Thread(target=_pump, args=(process.stderr, messages, "stderr"), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    records = []
    stderr_tail = []
    progress = "starting"
    terminal = None
    stdout_done = stderr_done = False
    timed_out = False
    deadline = started + MAX_PROCESS_SECONDS

    while not (stdout_done and stderr_done and process.poll() is not None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            process.kill()  # only the child process created above
            break
        try:
            label, line = messages.get(timeout=min(0.1, remaining))
        except queue.Empty:
            continue
        if line is None:
            if label == "stdout":
                stdout_done = True
            else:
                stderr_done = True
            continue
        if label == "stderr":
            stderr_tail.append(line.rstrip())
            stderr_tail = stderr_tail[-20:]
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            records.append({"type": "malformed_worker_line", "line": line[:500]})
            continue
        records.append(record)
        if record.get("type") == "progress":
            progress = str(record.get("stage", progress))
        if record.get("type") == "terminal":
            terminal = record

    if timed_out:
        process.wait(timeout=5)
    else:
        process.wait(timeout=5)
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    elapsed = time.monotonic() - started
    if timed_out:
        result = {"status": "process_timeout", "stage_at_timeout": progress,
                  "usage": {"source": "unknown"},
                  "partial_output_unverified": "".join(
                      r.get("text", "") for r in records if r.get("type") == "delta"
                  ),
                  "semantic_quality": "not_assessed"}
    elif terminal is not None:
        result = terminal
    else:
        result = {"status": "worker_exited_without_terminal", "usage": {"source": "unknown"},
                  "semantic_quality": "not_assessed"}

    manifest_path = run_dir / "preflight-manifest.json"
    manifest_content = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file() else None
    )
    report = {
        "status": "manual_cached_hf_local_stream_probe",
        "created_at_utc": started_utc,
        "run_directory": str(run_dir.relative_to(REPO_ROOT)),
        "scope": "one trivial public prompt; no private context, RAG, paid API, or network",
        "probe_script_sha256": _sha256_file(Path(__file__).resolve()),
        "preflight_manifest": {
            "path": str(manifest_path.relative_to(REPO_ROOT)),
            "sha256": (_sha256_file(manifest_path) if manifest_content is not None else None),
            "content": manifest_content,
        },
        "request": {"prompt": "Reply with one word: hello", "max_output_tokens": 4,
                    "temperature": 0, "timeout_seconds": MAX_PROCESS_SECONDS},
        "offline_enforcement": {
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1", "python_socket_apis_blocked_in_worker": True,
        },
        "process": {"wall_clock_seconds": elapsed,
                    "maximum_seconds": MAX_PROCESS_SECONDS,
                    "exit_code": process.returncode, "timed_out": timed_out},
        "worker_events": records,
        "result": result,
        "stderr_tail": stderr_tail,
        "semantic_quality": "not_assessed",
        "note": "Incremental timing measures cold stream startup (cache loading included) to first delta and completion.",
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == _WORKER_ARG:
        if len(sys.argv) != 3:
            raise SystemExit("worker requires an isolated run directory")
        raise SystemExit(_worker(Path(sys.argv[2])))
    report = run_parent()
    _json_line({"status": report["result"]["status"],
                "evidence": str(EVIDENCE_PATH),
                "run_directory": report["run_directory"],
                "wall_clock_seconds": report["process"]["wall_clock_seconds"],
                "timed_out": report["process"]["timed_out"]})
    if report["result"]["status"] != "completed" or report["process"]["exit_code"] != 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
