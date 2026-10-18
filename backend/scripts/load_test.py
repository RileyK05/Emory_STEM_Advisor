"""Repeatable concurrency harness: real API + real Postgres, fake model seams.

Not part of the test suite. It never downloads model weights and never calls a
live provider: a latency-simulating embedder and provider stand in for them, so
the numbers isolate the application's own limits (HTTP threads, connection
pools, worker serialization, SQL plans).

Run from backend/:

    python -m scripts.load_test --scenario query --requests 200 --concurrency 20
    python -m scripts.load_test --scenario conversation --requests 40 --workers 2

Without `--database-url` the run creates a disposable embedded Postgres under
`DATA_DIR/load-tests/` and seeds a fresh synthetic collection, so it never
touches a development database. Pass `--database-url` for a production-like
server; the collection is still created fresh unless `--collection` names one.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import shutil
import statistics
import threading
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import numpy as np

from app.config import Settings, load_settings

QUERIES = (
    "What are the prerequisites for BIOL 141?",
    "Which courses count toward the chemistry major?",
    "Can I take MATH 112 and PHYS 151 in the same semester?",
    "What is the prerequisite chain for a computer science degree?",
    "How many credits of humanities are required?",
)

_ANSWER = "An answer citing [1] the supplied context."


class _Vectors:
    """Deterministic unit vectors, so runs are comparable across machines."""

    def __init__(self, dim: int, seed: str = "load-test") -> None:
        self.dim = dim
        self.seed = seed

    def __call__(self, text: str) -> np.ndarray:
        digest = hashlib.sha256((self.seed + text).encode("utf-8")).digest()[:8]
        rng = np.random.default_rng(int.from_bytes(digest, "big"))
        vector = rng.standard_normal(self.dim).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return (vector / norm).astype(np.float32)


class LatencyEmbedder:
    """The Embedder protocol with a configurable per-query delay."""

    def __init__(self, dim: int = 768, delay_seconds: float = 0.03) -> None:
        self.dim = dim
        self.delay_seconds = delay_seconds
        self.vectors = _Vectors(dim)

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.vstack([self.vectors(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.vectors(text)


class LatencyProvider:
    """The typed Provider contract with a configurable generation delay."""

    def __init__(self, delay_seconds: float = 3.0, text: str = _ANSWER) -> None:
        self.delay_seconds = delay_seconds
        self.text = text
        self.calls = 0

    def _result(self):
        from app.llm.base import GenerationResult, Usage

        self.calls += 1
        return GenerationResult(
            text=self.text,
            finish_reason="stop",
            usage=Usage(prompt_tokens=2048, completion_tokens=64, source="provider"),
        )

    def generate(self, request):
        time.sleep(self.delay_seconds)
        return self._result()

    def stream(self, request):
        from app.llm.base import StreamEvent

        first, _, last = self.text.partition(" ")
        yield StreamEvent(type="text_delta", text=first + " ")
        time.sleep(self.delay_seconds)
        yield StreamEvent(
            type="text_delta", text=last
        )
        yield StreamEvent(type="completed", result=self._result())

    def chat(self, messages, *, max_new_tokens: int | None = None) -> str:
        time.sleep(self.delay_seconds)
        self._result()
        return self.text


def _lenient_config(settings: Settings):
    """Embeddings on with no floors, so synthetic text always retrieves."""
    from app.retrieval.config import load_retrieval_config

    config = load_retrieval_config(settings=settings)
    seams = replace(config.seams, embed=replace(config.seams.embed, min_similarity=0.0))
    return replace(config, seams=seams, fusion=replace(config.fusion, min_score=0.0))


def _dsn_and_root(args) -> tuple[str, Path, object | None]:
    """Resolve the database and a private data root; return the pgserver handle."""
    root = Path(
        args.data_dir or (load_settings().data_dir / "load-tests" / f"run-{uuid4().hex[:8]}")
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(root)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
        return args.database_url, root, None
    import pgserver

    server = pgserver.get_server(root / "pgserver", cleanup_mode="stop")
    dsn = server.get_uri()
    os.environ["DATABASE_URL"] = dsn
    return dsn, root, server


def _paragraph(document: int, block: int, words: int) -> str:
    """Unique text per document and block, so ingestion never dedupes the corpus."""
    topics = ("prerequisite", "corequisite", "credit", "semester", "course", "laboratory")
    index = document * 1009 + block
    body = " ".join(
        f"{topics[(index + offset) % len(topics)]}{offset % 97}" for offset in range(words)
    )
    return f"document {document} section {block} {body}"


def _seed(dsn: str, name: str, *, documents: int, chunks_per_document: int,
          embedder: LatencyEmbedder, config, model: str, root: Path) -> tuple[UUID, int]:
    """Create a collection of synthetic text once; return its id and chunk count."""
    from app.db import queries
    from app.db.connection import connect
    from app.db.migrate import apply_migrations
    from app.ingest.pipeline import ingest_file

    conn = connect(dsn)
    try:
        apply_migrations(conn)
        conn.commit()
        row = conn.execute(queries.get("get_collection_by_name"), {"name": name}).fetchone()
        if row is None:
            conn.execute(queries.get("insert_collection"),
                         {"collection_id": uuid4(), "name": name})
            conn.commit()
            row = conn.execute(queries.get("get_collection_by_name"), {"name": name}).fetchone()
        collection_id = row["collection_id"]
        count = conn.execute(queries.get("count_chunks_for_collection"),
                             {"collection_id": collection_id}).fetchone()["chunks"]
        if count:
            return collection_id, int(count)
        seed_dir = root / "seed"
        seed_dir.mkdir(parents=True, exist_ok=True)
        words = max(40, chunks_per_document * 400)
        for document in range(documents):
            path = seed_dir / f"document-{document:04d}.txt"
            body = "\n\n".join(_paragraph(document, block, 110)
                               for block in range(max(1, words // 110)))
            path.write_text(body, encoding="utf-8")
            ingest_file(
                conn, collection_id=collection_id, path=path, content_type="text/plain",
                count_tokens=embedder.count_tokens, embedder=embedder,
                embed_model=model, config=config,
            )
        conn.commit()
        count = conn.execute(queries.get("count_chunks_for_collection"),
                             {"collection_id": collection_id}).fetchone()["chunks"]
        return collection_id, int(count)
    finally:
        conn.close()


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def _summary(label: str, statuses: Counter, latencies: list[float], elapsed: float,
             extra: str = "") -> None:
    print(f"\n{label}")
    if extra:
        print(f"  {extra}")
    print("  status: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    if latencies:
        print(f"  latency ms: p50={_percentile(latencies, 0.50) * 1000:.0f} "
              f"p95={_percentile(latencies, 0.95) * 1000:.0f} "
              f"p99={_percentile(latencies, 0.99) * 1000:.0f} "
              f"max={max(latencies) * 1000:.0f} mean={statistics.mean(latencies) * 1000:.0f}")
        print(f"  throughput: {len(latencies) / elapsed:.2f} req/s over {elapsed:.1f}s")


def _pools() -> None:
    from app.db.connection import pool_stats

    print("  pools:")
    snapshots = pool_stats()
    if not snapshots:
        print("    (pooling disabled)")
    for item in snapshots:
        print(f"    {item['pool']}: size={item['size']}/{item['maxSize']} "
              f"available={item['available']} waiting={item['waiting']} errors={item['errors']}")


async def _run_queries(app, collection: str, requests: int, concurrency: int,
                       timeout: float) -> tuple[Counter, list[float], float, str]:
    statuses: Counter = Counter()
    latencies: list[float] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost",
                                 timeout=timeout) as client:
        gate = asyncio.Semaphore(concurrency)

        async def one(index: int) -> None:
            async with gate:
                start = time.perf_counter()
                try:
                    response = await client.post(
                        "/api/query",
                        json={"query": QUERIES[index % len(QUERIES)], "collectionId": collection},
                    )
                    statuses[str(response.status_code)] += 1
                except Exception as exc:  # noqa: BLE001 - a failed request is a datum
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append(time.perf_counter() - start)

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(requests)))
        elapsed = time.perf_counter() - started
    return statuses, latencies, elapsed, f"concurrency={concurrency}"


def _worker_loop(store, executor, worker_id: str, stop: threading.Event) -> None:
    from app.jobs.worker import run_once

    while not stop.is_set():
        try:
            job = run_once(store, worker_id, executor, lease_seconds=60)
        except Exception as exc:  # noqa: BLE001 - a worker fault must not stop the harness
            print(f"worker {worker_id}: {type(exc).__name__}: {exc}")
            stop.wait(0.5)
            continue
        if job is None:
            stop.wait(0.05)


def _start_workers(settings: Settings, config, embedder, provider, count: int,
                   stop: threading.Event) -> list[threading.Thread]:
    from app.conversations.worker import ConversationTurnExecutor
    from app.db.connection import connector
    from app.jobs.store import JobStore
    from app.runtime import build_connections

    profiles, runtime = build_connections(settings)
    executor = ConversationTurnExecutor(
        settings=settings, config=config, embedder=embedder, provider=provider,
        connection_profiles=profiles, connection_runtime=runtime,
        connect=connector(settings, pool_name="conversation_execution"),
    )
    store = JobStore(connector(settings))
    threads = [
        threading.Thread(target=_worker_loop,
                         args=(store, executor, f"load-{index}", stop), daemon=True)
        for index in range(count)
    ]
    for thread in threads:
        thread.start()
    return threads


async def _run_conversations(app, collection: str, requests: int, concurrency: int,
                             timeout: float, poll_seconds: float
                             ) -> tuple[Counter, list[float], float, str]:
    statuses: Counter = Counter()
    latencies: list[float] = []
    polls = 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost",
                                 timeout=timeout) as client:
        gate = asyncio.Semaphore(concurrency)

        async def one(index: int) -> None:
            nonlocal polls
            async with gate:
                start = time.perf_counter()
                try:
                    created = await client.post("/api/conversations",
                                                json={"title": f"load {index}"})
                    conversation = created.json()["conversationId"]
                    request_id = str(uuid4())
                    started = await client.post(
                        f"/api/conversations/{conversation}/turns/stream",
                        json={"query": QUERIES[index % len(QUERIES)],
                              "collectionId": collection, "requestId": request_id,
                              "expectedSequence": 0},
                    )
                    started.raise_for_status()
                    after = 0
                    while True:
                        events = await client.get(
                            f"/api/conversations/{conversation}/turns/{request_id}/events",
                            params={"afterEventId": after},
                        )
                        events.raise_for_status()
                        polls += 1
                        body = events.json()
                        after = body.get("nextEventId", after)
                        if body.get("done"):
                            statuses[body.get("requestState") or body.get("jobState")] += 1
                            break
                        await asyncio.sleep(poll_seconds)
                except Exception as exc:  # noqa: BLE001 - a failed request is a datum
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append(time.perf_counter() - start)

        started_at = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(requests)))
        elapsed = time.perf_counter() - started_at
    return statuses, latencies, elapsed, (
        f"concurrency={concurrency} polls={polls} pollInterval={poll_seconds * 1000:.0f}ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("query", "conversation"), default="query")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--workers", type=int, default=1,
                        help="conversation worker threads (one job at a time each)")
    parser.add_argument("--documents", type=int, default=40)
    parser.add_argument("--chunks-per-document", type=int, default=25)
    parser.add_argument("--provider-latency-ms", type=int, default=3000)
    parser.add_argument("--embed-latency-ms", type=int, default=30)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--pool-size", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--keep-data", action="store_true",
                        help="keep the per-run data directory (raw uploads, embedded database)")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.workers < 0:
        parser.error("requests and concurrency must be positive; workers cannot be negative")
    if args.scenario == "conversation" and args.workers < 1:
        parser.error("the conversation scenario needs at least one worker")

    dsn, root, server = _dsn_and_root(args)
    try:
        from app.api import create_app
        from app.db.bootstrap import prepare_database
        from app.db.connection import connect

        settings = replace(
            load_settings(), database_url=dsn, data_dir=root,
            db_pool_max_size=args.pool_size, auth_mode="development",
            tools_enabled=False, query_planning_enabled=False,
            advising_research_enabled=False,
        )
        config = _lenient_config(settings)
        embedder = LatencyEmbedder(delay_seconds=args.embed_latency_ms / 1000)
        provider = LatencyProvider(delay_seconds=args.provider_latency_ms / 1000)
        collection = args.collection or f"loadtest-{uuid4().hex[:8]}"

        bootstrap = connect(dsn)
        try:
            prepare_database(bootstrap, collection)
        finally:
            bootstrap.close()
        collection_id, chunks = _seed(
            dsn, collection, documents=args.documents,
            chunks_per_document=args.chunks_per_document, embedder=embedder,
            config=config, model=settings.embedding_model_id, root=root,
        )
        print(f"database={'external' if args.database_url else 'disposable'} "
              f"collection={collection} chunks={chunks} "
              f"poolSize={settings.db_pool_max_size}")

        app = create_app(settings=settings, config=config, embedder=embedder,
                         provider=provider)
        stop = threading.Event()
        workers = []
        try:
            if args.scenario == "conversation":
                workers = _start_workers(settings, config, embedder, provider,
                                         args.workers, stop)
                statuses, latencies, elapsed, extra = asyncio.run(
                    _run_conversations(app, collection, args.requests, args.concurrency,
                                       args.timeout_seconds, args.poll_ms / 1000))
            else:
                statuses, latencies, elapsed, extra = asyncio.run(
                    _run_queries(app, collection, args.requests, args.concurrency,
                                 args.timeout_seconds))
        finally:
            stop.set()
            for worker in workers:
                worker.join(timeout=5)

        from app.db.connection import close_pools

        _summary(args.scenario, statuses, latencies, elapsed, extra=extra)
        print(f"  provider calls: {provider.calls}")
        print(f"  corpus: {chunks} chunks in collection {collection_id}")
        _pools()
        close_pools()
    finally:
        if server is not None:
            server.cleanup()
        if not args.keep_data:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
