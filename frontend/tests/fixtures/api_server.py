"""Test-only real API server backed by isolated Postgres and deterministic fakes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

# Match pytest: browser fixtures never load developer credentials or settings.
os.environ["ADVISOR_SKIP_DOTENV"] = "1"

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))


def _verified_child(path: Path, parent: Path) -> Path:
    resolved_parent = parent.resolve()
    resolved = path.resolve()
    if resolved == resolved_parent or not resolved.is_relative_to(resolved_parent):
        raise RuntimeError("test data path is outside its approved workspace directory")
    return resolved


def _short_windows_path(path: Path) -> Path:
    """Use a short or extended path for deep content-addressed files on Windows."""
    if os.name != "nt":
        return path
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        if length and length < len(buffer):
            alias = Path(buffer.value)
            if alias.resolve() == path.resolve():
                return alias
    except (OSError, AttributeError):
        pass
    if path.drive and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--dist", required=True)
    args = parser.parse_args()

    data_root = _verified_child(Path(args.data_root), ROOT / "data" / "test-runs")
    pgdata = _verified_child(data_root / "pgserver", data_root)
    runtime_data = _verified_child(data_root / "runtime", data_root)
    runtime_data_for_app = _short_windows_path(runtime_data)
    dist = Path(args.dist).resolve()
    if not dist.is_relative_to(ROOT / "frontend" / "dist") or not (dist / "index.html").is_file():
        raise RuntimeError("frontend dist directory is outside the repository frontend build")
    pgdata.mkdir(parents=True, exist_ok=False)
    runtime_data.mkdir(parents=True, exist_ok=False)

    pg = None
    server = None
    thread = None
    try:
        import pgserver
        import uvicorn
        from app.api import create_app
        from app.config import load_settings
        from app.connections.models import EvaluationEvidence, ProfileCreate
        from app.db import queries
        from app.db.bootstrap import prepare_database
        from app.db.connection import connect
        from app.llm.base import GenerationResult, StreamEvent, Usage
        from app.llm.batch import BatchItemResult, BatchProgress, BatchSubmission
        from cryptography.fernet import Fernet
        from fakes import FakeEmbedder, ScriptedProvider
        from fastapi.staticfiles import StaticFiles

        batch_suite_hash = "b" * 64

        class SwitchableFakeEmbedder(FakeEmbedder):
            fail_next_documents = False

            def embed_documents(self, texts):
                if self.fail_next_documents:
                    self.fail_next_documents = False
                    raise RuntimeError("test-only reindex failure")
                return super().embed_documents(texts)

        # Preserve the production route's sanitized HTTP error while making
        # test-only failures diagnosable without returning internal details.
        from app.api.routes import corpus as api_routes
        original_ingest = api_routes.ingest_file

        def trace_ingest_failure(*args, **kwargs):
            try:
                return original_ingest(*args, **kwargs)
            except Exception as exc:
                print(f"TEST INGEST FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                raise

        api_routes.ingest_file = trace_ingest_failure

        pg = pgserver.get_server(str(pgdata), cleanup_mode="stop")
        dsn = pg.get_uri()
        os.environ["DATA_DIR"] = str(runtime_data_for_app)
        settings = replace(
            load_settings(),
            llm_provider="openai-compatible",
            llm_model_id="offline-browser-fake",
            tools_enabled=True,
            database_url=dsn,
            data_dir=runtime_data_for_app,
            default_collection="live-browser-integration",
            auth_mode="development",
            auth_server_key=None,
            admin_api_enabled=False,
            office_enabled=False,
            profile_encryption_key=Fernet.generate_key().decode(),
            llm_api_key=None,
            llm_base_url=None,
            routing_enabled=False,
            routing_eval_suite_hash=batch_suite_hash,
        )
        bootstrap_conn = connect(dsn)
        try:
            prepare_database(bootstrap_conn, settings.default_collection)
            bootstrap_conn.execute(queries.get("insert_collection"), {
                "collection_id": uuid4(), "name": "gap-review-nondefault",
            })
            bootstrap_conn.commit()
        finally:
            bootstrap_conn.close()
        embedder = SwitchableFakeEmbedder()
        provider = ScriptedProvider(
            "Students must complete the robotics lab orientation before using equipment. [1]",
            model="offline-browser-fake",
            usage=Usage(prompt_tokens=20, completion_tokens=12, source="provider"),
        )
        provider.calls = 0
        provider.fail_next = False
        original_generate = provider.generate
        provider_call_records = []
        provider_call_lock = threading.Lock()
        provider_call_context = threading.local()
        provider_stream_control = {"slow_next": False, "started": threading.Event(),
                                   "attempts": 0,
                                   "release": threading.Event()}

        def test_generate(request):
            with provider_call_lock:
                provider_call_records.append({
                    "call": provider.calls + 1,
                    "kind": ("conversation_stream" if getattr(
                        provider_call_context, "conversation_stream", False
                    ) else "other_chat"),
                    "streamAttempt": getattr(provider_call_context, "stream_attempt", None),
                    "requestId": getattr(provider_call_context, "request_id", None),
                })
            if provider.fail_next:
                provider.fail_next = False
                provider.calls += 1
                raise RuntimeError("test-only provider failure")
            provider.calls += 1
            return original_generate(request)

        provider.generate = test_generate

        def test_stream(request):
            provider_stream_control["attempts"] += 1
            provider_call_context.conversation_stream = True
            provider_call_context.stream_attempt = provider_stream_control["attempts"]
            yield StreamEvent("text_delta", text="Provisional, unverified answer text.")
            if provider_stream_control["slow_next"]:
                provider_stream_control["slow_next"] = False
                provider_stream_control["started"].set()
                provider_stream_control["release"].wait(timeout=30)
            yield StreamEvent("completed", result=provider.generate(request))

        provider.stream = test_stream

        class LocalFakeBatchAdapter:
            provider = "openai"
            model = "offline-batch-fixture"

            def __init__(self):
                self.submissions = 0
                self.status_calls = 0
                self.result_calls = 0
                self.cancel_calls = 0
                self.fail_next_submission = True
                self.keys = ()
                self.candidate = json.dumps({
                    "spec_json": json.dumps({"kind": "document", "title": "Batch guide",
                                             "blocks": [{"id": "p1", "type": "paragraph", "text": "A batch-generated draft."}]}),
                    "bindings": [],
                })

            def submit(self, items):
                self.submissions += 1
                if self.fail_next_submission:
                    self.fail_next_submission = False
                    raise TimeoutError("test-only ambiguous submission")
                self.keys = tuple(item.correlation_id for item in items)
                return BatchSubmission(f"offline-remote-{self.submissions}", self.provider, "in_progress", len(self.keys))

            def status(self, batch_id):
                self.status_calls += 1
                return BatchProgress(batch_id, self.provider, "completed", len(self.keys), len(self.keys), 0)

            def results(self, batch_id, *, expected_ids=None):
                self.result_calls += 1
                usage = Usage(prompt_tokens=10, completion_tokens=6, source="provider")
                return tuple(BatchItemResult(key, result=GenerationResult(
                    text=self.candidate, model=self.model, finish_reason="stop", usage=usage,
                )) for key in expected_ids)

            def cancel(self, batch_id):
                self.cancel_calls += 1
                return BatchProgress(batch_id, self.provider, "cancelled", len(self.keys), 0, 0)

        class LocalFakeConnectionRuntime:
            def __init__(self, adapter): self.adapter = adapter
            def generation_provider(self, *args, **kwargs): return object()
            def batch_provider(self, *args, **kwargs): return self.adapter
            def existing_batch_provider(self, profile, *, expected_provider):
                if expected_provider != profile.provider: raise RuntimeError("provider mismatch")
                return self.adapter

        batch_adapter = LocalFakeBatchAdapter()
        app = create_app(settings=settings, embedder=embedder, provider=provider)
        app.state.database_ready = True
        app.state.batch_connect_factory = lambda: connect(dsn)
        app.state.connection_runtime = LocalFakeConnectionRuntime(batch_adapter)
        with connect(dsn) as conn:
            profile = app.state.connection_profiles.create(conn, ProfileCreate(
                name="Offline browser batch profile", provider="openai", model_id=batch_adapter.model,
                roles=frozenset({"fast_generation", "complex"}),
                capabilities=frozenset({"batch", "structured_output"}),
                data_scopes=frozenset({"institutional_corpus", "user_supplied_data", "private_workspace"}),
                secret={"api_key": "offline-test-secret"},
            ))
            app.state.connection_profiles.record_evaluation(conn, EvaluationEvidence(
                evaluation_id=uuid4(), profile_id=profile.profile_id, profile_revision=profile.revision,
                provider=profile.provider, model_id=profile.model_id,
                suite_hash=batch_suite_hash, status="passed", metrics={"offline_fixture": 1.0},
            ))
            conn.commit()

        from app.conversations.worker import ConversationTurnExecutor
        from app.jobs.store import JobStore
        from app.jobs.worker import run_once

        worker_stop = threading.Event()
        worker_execution_records = []
        worker_execution_lock = threading.Lock()
        conversation_worker_store = JobStore(lambda: connect(dsn))
        conversation_executor = ConversationTurnExecutor(
            settings=app.state.settings, config=app.state.config, embedder=embedder,
            provider=provider, connection_profiles=app.state.connection_profiles,
            connection_runtime=app.state.connection_runtime, connect=lambda: connect(dsn),
        )

        class ConversationOnlyExecutor:
            def __call__(self, job, context):
                if job.kind != "conversation.turn":
                    raise RuntimeError("unexpected integration test job kind")
                provider_call_context.request_id = job.payload.get("requestId")
                record = {"jobId": str(job.job_id), "requestId": job.payload.get("requestId"),
                          "attempt": job.attempt_count, "phase": "started"}
                with worker_execution_lock:
                    worker_execution_records.append(record)
                try:
                    result = conversation_executor(job, context)
                    record["phase"] = "returned"
                    return result
                except Exception as exc:
                    record["phase"] = "raised"
                    record["errorType"] = type(exc).__name__
                    raise
                finally:
                    provider_call_context.request_id = None

            def publish_success(self, conn, job, value):
                return conversation_executor.publish_success(conn, job, value)

        def conversation_worker_loop():
            worker = ConversationOnlyExecutor()
            while not worker_stop.is_set():
                try:
                    job = run_once(conversation_worker_store, "live-browser-worker", worker,
                                   lease_seconds=30)
                except Exception as exc:  # noqa: BLE001 - expose integration worker failures in stderr
                    print(f"TEST CONVERSATION WORKER FAILURE: {type(exc).__name__}",
                          file=sys.stderr, flush=True)
                    worker_stop.wait(0.05)
                    continue
                if job is None:
                    worker_stop.wait(0.05)

        conversation_worker_thread = threading.Thread(target=conversation_worker_loop, daemon=True)
        conversation_worker_thread.start()

        control = {"server": None}

        @app.post("/__test/shutdown")
        async def shutdown_for_test():
            instance = control["server"]
            if instance is not None:
                instance.should_exit = True
            return {"stopping": True}

        @app.post("/__test/fail-next-embedding")
        async def fail_next_embedding_for_test():
            embedder.fail_next_documents = True
            return {"armed": True}

        @app.post("/__test/fail-next-provider")
        async def fail_next_provider_for_test():
            provider.fail_next = True
            return {"armed": True}

        @app.post("/__test/slow-next-provider")
        async def slow_next_provider_for_test():
            provider_stream_control["slow_next"] = True
            provider_stream_control["started"].clear()
            provider_stream_control["release"].clear()
            return {"armed": True}

        @app.get("/__test/provider-stream-started")
        async def provider_stream_started_for_test():
            return {"started": provider_stream_control["started"].is_set(),
                    "attempts": provider_stream_control["attempts"]}

        @app.post("/__test/release-provider-stream")
        async def release_provider_stream_for_test():
            provider_stream_control["release"].set()
            return {"released": True}

        @app.get("/__test/provider-calls")
        async def provider_calls_for_test():
            with provider_call_lock:
                with worker_execution_lock:
                    worker_records = list(worker_execution_records[-64:])
                return {"calls": provider.calls, "records": list(provider_call_records[-64:]),
                        "streamAttempts": provider_stream_control["attempts"],
                        "workerExecutions": worker_records}

        @app.get("/__test/conversation-job")
        async def conversation_job_for_test(conversation_id: str, request_id: str):
            with connect(dsn) as conn:
                request_row = conn.execute(queries.get("get_conversation_request"), {
                    "conversation_id": conversation_id, "request_id": request_id,
                }).fetchone()
                if request_row is None or request_row["job_id"] is None:
                    return {"found": False}
                job = conn.execute(queries.get("get_job"), {
                    "job_id": request_row["job_id"],
                }).fetchone()
                conn.commit()
                return {"found": job is not None, "jobId": str(request_row["job_id"]),
                        "requestState": request_row["state"],
                        "jobState": job["status"] if job else None,
                        "attemptCount": job["attempt_count"] if job else None,
                        "maxAttempts": job["max_attempts"] if job else None}

        @app.get("/__test/batch-stats")
        async def batch_stats_for_test():
            return {"submissions": batch_adapter.submissions,
                    "polls": batch_adapter.status_calls, "results": batch_adapter.result_calls,
                    "cancels": batch_adapter.cancel_calls}

        @app.post("/__test/enable-batch-routing")
        async def enable_batch_routing_for_test():
            app.state.settings = replace(app.state.settings, routing_enabled=True)
            return {"enabled": True}

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(2048)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
        server = uvicorn.Server(config)
        control["server"] = server
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 45
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("local API server exited before startup")
            if time.monotonic() >= deadline:
                raise TimeoutError("local API server startup timed out")
            time.sleep(0.05)
        print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{port}", "pid": __import__("os").getpid()}), flush=True)
        thread.join()
    finally:
        if "worker_stop" in locals():
            worker_stop.set()
        if "conversation_worker_thread" in locals() and conversation_worker_thread.is_alive():
            conversation_worker_thread.join(timeout=10)
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=20)
        if pg is not None:
            try:
                pg.cleanup()
            except Exception as exc:  # noqa: BLE001 - cleanup may be locked on Windows
                print(f"Postgres cleanup warning: {type(exc).__name__}", file=sys.stderr, flush=True)
        for target, parent in ((pgdata, data_root), (runtime_data, data_root)):
            try:
                shutil.rmtree(_verified_child(target, parent))
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"Leaving test data directory after cleanup lock: {exc.__class__.__name__}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
