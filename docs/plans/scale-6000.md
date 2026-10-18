# Scaling to the full student body (6,000 students): assessment and handoff

Status: requested plan (2026-10-18). Phase 0 is implemented: the repeatable
concurrency harness is `backend/scripts/load_test.py`, and the numbers below come
from it. No production behavior has been changed. `docs/architecture.md` remains
the design contract; this file sequences the scaling work.

## 1. Outcome

Serve the whole student body — about 6,000 students, with a realistic peak of a
few hundred concurrent sessions — without 5xx responses, unbounded queue growth,
or data loss, and leave enough headroom for private uploads to grow the corpus
by an order of magnitude.

"Six thousand students" is a population, not a concurrency target. The numbers
that matter are peak concurrent sessions, answer generation rate, and tokens per
day. Section 2 states the planning assumptions; Section 8 replaces them with
telemetry.

## 2. Demand model (assumptions, to be replaced by measurement)

| Quantity | Planning value | Basis |
| --- | ---: | --- |
| Registered students | 6,000 | requirement |
| Peak concurrent sessions | 600 (10%) | registration/advising-season assumption |
| Questions per active session | ~1 per minute at peak | assumption |
| Peak answer rate | ~10 questions/s | derived |
| Concurrent generations at 4 s provider latency | ~40 | derived (Little's law) |
| Tokens per answer | ~4,000 in + ~300 out | measured prompt budgets, provider-dependent |
| Tokens per day | ~250M at 10 questions/student/day | derived |

The last row is the cost lever that matters more than node count: price it
against the selected provider and set operation budgets accordingly. The system
already records provider usage per call (`model_calls`, `operations`), which is
the input to that estimate; it is not currently aggregated across students.

## 3. Measured baseline (2026-10-18)

All runs: in-process ASGI, disposable embedded Postgres, deterministic fake
embedder (768-d, 30 ms/query) and fake provider with a fixed latency. No model
weights, no network. Absolute latencies are therefore optimistic (shared GIL,
embedded Postgres); the shape of the curves — pool saturation, 503 onset,
worker serialization — is representative.

Reproduce from `backend/`:

```bash
../.venv/Scripts/python -m scripts.load_test \
  --scenario query --requests 60 --concurrency 60 --documents 20 \
  --chunks-per-document 50 --provider-latency-ms 5000
```

Add `--database-url postgresql://...` to measure a production-like server instead
of the disposable embedded database. The harness always seeds a fresh synthetic
collection (`--collection` reuses a named one), so it never reads a development
corpus.

| Run | Corpus | Concurrency | Provider | Result |
| --- | ---: | ---: | ---: | --- |
| Query, cold scan | 1,820 chunks | 1 | 0 ms | p50 73 ms (≈43 ms retrieval) |
| Query, growth | 18,160 chunks | 1 | 0 ms | p50 130 ms (≈100 ms retrieval) |
| Query, normal | 1,086 chunks | 20 | 1,000 ms | 17.3 req/s, p50 1.06 s, 0 errors, pool 20/20 |
| Query, saturated | 546 chunks | 60 | 5,000 ms | 20 × 200, **40 × 503**, p50 10.3 s |
| Conversation, 1 worker | 546 chunks | 16 | 1,000 ms | **0.90 turns/s**, p50 10.3 s |
| Conversation, 4 workers | 546 chunks | 16 | 1,000 ms | **3.29 turns/s**, p50 3.7 s |

Two conclusions follow directly:

1. **The vector scan grows linearly.** +16.3k chunks added ~57 ms per query on
   the embedded server. At the ~300k private chunks that 6,000 students could
   create, the same curve predicts roughly 1 s per query before concurrency
   contention. It is already the largest fixed cost in a no-provider request.
2. **Both concurrency ceilings are real.** A single API process refuses 40 of 60
   simultaneous answers as `503 database is busy` because a pooled connection is
   held for the whole model call. A single worker process serializes turns at
   ~0.9/s; four workers scale almost linearly, which confirms that process/thread
   count — not the queue — is the throughput knob.

## 4. Findings, ranked by consequence

### F1. A request holds a pooled connection across provider I/O

Evidence: `backend/app/api/deps.py:37-58` borrows for the entire handler;
`backend/app/api/query_service.py:321-380` commits before retrieval but keeps the
borrow through generation. Effective answer concurrency is `DB_POOL_MAX_SIZE`
(default 20) per process, not the 40-thread handler ceiling (`deps.py` +
AnyIO's default limiter). Measured: 40/60 requests returned 503 at concurrency 60.

Change: return the borrow before the provider call and re-borrow for
publication. The async conversation path already avoids this (the API only
inserts a job); the synchronous paths (`/api/query`, `/api/conversations/{id}/turns`,
decisions) still hold it.

Acceptance: at 60 concurrent answers with a 5 s provider, zero 503s, and pool
`waiting` stays 0.

### F2. The worker runs one turn at a time per process

Evidence: `backend/app/jobs/worker.py:71-137` claims and executes exactly one
job; `backend/scripts/worker.py:115-125` loops serially. Each worker also builds
its own embedder and up to three pool roles. Measured: 1 worker = 0.90 turns/s,
4 workers = 3.29 turns/s.

Change: `--concurrency N` in the worker with N claim/execute threads sharing one
embedder and provider, each with its own `conversation_execution` borrow. The
job queue already supports this (`FOR UPDATE SKIP LOCKED`). If CPU embedding
becomes the limit, move query embedding behind a small dedicated service.

Acceptance: one worker process sustains at least 4 concurrent turns; throughput
scales linearly until the embedder saturates; killing a worker mid-turn does not
duplicate or lose a publication.

### F3. Vector search scans every embedding, for every workspace

Evidence: `backend/app/db/queries/embeddings.sql:8-25` has no ANN index and no
collection/workspace predicate on `chunk_embeddings`; filters are applied on the
joined `sources` row after distance is computed. `docs/architecture.md:366`
records the current position ("exact scan is fine at this size"). Measured
~3.5 µs/chunk growth.

This is the finding that changes with private uploads: every student's query
scans every other student's private chunks and discards them afterwards. Add an
ANN index *and* scope the scan before the corpus grows.

Schema decision required: `chunk_embeddings.embedding` is an untyped `vector`
(`baseline/032_schema.sql:349-356`), so pgvector cannot index it directly. The
options are (a) fix the product dimension to `vector(768)` and update the
test fakes from 8-d to 768-d, or (b) keep per-dimension storage and add a typed
generated column plus a partial HNSW index (`vector_ip_ops`, `WHERE dim = 768`).
Option (b) preserves the "dimension is per row" design but needs a planner
check: a parameterized `dim = %(dim)s` predicate is not always provably equal to
the partial-index predicate. Do not add the index without an `EXPLAIN` proving
the index scan is chosen.

Acceptance: at 300k chunks, the retrieval portion of a query stays under 50 ms
at concurrency 20, and `EXPLAIN (ANALYZE, BUFFERS)` shows the ANN index, not a
sequential scan.

### F4. Files are per-host and the deployment is a dev stack

Evidence: artifact originals are written under `DATA_DIR` and their absolute
paths are stored in the database (`backend/app/artifacts/storage.py:146-176,259-274`);
raw uploads do the same (`backend/app/ingest/pipeline.py:64-75`). `compose.yaml`
runs the Vite dev server, and `backend/Dockerfile:23` starts uvicorn with
`--reload` and one process.

Change: a shared object store (S3/MinIO) or a shared POSIX volume mounted at the
same path on every replica; a built frontend behind nginx/CDN; no `--reload`;
`--workers` (or gunicorn) from configuration; non-root; health checks.

Acceptance: upload on replica A, download on replica B; a rolling restart loses
no artifact; the frontend is served as static assets, not by Vite.

### F5. Connection budget is unbounded and unmonitored

Worst case per process: API = `default` + `tool_execution`; worker = `default` +
`conversation_execution` + `tool_execution`. At 20 each:

| Topology | Worst-case connections |
| --- | ---: |
| 4 API + 4 workers | 4×40 + 4×60 = 400 |
| 4 API + 8 workers | 160 + 480 = 640 |

Managed Postgres defaults are far below that. Choose one:

- Set pool sizes from replica count so the total stays under ~70% of
  `max_connections`, or
- Put PgBouncer in transaction mode in front, and set `prepare_threshold=None`
  on psycopg connections (psycopg3's server-side prepared statements are not
  compatible with transaction pooling).

Also add `statement_timeout` and `idle_in_transaction_session_timeout` to pooled
connections (`backend/app/db/connection.py:110-114` currently sets neither), so
one bad plan cannot pin a slot indefinitely.

### F6. No admission control, no session cache, no production telemetry

- No rate limit or per-workspace cap on outstanding jobs; `POST /api/artifact-batches`
  and stream starts always enqueue.
- OIDC opens a second DB connection per request (`backend/app/auth/service.py:267-273`);
  a 30–60 s session cache halves auth round trips.
- `GET /ready` is cached for 5 s per process (`backend/app/ops/readiness.py`);
  there is no `/metrics`, no pool saturation metric, and no queue-depth alert.
- The polling client is fixed at 750 ms per open turn (`frontend/src/components/chat/useSavedChat.ts:129`).
  This is acceptable at tens of concurrent turns; instrument it before assuming
  it stays cheap at hundreds.

## 5. Target architecture (decision deferred to measurements)

Both options assume: managed Postgres with backups/PITR, object storage,
authenticated provider connections, and a reverse proxy that terminates TLS.

**A. A few stateless containers + managed Postgres (default recommendation).**
2–4 API replicas, 2–8 worker replicas, PgBouncer optional. Simplest to operate
and sufficient if the harness shows the target p95 is met by 4+4.

**B. Kubernetes with horizontal autoscaling.** Justified only if demand is
bursty enough that fixed replicas waste capacity or cannot absorb registration
day. Adds PgBouncer, HPA, and pod-level storage concerns.

Decision thresholds after Phase 2:

- Peak sustained answer rate ≤ 25/s and p95 ≤ 15 s at 2× expected concurrency
  with option A → stay with A.
- Otherwise, or if the provider becomes the bottleneck, revisit B and the
  provider/embedding topology.

## 6. Phased plan

### Phase 0 — measurement (done)

`backend/scripts/load_test.py`: query and conversation scenarios, synthetic
corpus, fake embedder/provider, pool snapshots, percentile and error reporting.
Not in the test suite; no models, no network.

### Phase 1 — database and connection hygiene

1. Decide the vector-schema option in F3; add migration 041 with the HNSW index
   and rewrite `embeddings.sql` to the index-compatible `ORDER BY` form.
   Verify with `EXPLAIN (ANALYZE, BUFFERS)` at 300k chunks and re-run retrieval
   evals (`python -m scripts.eval_retrieval`) before and after.
2. Scope the vector scan to the collection/workspace *before* ordering, or
   accept HNSW filter recall loss and document it.
3. Add `statement_timeout` / `idle_in_transaction_session_timeout`, configurable
   in `config.py`, applied to pooled connections.
4. Publish a connection-budget formula in `docs/architecture.md` and enforce it
   at startup (refuse a pool configuration whose worst case exceeds the
   configured database limit).

### Phase 2 — concurrency

1. Release request borrows across provider I/O (F1). Keep the test
   `override_conn` seam working.
2. Add `--concurrency` to the worker (F2) and measure embedder thread safety and
   CPU saturation.
3. Add a short-TTL OIDC session cache, or reuse the request borrow for the
   session lookup.

### Phase 3 — storage and deployment

1. Object storage or shared volume for artifacts and raw uploads; migrate
   existing local paths with a checked, resumable backfill.
2. Production image and compose/k8s manifests: built frontend, no `--reload`,
   real worker count, non-root, health checks, TLS termination.
3. PgBouncer if the Phase 1 budget requires it, with `prepare_threshold=None`.
4. Backups, a restore drill, and rollback instructions.

### Phase 4 — safety and observability

1. Per-workspace outstanding-job caps and a queue-depth limit; return a clear
   `429` rather than growing the queue without bound.
2. Provider spend caps aggregated per student/day, not only per operation.
3. `/metrics` for request latency, pool saturation, queue depth, and provider
   usage; alert on pool `waiting > 0` and queue age.
4. Retention for traces, job events, and artifacts.

## 7. Release acceptance for "scales to 6k"

The claim is only made when all of these are demonstrated with the harness or a
production-like environment:

1. **80 concurrent turns** (2× the planning peak) with a 4 s provider: zero 5xx,
   p95 end-to-end ≤ 15 s, pool `waiting` 0, DB connections < 70% of the limit.
2. **Non-generation routes** at 50 req/s: p95 ≤ 300 ms.
3. **300k-chunk corpus**: retrieval p95 < 50 ms at concurrency 20, ANN index
   confirmed by `EXPLAIN`.
4. **Cross-replica artifacts**: upload on A, download on B; rolling restart loses
   nothing.
5. **Failure**: kill a worker mid-turn; the lease recovery marks the turn failed
   (or a retry publishes once), never twice.
6. **Restore**: a backup restores into a fresh database and passes `/ready`.
7. **Cost**: measured tokens/day and a provider bill estimate with a stated
   budget cap.

## 8. Open questions

- Real peak concurrency and questions/student/day. Instrument before sizing;
  the harness's demand model is an assumption.
- Which identity provider, and whether sessions can be cached safely under the
  school's revocation expectations.
- Provider quota, data-processing terms, and whether student questions or
  uploads may leave the network.
- Retention policy for private uploads and generated artifacts (FERPA review).
- Whether private uploads are in scope for the first release. If not, F3 is a
  much smaller problem and the 300k-chunk target can be deferred.

## 9. Non-goals

- Multi-region or active-active deployment.
- A new retrieval algorithm or answer-quality work; this plan changes capacity,
  not ranking quality.
- Reworking the job queue. `FOR UPDATE SKIP LOCKED`, leases, and one-attempt
  conversation jobs are already the right shape.
