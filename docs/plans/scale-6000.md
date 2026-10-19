# Scaling to the full student body (6,000 students): assessment and handoff

Status: revised after review (2026-10-18). The initial concurrency harness is
available in `backend/scripts/load_test.py`; Phase 0 is only partly complete.
Worker `--concurrency` and pooled database timeouts exist in the current working
tree, but their production capacity and failure behavior remain unverified.
The baseline below is historical evidence from the initial harness, not a fresh
benchmark of every current change. This revision changes documentation only.
[The architecture](../architecture.md#capacity-and-student-rollout-target)
remains the design contract; this file sequences the scaling work.

### Implementation progress (2026-10-18, uncommitted working tree)

Code-level capacity items are implemented and tested; deployment, provider
quota, retention and the 300k scoped-retrieval fixture remain gated on external
decisions.

Implemented:

- **F1:** pooled borrows are released around provider I/O in the synchronous
  query, conversation worker, tools and research paths. The handle reacquires
  lazily, so accounting, fences and publication still use the same call sites.
- **F5:** pooled connections set `DB_STATEMENT_TIMEOUT_MS` and
  `DB_IDLE_TRANSACTION_TIMEOUT_MS`; `HTTP_THREADPOOL_TOKENS` sizes the AnyIO pool.
  Both matter: at 60 concurrent answers the 40-token default starved handlers
  that held borrows, independent of the provider call.
- **F6 (partial):** loopback `/metrics` (behind `ADMIN_API_ENABLED`) reports pool
  occupancy, queue depth/age and borrow counters. Opt-in atomic admission caps
  cover conversation stream starts and artifact run creation
  (`ADMISSION_MAX_PENDING_WORKSPACE/GLOBAL`); artifact batches, uploads and
  sandbox work are not yet capped.
- The stream-events endpoint reads request and job state in one statement,
  removing the `pending`/`succeeded` snapshot race.
- The harness now warms the process, offers open-loop scheduled arrivals, samples
  pools and `pg_stat_activity` during a run, prints the vector `EXPLAIN` plan,
  and reports release/reacquire counts.

Measured on this host (in-process ASGI, embedded Postgres, fake seams):

- 60 simultaneous answers with a 5 s provider: **60/60, zero 503s, p50 5.6 s**,
  pool 15/20 idle while generating. Before F1 the same run produced 20 successes
  and 40 `database is busy` responses.
- 18,160 chunks: the vector seam scans every matching embedding
  (`chunk_embeddings_model_idx`, 9,155 joined rows, 221 ms execution). This is
  the F3 evidence; the 300k multi-workspace fixture and exact-vs-ANN recall
  comparison have not been run.
- Full backend suite: 1022 passed, 2 platform skips (symlinks and POSIX mode).

Not implemented or gated:

- 300k scoped retrieval fixture, ANN/index decision and recall gate (F3).
- Real HTTP/proxy, mock-OIDC multi-workspace load and production topology.
- Object storage for artifacts/raw files, deployment manifests, PITR/restore drill.
- Admission/spend coverage for artifact batches, uploads and sandboxes (run
  creation and conversation turns are capped).
- Retention and staged student rollout (Phase 4).

## 1. Outcome

Serve the whole student body — about 6,000 students, with a realistic peak of a
few hundred concurrent sessions — without 5xx responses, unbounded queue growth,
or data loss, and leave enough headroom for private uploads to grow the corpus
by an order of magnitude.

"Six thousand students" is a population, not a concurrency target. The numbers
that matter are peak concurrent sessions, answer generation rate, and tokens per
day. Section 2 states the planning assumptions; Phase 0 replaces them with
telemetry. A release claim must name its enabled features, workload mix, corpus,
provider and topology. A one-shot answer result does not establish research,
upload, artifact or sandbox capacity.

## 2. Demand model (assumptions, to be replaced by measurement)

| Quantity | Planning value | Basis |
| --- | ---: | --- |
| Registered students | 6,000 | requirement |
| Peak concurrent sessions | 600 (10%) | registration/advising-season assumption |
| Questions per active session | ~1 per minute at peak | assumption |
| Peak answer rate | ~10 questions/s | derived |
| Busy execution slots for a 4 s simple turn | ~40 at peak; ~80 at 2× peak | lower bound, before headroom and retrieval overhead |
| Tokens per simple answer | ~4,000 in + ~300 out | initial budget assumption; validate against provider usage |
| Provider calls at peak, one call per answer | 600/min | excludes planning, tools, repair and retries |
| Provider tokens at peak, one call per answer | 2.4M input/min + 180k output/min | derived from the simple-answer assumption |
| Tokens per day, simple answers only | ~258M at 10 questions/student/day | 60,000 × 4,300; not a research estimate |
| Poll traffic at 750 ms | ~53 requests/s for 40 pending turns; ~107/s for 80 | additional to submissions and other reads |

The last row is the cost lever that matters more than node count: price it
against the selected provider and set operation budgets accordingly. The system
already records provider usage per call (`model_calls`, `operations`), which is
the input to that estimate; it is not currently aggregated across students.

Size execution slots from `arrival rate × mean whole-turn service time`, then
add measured headroom. At exactly that many slots, utilization is already 100%.
Four-second provider latency is not whole-turn latency for a multi-call workflow.
The research path permits up to 12 provider calls per turn by default; this is a
ceiling, not a measured average. Measure calls, tokens, retrieval/tool time,
time to first useful output, and completion time separately by service class.
Provider quota must cover the chosen model/profile's calls, tokens and concurrent
requests, including bounded retries and the shared quota used by other features.

Before sizing, freeze a workload manifest in the release handoff: feature flags,
fractions of simple/planned/research turns, conversation history lengths,
upload sizes/rates, artifact/Office work, private-source distribution, provider
latency/error distributions, quotas, per-class SLOs, and replica/thread counts.
Exercise every enabled class alone and in the launch mix. Research needs its own
declared completion SLO; it cannot inherit the simple-turn 15 s target by accident.
If that SLO and capacity have not been demonstrated, keep research disabled for
the student rollout. Paid live calibration requires an agreed budget; simulated
latency alone cannot establish real provider quota or model throughput.

## 3. Measured baseline (2026-10-18)

All runs: in-process ASGI, disposable embedded Postgres, deterministic fake
embedder (768-d, 30 ms/query) and fake provider with a fixed latency. No model
weights, no network. These isolate some application limits, but fixed sleeps do
not model CPU embedding contention or real provider latency. They do not measure
TLS/proxy/network behavior, independent process pools, or real model capacity.
Authentication is development mode; planning, tools and research are disabled.
The synthetic corpus belongs to one collection and does not establish performance
or recall under many private workspaces.

Reproduce from `backend/`:

```bash
../.venv/Scripts/python -m scripts.load_test \
  --scenario query --requests 60 --concurrency 60 --documents 20 \
  --chunks-per-document 50 --provider-latency-ms 5000
```

Add `--database-url postgresql://...` to measure a dedicated test server instead
of the disposable embedded database. This still invokes the app in-process,
without a real HTTP deployment. External database runs apply migrations and
write data; never target the production or ordinary development database.
By default the harness seeds a fresh synthetic collection; `--collection` can
reuse an existing collection and therefore requires a deliberately chosen test
collection. Dedicated-test cleanup must preserve files referenced by retained
database rows.

| Run | Corpus | Concurrency | Provider | Result |
| --- | ---: | ---: | ---: | --- |
| Query, cold scan | 1,820 chunks | 1 | 0 ms | p50 73 ms (≈43 ms retrieval) |
| Query, growth | 18,160 chunks | 1 | 0 ms | p50 130 ms (≈100 ms retrieval) |
| Query, normal | 1,086 chunks | 20 | 1,000 ms | 17.3 req/s, p50 1.06 s, 0 errors, pool 20/20 |
| Query, saturated | 546 chunks | 60 | 5,000 ms | 20 × 200, **40 × 503**, p50 10.3 s |
| Conversation, 1 worker thread | 546 chunks | 16 | 1,000 ms | **0.90 turns/s**, p50 10.3 s |
| Conversation, 4 worker threads | 546 chunks | 16 | 1,000 ms | **3.29 turns/s**, p50 3.7 s |

Two conclusions follow directly:

1. **The measured retrieval cost increased with corpus size.** +16.3k chunks
   added ~57 ms per query on this fixture. A linear extrapolation to 300k chunks
   suggests roughly 1 s, but it is not a measurement of a filtered multi-tenant
   query. Verify actual join plans and candidate counts before choosing indexes.
2. **Both baseline concurrency ceilings are real.** A single API process refused 40 of 60
   simultaneous answers as `503 database is busy` because a pooled connection is
   held for the whole model call. A single worker process serializes turns at
   ~0.9/s; four threads improved this to 3.29/s with fake seams. All harness
   threads share one executor, embedder, provider and process-level pools. This
   is not evidence of four-replica scaling or production provider thread safety.

## 4. Findings, ranked by consequence

### F1. Requests and conversation executors hold connections across provider I/O

Evidence: `backend/app/api/deps.py:37-58` borrows for the entire handler;
`backend/app/api/query_service.py:321-380` commits before retrieval but keeps the
borrow through generation. Effective answer concurrency is `DB_POOL_MAX_SIZE`
(default 20) per process, not the 40-thread handler ceiling (`deps.py` +
AnyIO's default limiter). Measured: 40/60 requests returned 503 at concurrency 60.

Change: return borrows before provider I/O and re-borrow for bounded database
work, including budget reservation/settlement, audit writes, cancellation and
source checks, and publication. The stream-start API only inserts a job, but
`ConversationTurnExecutor.__call__` in `backend/app/conversations/worker.py`
retains a `conversation_execution` borrow around the whole `query_response`.
Cover that worker and research/tools/planning paths as well as synchronous
query, conversation and decision handlers. Committing a transaction does not
return a pool slot. Preserve source/profile revision fences, publication
atomicity, durable unknown usage, and the `override_conn` test seam.

Acceptance: 60 concurrent simple answers with a 5 s fake provider have zero
pool-related 503s. Sample all pool roles during the run: provider latency must
not determine borrow duration, and pool waits must not cause timeouts or sustained
queueing. A post-run `waiting = 0` snapshot cannot establish this property.

### F2. Worker concurrency exists; its safe capacity is unverified

`backend/scripts/worker.py` now has `--concurrency` (default 1, bounded 1–32).
`run_once` still executes one job per caller, and `FOR UPDATE SKIP LOCKED`
supports parallel claimers. The initial harness measured 0.90 turns/s with one
thread and 3.29/s with four, all inside one process. Production replica and
thread counts must be measured separately, including their separate model memory
and pool budgets.

Validate shared provider/embedder thread safety, lazy runtime initialization,
CPU saturation, and memory use. If safe serialization is required for a model,
measure that limit rather than assuming threads remove it. A dedicated embedding
service is an option only if measurements justify it. Ensure slow research,
artifact, ingest and Office work cannot occupy all interactive capacity; choose
bounded class concurrency or reserved capacity before adding queue infrastructure.

Size total execution slots explicitly: 32 slots with 4 s service time can finish
an 80-turn burst in roughly 12 s, yet sustain only 8 turns/s. Passing a finite
burst does not establish the 10 turns/s demand target. Include worker idle-poll
delay and control/heartbeat capacity when measuring queue wait.

Acceptance: validate the chosen replica × thread topology at the sustained and
mixed-load gates in Section 7. A normal termination stops claiming, drains within
a configured grace period, then leaves interrupted work recoverable. Worker loss,
duplicate delivery and cancellation must never cause duplicate publication.

### F3. Scoped vector performance and recall need a joint decision

`backend/app/db/queries/embeddings.sql` has no ANN index. It filters model and
dimension on `chunk_embeddings` and collection/current/workspace/source scope
through joined rows. SQL text alone does not prove that every private embedding
is scored before filtering: Postgres can reorder joins and predicates. Capture
`EXPLAIN (ANALYZE, BUFFERS)` for representative scopes and both warm and cold
cache states before asserting where the cost lies.

Benchmark a 300k-chunk corpus split across 6,000 workspaces plus the shared
advising corpus, including skewed large workspaces. Cover shared-only,
shared-plus-owned, owned-only, narrow source selections, explicit empty scope,
historical/replaced sources, and several embedding models/dimensions. Unauthorized
or stale candidates must not consume a scoped result limit, reach a model/tool,
or become evidence. An ANN change cannot weaken that contract.

Keep exact search for small eligible sets when it meets the latency target.
For broad searches compare scoped indexes/partitioning and HNSW with bounded
iterative scans and an exact fallback. Approximate filtering can leave too few
eligible results; documenting that loss alone is not acceptance. See the
[pgvector filtering guidance](https://github.com/pgvector/pgvector#filtering).

If ANN is warranted, preserve the per-model/per-dimension contract. The current
untyped `vector` can use a typed expression plus a partial index; a generated
column is not required. A generated column, if selected, must guard incompatible
dimensions. Fixing the entire product to `vector(768)` would be an explicit
architecture change, not merely a test-fake adjustment. Verify the exact cast,
partial predicate and distance `ORDER BY` under repeated/prepared executions.
See [mixed-dimension indexing](https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column).

Acceptance: retrieval p95 < 50 ms at concurrency 20 on the target fixture, with
embedding and total retrieval time reported separately. Exact scoped results
are the vector reference: mean recall@k ≥ 0.95 in each scope cohort, no empty ANN
result when the exact query has eligible hits, and no regression on the frozen
advising retrieval eval. Scope violations always fail. Explain the selected plan;
an ANN scan is not mandatory where exact search is faster. Record k and thresholds
before comparing results, and review underfilled results rather than hiding them
in a global average.

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

These figures exclude unpooled startup/migration connections and deployment
surge. The budget is `sum(process count × sum(active role pool maxima)) +
unpooled application connections`, evaluated at maximum replicas and rolling
restart overlap, not just the steady-state replica count. Keep application
connections under 70% of the actual database limit, reserving the remainder for
operations, maintenance and recovery. Choose one:

- Set pool sizes from replica count so the total stays under ~70% of
  `max_connections`, or
- Put PgBouncer in transaction mode in front and budget its backend connections
  separately from application client slots. Disabling prepared statements with
  `prepare_threshold=None` is the conservative option. Supported Psycopg,
  PgBouncer and libpq versions/configurations can retain prepared statements;
  verify the chosen combination rather than claiming blanket incompatibility.
  See [Psycopg's compatibility requirements](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html#using-prepared-statements-with-pgbouncer).

`DB_STATEMENT_TIMEOUT_MS` (30,000) and `DB_IDLE_TRANSACTION_TIMEOUT_MS` (60,000)
already configure pooled connections in the working tree. Verify their effective
server values and failure behavior, including behind PgBouncer; unpooled
connections currently use a different setup. Audit transactions reopened before
slow provider calls so the idle timeout is not used to mask connection ownership
bugs. Choose lock timeouts for publication/migrations and leave migrations enough
time for index builds. Validate the topology-wide budget in deployment preflight;
each process can validate only the configured topology and its own pools, not
discover the true replica count. Reserve the next unused migration number when
implementation begins; never assume 041 remains free or edit applied SQL.

### F6. No admission control, no session cache, no production telemetry

- No general atomic per-workspace/global outstanding-work cap covers stream
  starts, batches and other expensive entry points. Existing per-conversation
  guards do not bound one student's total work across conversations.
- OIDC performs a separate, short session lookup using the default pool
  (`backend/app/auth/service.py`, `_auth_connection_factory` in `api/__init__.py`).
  Measure its cost under authenticated polling; it is not necessarily a second
  simultaneously held connection. Reuse a compatible short request borrow or
  cache only if needed. A cache must define expiry, logout/revocation invalidation
  across replicas, and an accepted maximum revocation delay.
- `GET /ready` is cached for 5 s per process (`backend/app/ops/readiness.py`);
  process-local `pool_stats()` exists for diagnostics, but there is no production
  metrics pipeline or queue-depth/age alert.
- The polling client is fixed at 750 ms per open turn (`frontend/src/components/chat/useSavedChat.ts:129`).
  At 80 pending turns this is ~107 requests/s before other reads; at 600 it is
  ~800/s. Longer research turns increase the number of pollers. Instrument the
  real browser schedule, reconnects and synchronized bursts. If changing transport,
  verify proxy timeouts, reconnect cursors and browser behavior. Backoff/jitter
  must preserve prompt cancellation and eventual recovery of durable events.

Admission and metrics belong before concurrency expansion. Atomically reserve
workspace/global pending-work capacity and provider spend across replicas; count
batch fan-out, release reservations on all terminal paths, and charge repeated
request IDs only once. Bound upload bytes, extraction/indexing work, artifact
generation and sandbox concurrency as well as question counts. Return `429` with
`Retry-After` for capacity limits before expensive work, while keeping polling,
cancellation, login/logout and ordinary reads usable during overload. Preserve
bounded provider retries and unknown-outcome accounting; do not automatically
retry an ambiguous model call or replay a saved turn to manufacture throughput.

Track per-class arrival/completion/admission rates, queue depth and oldest age,
borrow duration/waits/timeouts by pool role, provider calls/tokens/429s/timeouts,
embedding/retrieval latency, CPU/RAM, and database I/O/storage growth. Sample during
the run rather than only at exit. Report successful answers, valid refusals,
failures and overload rejections separately; a fast refusal or `200` alone is not
proof of useful answer throughput. Keep raw student text, secrets and high-cardinality
student/conversation IDs out of metric labels and routine logs.

## 5. Target architecture (decision deferred to measurements)

Both options assume: managed Postgres with backups/PITR, object storage,
authenticated provider connections, and a reverse proxy that terminates TLS.

**A. A few stateless containers + managed Postgres (default recommendation).**
2–4 API replicas and 2–8 worker replicas are starting candidates, not a capacity
promise. Specify threads per worker and every uvicorn process: pools and model
memory multiply per process. Select the topology only after the sustained,
mixed-load and failure gates pass, with headroom inside provider and DB budgets.

**B. Kubernetes with horizontal autoscaling.** Justified only if demand is
bursty enough that fixed replicas waste capacity or cannot absorb registration
day and the team can operate it. Adds autoscaling, rollout and storage concerns;
PgBouncer is independent of the orchestrator choice. Scaling containers cannot
increase provider quota or correct serialized embedding/model execution.

Use Phase 2 measurements for a provisional topology choice; confirm it on the
Phase 3 deployment before release:

- Stay with A if it passes Section 7 at 10 turns/s sustained and 20 turns/s
  headroom, with the launch workload mix and declared per-class SLOs.
- If it fails, identify the limiting resource first. Adjust provider quota,
  embedding capacity, SQL/storage, admission policy or execution slots as measured.
  Consider B only for a demonstrated orchestration/autoscaling need; it is not
  the fallback for an external provider bottleneck.

## 6. Phased plan

### Phase 0 — representative measurement (partly complete)

1. Keep the existing in-process query/conversation harness as an isolation tool.
   It reports percentiles/statuses and a final pool snapshot; it does not yet
   satisfy the release workload or deployment requirements.
2. Add scheduled arrival-rate and duration modes. A semaphore-only closed loop
   slows new arrivals when the server slows and can hide overload. Measure from
   intended arrival through queue wait and final publication; record load-driver
   lag/missed arrivals and fail an underpowered driver. Bound client timeouts and
   conversation polling with an explicit overall deadline.
3. Add real HTTP target mode through the intended proxy to independent API/worker
   processes on production-like Linux, dedicated Postgres and shared storage.
   Use thousands of distinct mock-OIDC student sessions; cover login, expiry,
   logout and rejected cross-workspace reads. Do not use development auth as the
   release path. Keep service/model smoke and load checks outside pytest.
4. Exercise planning/tools/research through typed scripted fake responses with
   realistic latency tails, slow calls, rate limits and errors; run the actual
   approved embedder separately to measure CPU contention. Capture real provider
   calibration only under the agreed budget, then freeze the Section 2 manifest.
5. Sample queue/pool/resource metrics throughout runs. Distinguish queue wait,
   first useful output, generation and terminal-publication latency. Report
   p50/p95/p99 per service class and outcome; don't silently drop timed-out work.
   Print diagnostics in the terminal and put release evidence in the requested
   handoff/PR, not new saved test-output or status-report files.

### Phase 1 — admission, observability, database and connection hygiene

1. Implement atomic admission and aggregate spend reservations from F6, including
   all expensive entry points and batch children. Define fair per-workspace and
   service-class limits and a provider-wide concurrency/token gate.
2. Add metrics and actionable thresholds before increasing concurrency. Alert
   on queue age/SLO breaches, sustained pool waits/timeouts, lease renewal errors,
   provider quota/spend, DB storage and worker resource exhaustion. Name an owner
   and response/runbook for each release-critical alert; exercise delivery.
3. Release API and worker borrows across provider I/O (F1), preserving transaction,
   accounting and publication invariants. Validate the existing server timeouts
   rather than listing them as unimplemented.
4. Validate the connection budget at maximum replicas plus deployment surge.
   Configure PgBouncer only if needed, including prepared-statement/startup-option
   compatibility and separate client/backend budgets.
5. Measure scoped query plans and implement the retrieval choice from F3. Add a
   new migration only when required; plan index-build locks, memory, disk and
   concurrent ingest behavior. Compare exact/ANN recall and advising evals before
   accepting a latency improvement. Avoid model/dimension contract changes just
   to obtain an index.

### Phase 2 — safe concurrency and mixed workloads

1. Validate the existing worker `--concurrency` with actual shared runtimes,
   covering initialization races, cancellation and publication under contention.
   Choose measured threads × processes and per-role pool sizes.
2. Reserve interactive/control capacity against long research, artifact, ingest
   and Office jobs; cap simultaneous extraction, embedding and sandbox work.
   Exercise one large workspace alongside many small ones to verify fairness.
3. Measure authenticated polling at its derived request rate, with realistic
   history/event pages and reconnect bursts. Tune polling/backoff or transport
   only when justified. Preserve cursor correctness and no replay of saved turns.
4. Optimize session lookups only if measurements require it, with explicit
   cross-replica revocation/expiry behavior for any cache.
5. Exercise sustained arrivals, headroom, burst recovery and faults in the test
   topology; iterate with Phase 3 and rerun Section 7 on the actual deployment.
   More threads alone are not acceptance.

### Phase 3 — storage, deployment and recovery

1. Choose object storage or a shared volume for every durable raw/original file,
   artifact import and download. Migrate local paths with a checksummed, resumable
   backfill and verify existing revisions and citations, not just new uploads.
   Identify disposable exports/temp files separately; never delete referenced
   originals as temporary cleanup. Verify behavior during storage unavailability.
2. Deliver a production image and deployment manifest: built frontend, no reload,
   explicit process/thread/resources, non-root, TLS/proxy settings, secure OIDC
   session configuration, readiness/liveness and graceful termination. Size proxy
   timeouts for the chosen polling/streaming paths. Keep the sandbox boundary and
   resource limits intact while scaling Office/code workloads.
3. Run migrations once as a release step; prove mixed-version rollout/rollback
   compatibility and worker drain behavior. Prefer additive schema changes;
   rollback cannot mean editing or reversing an applied migration in place.
4. Schedule database PITR and file/object recovery together. The existing logical
   backup requires stopped writers and has external key dependencies; it is not
   proof of online cross-store consistency. Define snapshot/version retention
   and prove the recovered DB references recoverable file versions. Protect and
   restore auth/profile keys separately. Set RPO ≤ 1 hour and RTO ≤ 4 hours as
   provisional targets; confirm them before public rollout and drill on Linux.

### Phase 4 — retention and staged student rollout

1. Define retention and deletion for private uploads, conversations, artifacts,
   traces/events and backups, preserving live citation references and required
   audit/accounting records. Measure cleanup, autovacuum, table/index growth and
   backup duration under the expected semester-long write volume.
2. Assign deployment, on-call and cost owners. Record exact image/config/feature
   flags, provider quota and price assumptions, daily/monthly budget caps, rollout
   limits and a documented stop/rollback procedure.
3. Roll out to bounded cohorts (for example 50, 300, 1,000, then 6,000 students),
   with at least one complete daily usage cycle at each step and a registration
   burst rehearsal. Advance only with healthy SLOs, bounded queues, acceptable
   spend and validated alerts. Sustained queue-age/SLO failures, scope violations,
   exhausted provider quota or recovery failures stop expansion. Change one
   capacity/feature assumption at a time and revalidate the affected gates.

## 7. Release acceptance for "scales to 6k"

The claim is made only for the frozen launch manifest after these gates pass
through the production-like topology. All numbers below are planning targets,
not achieved results. Name the environment, commit/config, corpus, provider and
date in release evidence. The in-process harness alone cannot satisfy them.

1. **Sustained arrivals:** after warmup, offer 10 turns/s for at least 30 minutes
   and 20 turns/s for at least 10 minutes, with 600 distinct active student
   sessions rotated across the 6,000-workspace fixture. The headroom test
   deliberately doubles the baseline per-session submission rate. Run the launch
   mix and each enabled class. No capacity 429s, 5xx responses or unexplained failed/lost turns within
   this envelope; completed throughput keeps pace with admitted work, queue age
   and depth do not trend upward, queue-wait p95 ≤ 2 s, and each class meets its
   declared completion SLO. Simple-turn p95 is ≤ 15 s with a 4 s provider baseline;
   tail-latency scenarios use the calibrated provider distribution and predeclared
   class SLOs. Include full terminal drain in accounting, not just submission rate.
2. **Burst and overload:** retain an 80-simultaneous-simple-turn burst test and
   p95 ≤ 15 s as an additional check. Then offer 30 turns/s for 2 minutes before
   returning to 10/s. Excess work receives bounded, explicit `429`/`Retry-After`
   responses; queues remain within configured caps and return to their pre-burst
   range within 5 minutes. Poll/read/cancel endpoints remain responsive. Verify
   concurrent admission and request replays cannot exceed caps or double-reserve
   spend; do not count rejections as successful answer throughput.
3. **Authenticated read traffic:** alongside generation, sustain at least 50/s
   non-poll reads plus the real client polling rate; minimum combined target
   160/s for the 80-pending-turn case. Recompute upward for longer research
   occupancy and test reconnect bursts. Read/poll p95 ≤ 300 ms, no pool timeouts,
   and logout/expiry/revocation meet the declared consistency policy on every
   replica. Include cold login and session-expiry bursts as separate scenarios.
4. **Database and workers:** sample all roles throughout these runs. Application
   connections remain < 70% of the DB limit, including rollout surge; no sustained
   pool queueing, provider-held borrows or lease-renewal failures. CPU, memory,
   model initialization and DB I/O stay inside the chosen resource envelope.
   Exercise mixed chat/research/upload/artifact/Office load and workspace skew;
   enabled background work cannot starve another workspace's interactive turns.
5. **Scoped retrieval:** pass F3's 300k-chunk, latency, recall and frozen advising
   eval gates. Include in-flight ingest/replacement, narrow/empty selections,
   private scopes and mixed models/dimensions. No unauthorized evidence, stale
   publication or accidental widening of an empty selection is acceptable.
6. **Durable storage:** upload/import on replica A and read original/export on B;
   verify checksums, historic revisions and citation locators after path backfill
   and rolling restart. Storage failures produce recoverable visible errors and
   no published record claiming an unavailable original is complete.
7. **Failure and drain:** terminate workers during provider I/O and publication,
   kill/restart an API replica, and exercise DB reconnects plus provider 429,
   timeout and outage. Verify lease fencing, cancellation, graceful drain and
   recovery within the declared lease/recovery interval. At most one publication;
   interrupted or unknown outcomes stay inspectable, and ambiguous provider work
   is never silently retried. Retry/fallback traffic stays inside the same
   operation, admission and spend budgets. Required reads/cancellation recover
   after dependencies return; these fault scenarios have separate error accounting
   from the healthy-load zero-5xx gate.
8. **Restore and rollback:** restore DB plus referenced file versions and external
   secrets into an isolated Linux environment within the agreed RPO/RTO. Verify
   `/ready`, login, private ownership, a saved conversation/citation, an original
   download, an artifact revision and unknown reservations; `/ready` alone is
   insufficient. Rehearse deploy rollback with in-flight work and additive schema
   compatibility. Never point the drill at the live environment or replay its
   outstanding provider jobs.
9. **Provider capacity and cost:** establish launch-profile quota for both
   sustained/headroom traffic and the measured class mix, including tool/repair
   calls, latency tails and retries. Produce measured per-class/day projections,
   total daily/monthly budget caps and an alert owner. Concurrent student/global
   cap enforcement and unknown-usage reservations survive restarts; hitting a
   cap cannot initiate an unreserved expensive call.
10. **Operational soak:** run the launch mix for at least 4 hours with scripted
    seams plus separately budgeted live calibration. Verify no growing queue,
    leaked pool/model/file resources or unbounded event growth. Run retention
    and online backup maintenance alongside load and validate alert delivery;
    rehearse the existing logical backup's stopped-writer window separately.
    Standard backend, real-DB, frontend and browser release checks still apply; load tests
    do not replace functional or advising-quality evaluation.

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
- Launch feature mix and per-class SLOs, provider/model/profile quotas and latency
  distributions, upload/artifact/Office rates and maximum outstanding work.
- Chosen topology and maximum rollout/autoscaling surge, effective DB limit,
  storage/versioning strategy, retention, and confirmed RPO/RTO targets.
- Named rollout/on-call/cost owners and the cohort advancement/rollback window.

These decisions must be recorded before a gate is run. Deferring a feature or
the 300k private-corpus target narrows the release claim explicitly; it does not
remove source isolation, overload protection or recovery requirements.

## 9. Non-goals

- Multi-region or active-active deployment.
- Redesigning advising or retrieval ranking. Any approximate search change still
  has to demonstrate acceptable scoped recall and no advising-eval regression.
- Reworking the job queue. `FOR UPDATE SKIP LOCKED`, leases, and one-attempt
  conversation jobs are already the right shape.
