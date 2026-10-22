"""User-requested live manual prompt ledger; not a pytest suite or benchmark.

Uses the real HTTP handlers, current data and configured provider. Only the
database address (borrowed from the running embedded server) is supplied when
absent. Does not migrate, ingest, change feature flags or change prompts.
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.db.connection import close_pools, connect
from app.generation.research_final import RESEARCH_PROMPT_VERSION
from app.generation.research_tools import RESEARCH_TOOLS_VERSION
from app.llm.base import InvalidRequestError, normalize_message
from app.runtime import build_provider

ROOT = Path(__file__).resolve().parents[3]
CASE = contextvars.ContextVar("manual_audit_case", default="unknown")


class RecordedProvider:
    def __init__(self, provider, max_calls=1200, max_tokens=12000000):
        self.provider = provider
        self.calls = []
        self.lock = threading.Lock()
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.reserved_tokens = 0
        self.exhausted = False

    def generate(self, request):
        reservation = len(json.dumps([normalize_message(m) for m in request.messages], default=str).encode())
        reservation += request.max_output_tokens or 4096
        with self.lock:
            if len(self.calls) >= self.max_calls or self.reserved_tokens + reservation > self.max_tokens:
                self.exhausted = True
                raise InvalidRequestError("Manual audit budget exhausted", provider="manual-audit")
            self.reserved_tokens += reservation
            row = {"case_id": CASE.get(), "started_at": datetime.now(UTC).isoformat()}
            self.calls.append(row)
        print(json.dumps({"event": "provider_start", "case": CASE.get()}), flush=True)
        start = time.perf_counter()
        try:
            result = self.provider.generate(request)
            row.update(text=result.text, model=result.model, finish_reason=result.finish_reason,
                       usage=asdict(result.usage),
                       tool_calls=[asdict(call) for call in result.tool_calls])
            if result.usage.total_tokens is not None:
                with self.lock:
                    self.reserved_tokens += result.usage.total_tokens - reservation
            return result
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row["seconds"] = round(time.perf_counter() - start, 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    if not settings.database_url:
        pid = (settings.data_dir / "pgserver/postmaster.pid").read_text().splitlines()
        settings = replace(settings, database_url=f"postgresql://postgres@127.0.0.1:{int(pid[3])}/postgres")
    with connect(settings.database_url) as conn:
        version = conn.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
        inventory = conn.execute("SELECT count(*) AS count FROM courses").fetchone()
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        cases = [case for case in cases if case["id"] in args.ids]
    cases = [case for case in cases if not (args.output / (case["id"] + ".json")).exists()]
    provider, error = build_provider(settings)
    if provider is None:
        raise RuntimeError(f"Configured provider unavailable: {error}")
    recording = RecordedProvider(provider)
    admission_lock = threading.Lock()
    last_admission = [0.0]
    app = create_app(settings=settings, provider=recording)
    app.state.database_ready = True
    metadata = {"started_at": datetime.now(UTC).isoformat(), "provider": settings.llm_provider,
                "model": settings.llm_model_id, "tools": settings.tools_enabled,
                "research": settings.advising_research_enabled, "planning": settings.query_planning_enabled,
                "collection": settings.default_collection, "schema": version, "courses": inventory,
                "research_prompt_version": RESEARCH_PROMPT_VERSION,
                "research_tools_version": RESEARCH_TOOLS_VERSION,
                "audit_limits": {"provider_calls": recording.max_calls, "tokens": recording.max_tokens},
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "workers": args.workers, "transport": "FastAPI TestClient, real /api/query handler",
                "scope": "Stateless individual prompts; not a frontend or multi-turn test",
                "prompt_modifications": False, "data_modifications": "Normal traces/research/usage only"}
    (args.output / "run.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")

    def run(case):
        CASE.set(case["id"])
        with admission_lock:
            # Stay below the app's ordinary 30-query/minute rate policy even
            # when a provider or harness failure returns immediately.
            time.sleep(max(0, 2.1 - (time.perf_counter() - last_admission[0])))
            last_admission[0] = time.perf_counter()
        start = time.perf_counter()
        row = dict(case, started_at=datetime.now(UTC).isoformat())
        try:
            with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000),
                            raise_server_exceptions=False) as client:
                response = client.post("/api/query", json={"query": case["prompt"]})
            row["http_status"] = response.status_code
            try:
                row["response"] = response.json()
            except ValueError:
                row["response"] = {"type": "non_json_error"}
        except Exception as exc:  # noqa: BLE001 - every manual test needs a durable result
            row["runner_error_type"] = type(exc).__name__
        row["seconds"] = round(time.perf_counter() - start, 3)
        if recording.exhausted:
            row["runner_budget_exhausted"] = True
        row["provider_calls"] = [call for call in recording.calls if call["case_id"] == case["id"]]
        row["review_status"] = "pending manual semantic review"
        (args.output / (case["id"] + ".json")).write_text(
            json.dumps(row, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps({"id": case["id"], "http": row.get("http_status"), "seconds": row["seconds"],
                          "type": row.get("response", {}).get("type"),
                          "outcome": (row.get("response", {}).get("research") or {}).get("outcome"),
                          "calls": len(row["provider_calls"])}), flush=True)
        return row

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for future in as_completed([executor.submit(run, case) for case in cases]):
                future.result()
    finally:
        metadata.update(finished_at=datetime.now(UTC).isoformat(), calls=len(recording.calls),
                        accounted_tokens=recording.reserved_tokens)
        (args.output / "run.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
        close_pools()


if __name__ == "__main__":
    main()
