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
import math
import os
import random
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

    def chunk_tokens(self, text: str) -> int:
        return len(text.split())

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.vstack([self.vectors(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.vectors(text)


def _sample_lognormal(rng: random.Random, p50: float, p95: float) -> float:
    """One draw matching a median and 95th percentile."""
    if p50 <= 0:
        return 0.0
    if p95 <= p50:
        return p50
    sigma = (math.log(p95) - math.log(p50)) / 1.6448536269514722
    return math.exp(rng.gauss(math.log(p50), sigma))


class LatencyProvider:
    """The typed Provider contract with a configurable latency/token distribution.

    A 429 burst is simulated by raising ``RateLimitError`` for the configured
    fraction of calls, with a Retry-After the controller must honour.
    """

    def __init__(
        self,
        delay_seconds: float = 3.0,
        text: str = _ANSWER,
        *,
        p50_seconds: float = 0.0,
        p95_seconds: float = 0.0,
        token_p50: int = 2048,
        token_p95: int = 2048,
        rate_limit_fraction: float = 0.0,
        rate_limit_retry_after: float = 1.0,
        seed: int = 20261108,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.p50_seconds = p50_seconds
        self.p95_seconds = p95_seconds
        self.token_p50 = token_p50
        self.token_p95 = max(token_p50, token_p95)
        self.rate_limit_fraction = rate_limit_fraction
        self.rate_limit_retry_after = rate_limit_retry_after
        self.text = text
        self.calls = 0
        self.rate_limited = 0
        self._rng = random.Random(seed)
        self._lock = threading.Lock()

    def _delay(self) -> float:
        with self._lock:
            if self.p50_seconds > 0:
                return _sample_lognormal(self._rng, self.p50_seconds, self.p95_seconds)
            return self.delay_seconds

    def _result(self, request):
        from app.llm.base import GenerationResult, Usage
        from tests.fakes import research_context, research_step

        with self._lock:
            self.calls += 1
            total = round(_sample_lognormal(
                self._rng, float(self.token_p50), float(self.token_p95)
            ))
        total = max(2, total)
        completion = max(1, min(total // 2, 256))
        usage = Usage(prompt_tokens=total - completion, completion_tokens=completion,
                      source="provider")
        context = research_context(request)
        if context is not None:
            return GenerationResult(tool_calls=(research_step(context, self.text, self.calls,
                                                              request.tools),),
                                    finish_reason="tool_calls", usage=usage)
        return GenerationResult(text=self.text, finish_reason="stop", usage=usage)

    def _maybe_rate_limit(self) -> None:
        if self.rate_limit_fraction <= 0:
            return
        with self._lock:
            limited = self._rng.random() < self.rate_limit_fraction
            if limited:
                self.rate_limited += 1
        if limited:
            from app.llm.base import RateLimitError

            raise RateLimitError(
                "fake provider rate limit", provider="load-test",
                retry_after=self.rate_limit_retry_after,
            )

    def generate(self, request):
        self._maybe_rate_limit()
        time.sleep(self._delay())
        return self._result(request)

    def chat(self, messages, *, max_new_tokens: int | None = None) -> str:
        time.sleep(self.delay_seconds)
        return self.text


class GatedProvider:
    """Apply the per-provider concurrency cap (LLM_MAX_CONCURRENCY) around the fake provider."""

    def __init__(self, provider, limit: int):
        from app.llm.base import ProviderGate

        self.provider = provider
        self.gate = ProviderGate(limit, provider="load-test")

    @property
    def calls(self) -> int:
        return getattr(self.provider, "calls", 0)

    @property
    def rate_limited(self) -> int:
        return getattr(self.provider, "rate_limited", 0)

    def generate(self, request):
        with self.gate.acquire(request) as active:
            return self.provider.generate(active)


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

    pgdata = root / "pgserver"
    if getattr(args, "max_connections", 0) > 0 and not (pgdata / "PG_VERSION").exists():
        from pgserver._commands import initdb

        pgdata.mkdir(parents=True, exist_ok=True)
        initdb(["-U", "postgres", "--auth=trust", "-E", "UTF8"], pgdata=pgdata)
        with (pgdata / "postgresql.conf").open("a", encoding="utf-8") as config:
            config.write(f"\nmax_connections = {args.max_connections}\n")
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
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


def _seed(dsn: str, name: str, *, disposable: bool, documents: int,
          chunks_per_document: int,
          embedder: LatencyEmbedder, config, model: str, root: Path) -> tuple[UUID, int]:
    """Create a collection of synthetic text once; return its id and chunk count."""
    from app.db import queries
    from app.db.connection import connect
    from app.db.migrate import apply_migrations
    from app.ingest.pipeline import ingest_file

    conn = connect(dsn)
    try:
        apply_migrations(conn)
        if disposable:
            # The harness measures capacity, not the developer policy seeds. Never
            # clear the policies of a database the caller passed in.
            conn.execute(queries.get("schema_clear_fresh_quota_policies"))
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
                count_tokens=embedder.chunk_tokens, embedder=embedder,
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
             extra: str = "", waits: list[float] | None = None) -> None:
    print(f"\n{label}")
    if extra:
        print(f"  {extra}")
    print("  status: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    if latencies:
        print(f"  service ms: p50={_percentile(latencies, 0.50) * 1000:.0f} "
              f"p95={_percentile(latencies, 0.95) * 1000:.0f} "
              f"p99={_percentile(latencies, 0.99) * 1000:.0f} "
              f"max={max(latencies) * 1000:.0f} mean={statistics.mean(latencies) * 1000:.0f}")
    if waits:
        print(f"  queue wait ms: p50={_percentile(waits, 0.50) * 1000:.0f} "
              f"p95={_percentile(waits, 0.95) * 1000:.0f} "
              f"p99={_percentile(waits, 0.99) * 1000:.0f} "
              f"max={max(waits) * 1000:.0f}")
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


async def _run_arrivals(app, collection: str, rate: float, duration: float,
                        max_inflight: int, timeout: float
                        ) -> tuple[Counter, list[float], float, str, list[float]]:
    """Open-loop scheduled arrivals: measure queue wait separately from service."""
    statuses: Counter = Counter()
    details: Counter = Counter()
    latencies: list[float] = []
    waits: list[float] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost",
                                 timeout=timeout) as client:
        gate = asyncio.Semaphore(max_inflight)
        tasks = []

        async def one(index: int, scheduled: float) -> None:
            async with gate:
                started = time.perf_counter()
                waits.append(started - scheduled)
                try:
                    response = await client.post(
                        "/api/query",
                        json={"query": QUERIES[index % len(QUERIES)], "collectionId": collection},
                    )
                    statuses[str(response.status_code)] += 1
                    if response.status_code >= 400:
                        try:
                            details[str(response.json().get("detail"))[:80]] += 1
                        except Exception:  # noqa: BLE001 - diagnostics only
                            details[response.text[:80]] += 1
                except Exception as exc:  # noqa: BLE001 - a failed request is a datum
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append(time.perf_counter() - started)

        started = time.perf_counter()
        total = max(1, int(rate * duration))
        for index in range(total):
            scheduled = started + index / rate
            delay = scheduled - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(index, scheduled)))
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started
    extra = f"rate={rate}/s duration={duration:.0f}s inflight<={max_inflight}"
    if details:
        extra += " errors=" + ", ".join(f"{key}×{value}" for key, value in details.items())
    return statuses, latencies, elapsed, extra, waits


async def _warmup(app, timeout: float, headers: dict[str, str] | None = None) -> None:
    """Run one request so per-process schema bootstrap is not measured as load."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost",
                                 timeout=timeout) as client:
        await client.get("/api/collections", headers=headers or {})


async def _run_queries(app, collection: str, requests: int, concurrency: int,
                       timeout: float) -> tuple[Counter, list[float], float, str]:
    statuses: Counter = Counter()
    details: Counter = Counter()
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
                    if response.status_code >= 400:
                        try:
                            details[str(response.json().get("detail"))[:80]] += 1
                        except Exception:  # noqa: BLE001 - diagnostics only
                            details[response.text[:80]] += 1
                except Exception as exc:  # noqa: BLE001 - a failed request is a datum
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append(time.perf_counter() - start)

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(requests)))
        elapsed = time.perf_counter() - started
    extra = f"concurrency={concurrency}"
    if details:
        extra += " errors=" + ", ".join(f"{key}×{value}" for key, value in details.items())
    return statuses, latencies, elapsed, extra


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
                             timeout: float, poll_seconds: float, auth_provider
                             ) -> tuple[Counter, list[float], list[float], float, str]:
    statuses: Counter = Counter()
    details: Counter = Counter()
    latencies: list[float] = []
    first_progress: list[float] = []
    polls = 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost",
                                 timeout=timeout) as client:
        gate = asyncio.Semaphore(concurrency)

        async def one(index: int) -> None:
            nonlocal polls
            from app.auth.service import COOKIE_NAME

            token = auth_provider.issue(f"load-workspace-{index}")
            headers = {
                "Cookie": f"{COOKIE_NAME}={token}",
                "Origin": "http://localhost",
            }
            async with gate:
                start = time.perf_counter()
                try:
                    created = await client.post(
                        "/api/conversations", json={"title": f"load {index}"}, headers=headers,
                    )
                    conversation = created.json()["conversationId"]
                    request_id = str(uuid4())
                    started = await client.post(
                        f"/api/conversations/{conversation}/turns/stream",
                        json={"query": QUERIES[index % len(QUERIES)],
                              "collectionId": collection, "requestId": request_id,
                              "expectedSequence": 0},
                        headers=headers,
                    )
                    started.raise_for_status()
                    after = 0
                    first_seen = False
                    while True:
                        events = await client.get(
                            f"/api/conversations/{conversation}/turns/{request_id}/events",
                            params={"afterEventId": after}, headers=headers,
                        )
                        events.raise_for_status()
                        polls += 1
                        body = events.json()
                        if not first_seen and any(
                            event.get("type") == "stage"
                            for event in body.get("events", [])
                        ):
                            first_seen = True
                            first_progress.append(time.perf_counter() - start)
                        after = body.get("nextEventId", after)
                        if body.get("done"):
                            statuses[f"{body.get('requestState')}/{body.get('jobState')}"] += 1
                            break
                        await asyncio.sleep(poll_seconds)
                except httpx.HTTPStatusError as exc:
                    statuses[f"http_{exc.response.status_code}"] += 1
                    try:
                        details[str(exc.response.json().get("detail"))[:120]] += 1
                    except Exception:  # noqa: BLE001 - diagnostics only
                        details[exc.response.text[:120]] += 1
                except Exception as exc:  # noqa: BLE001 - a failed request is a datum
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append(time.perf_counter() - start)

        started_at = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(requests)))
        elapsed = time.perf_counter() - started_at
    extra = (
        f"concurrency={concurrency} polls={polls} pollInterval={poll_seconds * 1000:.0f}ms"
    )
    if details:
        extra += " errors=" + ", ".join(f"{key}×{value}" for key, value in details.items())
    return statuses, latencies, first_progress, elapsed, extra


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("query", "conversation"), default="query")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--arrival-rate", type=float, default=0,
                        help="query scenario: open-loop arrivals per second (0 = closed loop)")
    parser.add_argument("--duration", type=float, default=30,
                        help="query scenario: seconds to offer --arrival-rate")
    parser.add_argument("--max-inflight", type=int, default=0,
                        help="query scenario: execution cap (default max(10, --concurrency))")
    parser.add_argument("--workers", type=int, default=1,
                        help="conversation worker threads (one job at a time each)")
    parser.add_argument("--documents", type=int, default=40)
    parser.add_argument("--chunks-per-document", type=int, default=25)
    parser.add_argument("--provider-latency-ms", type=int, default=3000)
    parser.add_argument("--provider-p50-ms", type=int, default=0,
                        help="sample call latency from this median (0 uses fixed latency)")
    parser.add_argument("--provider-p95-ms", type=int, default=0,
                        help="95th percentile paired with --provider-p50-ms")
    parser.add_argument("--token-p50", type=int, default=2048,
                        help="sampled tokens per call median")
    parser.add_argument("--token-p95", type=int, default=2048,
                        help="sampled tokens per call 95th percentile")
    parser.add_argument("--rate-limit-fraction", type=float, default=0.0,
                        help="fraction of provider calls that get a 429 burst")
    parser.add_argument("--rate-limit-retry-after", type=float, default=1.0)
    parser.add_argument("--max-concurrency", type=int, default=8,
                        help="per-provider call cap, as LLM_MAX_CONCURRENCY")
    parser.add_argument("--embed-latency-ms", type=int, default=30)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--pool-size", type=int, default=20)
    parser.add_argument("--max-connections", type=int, default=0,
                        help="disposable server max_connections (0 keeps the server default)")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--keep-data", action="store_true",
                        help="keep the per-run data directory (raw uploads, embedded database)")
    parser.add_argument("--sample-pools", action="store_true",
                        help="print pool occupancy every second during the run")
    parser.add_argument("--explain", action="store_true",
                        help="print EXPLAIN (ANALYZE, BUFFERS) for the vector seam")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.workers < 0:
        parser.error("requests and concurrency must be positive; workers cannot be negative")
    if args.scenario == "conversation" and args.workers < 1:
        parser.error("the conversation scenario needs at least one worker")
    if args.arrival_rate < 0 or args.duration <= 0 or args.max_inflight < 0:
        parser.error("arrival rate, duration and max in-flight must be positive")

    dsn, root, server = _dsn_and_root(args)
    try:
        from app.api import create_app
        from app.db.bootstrap import prepare_database
        from app.db.connection import connect

        settings = replace(
            load_settings(), database_url=dsn, data_dir=root,
            db_pool_max_size=args.pool_size, auth_mode="development",
            http_threadpool_tokens=max(64, args.concurrency),
            llm_max_concurrency=args.max_concurrency,
            artifact_operation_max_calls=4,
            artifact_operation_token_budget=max(16_000, args.token_p95 * 4),
            # The harness measures app capacity, not a developer's quota policy.
            quota_queries_per_minute=0, quota_queries_per_day=0,
            quota_tokens_per_day=0, quota_spend_usd_per_day=0.0,
            quota_max_active_jobs=0,
        )
        config = _lenient_config(settings)
        embedder = LatencyEmbedder(delay_seconds=args.embed_latency_ms / 1000)
        provider = GatedProvider(
            LatencyProvider(
                delay_seconds=args.provider_latency_ms / 1000,
                p50_seconds=args.provider_p50_ms / 1000,
                p95_seconds=args.provider_p95_ms / 1000,
                token_p50=args.token_p50,
                token_p95=args.token_p95,
                rate_limit_fraction=args.rate_limit_fraction,
                rate_limit_retry_after=args.rate_limit_retry_after,
            ),
            args.max_concurrency,
        )
        collection = args.collection or f"loadtest-{uuid4().hex[:8]}"

        bootstrap = connect(dsn)
        try:
            prepare_database(bootstrap, collection)
        finally:
            bootstrap.close()
        collection_id, chunks = _seed(
            dsn, collection, disposable=not args.database_url, documents=args.documents,
            chunks_per_document=args.chunks_per_document, embedder=embedder,
            config=config, model=settings.embedding_model_id, root=root,
        )
        print(f"database={'external' if args.database_url else 'disposable'} "
              f"collection={collection} chunks={chunks} "
              f"poolSize={settings.db_pool_max_size}")
        if args.explain:
            from app.db import queries as named_queries

            explain_conn = connect(dsn)
            try:
                plan = explain_conn.execute(
                    "EXPLAIN (ANALYZE, BUFFERS) " + named_queries.get("embed_search"),
                    {
                        "q": embedder.embed_query(QUERIES[0]),
                        "source_ids": None, "workspace_key": "development",
                        "model": settings.embedding_model_id, "dim": embedder.dim,
                        "collection_id": collection_id,
                        "limit": config.seams.embed.limit,
                        "min_similarity": config.seams.embed.min_similarity,
                    },
                ).fetchall()
            finally:
                explain_conn.close()
            print("vector plan:")
            for row in plan:
                print("  " + row["QUERY PLAN"])

        from app.auth.fake import FakeAuthProvider

        auth_provider = (
            FakeAuthProvider(redirect_uri="http://localhost/api/auth/callback")
            if args.scenario == "conversation" else None
        )
        if auth_provider is not None:
            # The fake provider stands in for every student's evaluated
            # connection; this harness measures application capacity, not
            # profile routing. Each turn still gets its own workspace.
            from app.api import query_service as query_api

            query_api._select_connection = lambda *args, **kwargs: None
        app = create_app(settings=settings, config=config, embedder=embedder,
                         provider=provider, auth_provider=auth_provider)
        warm_headers = {}
        if auth_provider is not None:
            from app.auth.service import COOKIE_NAME

            warm_headers = {
                "Cookie": f"{COOKIE_NAME}={auth_provider.issue('load-warmup')}",
                "Origin": "http://localhost",
            }
        asyncio.run(_warmup(app, args.timeout_seconds, warm_headers))
        stop = threading.Event()
        sampler = None
        if args.sample_pools:
            from app.db import queries
            from app.db.connection import borrow_diagnostics, connect, pool_stats

            def connection_states() -> str:
                conn = connect(dsn)
                try:
                    rows = conn.execute(queries.get("diagnostic_connection_states")).fetchall()
                    return ",".join(f"{row['state']}={row['connections']}" for row in rows)
                finally:
                    conn.close()

            def sample_pools() -> None:
                while not stop.wait(1.0):
                    snapshots = pool_stats()
                    print("  pool sample: " + ", ".join(
                        f"{item['pool']}={item['size']}/{item['maxSize']}"
                        f"(free {item['available']}, wait {item['waiting']})"
                        for item in snapshots)
                        + f" borrows={borrow_diagnostics()} pg={connection_states()}")

            sampler = threading.Thread(target=sample_pools, daemon=True)
            sampler.start()
        workers = []
        waits = None
        try:
            if args.scenario == "conversation":
                workers = _start_workers(settings, config, embedder, provider,
                                         args.workers, stop)
                statuses, latencies, first_progress, elapsed, extra = asyncio.run(
                    _run_conversations(app, collection, args.requests, args.concurrency,
                                       args.timeout_seconds, args.poll_ms / 1000,
                                       auth_provider))
            elif args.arrival_rate > 0:
                statuses, latencies, elapsed, extra, waits = asyncio.run(
                    _run_arrivals(app, collection, args.arrival_rate, args.duration,
                                  args.max_inflight or max(10, args.concurrency),
                                  args.timeout_seconds))
            else:
                statuses, latencies, elapsed, extra = asyncio.run(
                    _run_queries(app, collection, args.requests, args.concurrency,
                                 args.timeout_seconds))
        finally:
            stop.set()
            for worker in workers:
                worker.join(timeout=5)
            if sampler is not None:
                sampler.join(timeout=2)

        from app.db.connection import close_pools

        _summary(args.scenario, statuses, latencies, elapsed, extra=extra, waits=waits)
        if args.scenario == "conversation":
            if first_progress:
                print(f"  first progress ms: p50={_percentile(first_progress, 0.50) * 1000:.0f} "
                      f"p95={_percentile(first_progress, 0.95) * 1000:.0f} "
                      f"p99={_percentile(first_progress, 0.99) * 1000:.0f}")
            if provider.rate_limited:
                print(f"  provider 429s: {provider.rate_limited}")
            if any(status.startswith(("failed", "pending")) for status in statuses):
                from app.db.connection import connect

                debug = connect(dsn)
                try:
                    rows = debug.execute(
                        "SELECT status, error FROM jobs WHERE status = 'failed' "
                        "ORDER BY finished_at DESC LIMIT 5"
                    ).fetchall()
                finally:
                    debug.close()
                for row in rows:
                    print(f"  failed job: {row['error']}")
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
