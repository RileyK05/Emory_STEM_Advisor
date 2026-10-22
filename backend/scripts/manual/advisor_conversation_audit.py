"""Requested live, saved-conversation audit; fictional students, real provider.

Uses the synchronous persisted conversation HTTP route, including its real
history selection, archive/recall tools and research workspace. This does not
exercise the browser or streaming job transport. Results are manual evidence,
not pytest tests or automatically graded answers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.db.connection import close_pools
from app.generation.research_final import RESEARCH_PROMPT_VERSION
from app.generation.research_tools import RESEARCH_TOOLS_VERSION
from app.runtime import build_provider
from scripts.manual.advisor_prompt_audit import CASE, RecordedProvider


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


class ConversationProvider(RecordedProvider):
    def generate(self, request):
        try:
            context = json.loads(request.messages[1].content)
        except (ValueError, AttributeError, IndexError):
            context = {}
        result = super().generate(request)
        # Each case has sequential provider calls, even when conversations run
        # independently in parallel. Never retain credentials or SDK requests.
        row = next(r for r in reversed(self.calls) if r["case_id"] == CASE.get())
        row["history_untrusted"] = context.get("history_untrusted", "")
        row["finish_required"] = context.get("finish_required")
        row["state_revision"] = (context.get("state_model_assessments") or {}).get("revision")
        row["conversation_page_sequences"] = [
            t.get("sequence") for t in (context.get("conversation_page_untrusted") or {}).get(
                "data", {}).get("turns_untrusted", [])]
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["conversations"]
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    settings = load_settings()
    if not settings.database_url:
        pid = (settings.data_dir / "pgserver/postmaster.pid").read_text().splitlines()
        settings = replace(settings, database_url=f"postgresql://postgres@127.0.0.1:{int(pid[3])}/postgres")
    provider, error = build_provider(settings)
    if provider is None:
        raise RuntimeError(f"Configured provider unavailable: {error}")
    recording = ConversationProvider(provider)
    app = create_app(settings=settings, provider=recording)
    app.state.database_ready = True
    lock, last_admission = threading.Lock(), [0.0]
    metadata = {"started_at": datetime.now(UTC).isoformat(), "model": settings.llm_model_id,
        "provider": settings.llm_provider, "research_prompt_version": RESEARCH_PROMPT_VERSION,
        "research_tools_version": RESEARCH_TOOLS_VERSION, "collection": settings.default_collection,
        "research": settings.advising_research_enabled, "tools": settings.tools_enabled,
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "workers": args.workers, "conversation_count": len(cases),
        "planned_turns": sum(len(c["turns"]) for c in cases),
        "transport": "Real persisted /api/conversations/{id}/turns via FastAPI TestClient",
        "scope": "Fictional student reports; real models, corpus, saved history and research workspace. No browser/stream-worker claim.",
        "data_modifications": "Audit conversations, ordinary research/traces/usage only; no course, program or prerequisite edits",
        "audit_limits": {"provider_calls": recording.max_calls, "tokens": recording.max_tokens}}
    save(args.output / "run.json", metadata)

    def run(case):
        folder = args.output / case["id"]
        folder.mkdir(exist_ok=True)
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 52000),
                        raise_server_exceptions=False) as client:
            identity = folder / "conversation.json"
            if identity.exists():
                made = json.loads(identity.read_text(encoding="utf-8"))
            elif case.get("existing_conversation_id"):
                # Continuations use the actual saved transcript, never an
                # injected assistant history or a new conversation copy.
                response = client.get("/api/conversations/" + case["existing_conversation_id"])
                response.raise_for_status()
                made = {"conversationId": response.json()["conversationId"]}
                save(identity, made)
            else:
                response = client.post("/api/conversations", json={
                    "title": "[AUDIT 2026-10-22] " + case["id"] + " " + case["title"]})
                response.raise_for_status()
                made = response.json()
                save(identity, made)
            path = "/api/conversations/" + made["conversationId"]
            for index, turn in enumerate(case["turns"], 1):
                case_id = f"{case['id']}-T{index:02d}"
                CASE.set(case_id)
                output = folder / f"T{index:02d}.json"
                if output.exists():
                    continue
                transcript_response = client.get(path)
                transcript_response.raise_for_status()
                transcript = transcript_response.json()
                if transcript.get("pendingRequestId"):
                    raise RuntimeError("Audit conversation still has a pending request; no automatic abandonment")
                request_file = folder / f"T{index:02d}-request.json"
                if request_file.exists():
                    body = json.loads(request_file.read_text(encoding="utf-8"))
                else:
                    body = {"query": turn["prompt"], "requestId": str(uuid4()),
                        "expectedSequence": transcript["lastSequence"], "mode": "advising"}
                    if index == 1:
                        body["collectionId"] = settings.default_collection
                    save(request_file, body)
                with lock:
                    time.sleep(max(0, 2.1 - (time.perf_counter() - last_admission[0])))
                    last_admission[0] = time.perf_counter()
                row = {"id": case_id, "conversation_id": made["conversationId"],
                    "category": case["category"], **turn, "request": body,
                    "started_at": datetime.now(UTC).isoformat()}
                start = time.perf_counter()
                try:
                    response = client.post(path + "/turns", json=body)
                    row["http_status"] = response.status_code
                    try:
                        row["response"] = response.json()
                    except ValueError:
                        row["response"] = {"type": "non_json_error"}
                except Exception as exc:  # noqa: BLE001 - preserve safe manual diagnostics
                    row["runner_error_type"] = type(exc).__name__
                row["seconds"] = round(time.perf_counter() - start, 3)
                row["provider_calls"] = [r for r in recording.calls if r["case_id"] == case_id]
                row["review_status"] = "pending manual semantic review"
                save(output, row)
                final = client.get(path)
                final.raise_for_status()
                save(folder / "saved-transcript.json", final.json())
                print(json.dumps({"id": case_id, "http": row.get("http_status"),
                    "seconds": row["seconds"], "calls": len(row["provider_calls"])}), flush=True)
                if recording.exhausted:
                    raise RuntimeError("Audit provider budget exhausted; completed outputs preserved")

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(run, c) for c in cases]):
                future.result()
    finally:
        metadata.update(finished_at=datetime.now(UTC).isoformat(), calls=len(recording.calls),
                        accounted_tokens=recording.reserved_tokens)
        save(args.output / "run.json", metadata)
        close_pools()


if __name__ == "__main__":
    main()
