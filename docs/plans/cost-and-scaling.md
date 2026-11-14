# Cost, speed and scaling (2026-11-13)

## Baseline (dev database, last 14 days of real traffic)

| | Chat answer (research run) | Artifact run |
|---|---|---|
| Runs | 4,097 | 1,030 |
| Model calls per run | 6.9 avg, p90 12 | 3.5 avg, p90 6 |
| Input tokens per run | 74k (36k served from the provider cache) | 14k (9k cached) |
| Output tokens per run | 6.0k | 5.5k |
| Model time per run | median 34 s, p90 115 s | median 18 s, p90 88 s |

- 30.6k of 31k completed calls went to `minimax-m3.1-flash` (openai-compatible).
  The average call was 10.2k input tokens and 956 output tokens, with a median
  latency of 4.7 s.
- Research round 1 is 6.8k tokens, 6.3k of them cached: the static system
  prompt plus tools. Later rounds grow to about 13k, but only about 5k stay cached.
- There were 2,656 rejected finishes, about 0.65 extra model rounds per run. By
  reason:
  - Unread recommended course: about 560.
  - A judgment block without a candidate: about 300.
  - Truncated output: 160.
  - Codes absent from the cited evidence: about 300.
  - A course no longer offered: about 140.

## Done

### Chat answers (LLM tokens and rounds)

- **The system prompt is identical every round.** The per-round "CURRENT
  ROUND" note duplicated the tool list and the hint. The hint now travels only
  in the user context. Repair rounds had carried the 1.5-3.5 KB hint two or
  three times.
- **The context is ordered from stable to changing**, so a provider's prefix
  cache reuses more of each round. The order is: question, history, scope, then
  evidence, then state and actions, then limits and flags last.
- **Stable cache key.** Research requests carry a `cache_key` for their prompt
  prefix, and artifact stages carry one per stage kind. It is sent to the
  provider when `LLM_COMPATIBLE_CACHE_KEY=true`.
- **Smaller evidence records.** Records drop server-only identities, digests,
  steps and empty fields: 869 → 328 bytes for a short passage, about 135 tokens
  per record per round. Scope notices are still computed from the full records.
- **Smaller tool schemas.** Schemas drop pydantic's generated titles, a property
  named "title" excepted. They are built once per process instead of every
  round: 17.1 → 14.9 KB for the 13 research tools, about 570 tokens per round.
- **No artifact nudge round.** A finish without the requested artifact no
  longer gets one: the request is attached from the student's words in code.
  The missing-program nudge stays, because the reply must say so.
- **No state resend.** A state-only round no longer resends its call; the state
  is already in the next context.
- **Lower reasoning effort where little thought is needed.** Rounds that only
  open an issued handle (forced read, program, continuation and policy rounds)
  ask for low effort. So do truncation retries, because hidden reasoning is what
  fills the allowance.
- **Slips fixed in code instead of a model round.** `normalize_decision` turns a
  judgment block with evidence but no candidate into a fact (a stricter kind),
  and drops citations of evidence the request never supplied.
- **Byte-exact incremental packing.** Evidence packing no longer re-serializes
  the whole prompt per passage. It was checked to equal a full re-serialization
  in all 326 research tests.

### Artifacts

- **Plans make no per-term model call.** Each term used to make one for an
  optional copied note, which cost 1+N calls per plan.
- **A plan for one named program needs no outline call** when no course is
  named: its required lines and the term packer decide everything.
- **Code-owned outlines are slim.** For plan, what-if and built templates, the
  outline payload drops source excerpts and the template, and asks for low effort.
- **The catalog pick stage is cheap:** low effort and a 1,024-token cap.
- **Built sections are never repaired.** A finding on a code-built section fails
  fast instead of burning repair rounds whose output was ignored.
- **Sections share one system prompt.** Template guidance goes only in the
  payload, so every section's system prompt is identical and cacheable.
- **Built pages skip the vision review** when it is enabled.
- **The scope is built once per run.**

### Server compute

- **Workers back off when idle**, from 1 s up to 15 s (`--max-idle-seconds`). A
  queued job wakes them at once: `pg_notify('advisor_jobs')` fires on enqueue,
  and `listen_for_jobs` runs in its own thread. Polling stays as the safety net.
- **Gentler chat polling.** It stays at 750 ms for the first 10 s, then slows to
  1.5 s, and to 2.5 s after 30 s. That is about a third of the requests and
  queries for a long answer.
- **The research step lock no longer reads the scope manifest.** The manifest
  averages 38.7 KB, 97% of the row, and was read about 14 times per run. It is
  now read once per run, where it is needed.
- **Campus equivalences load once per research run** instead of at up to six
  call sites.
- **Migration 089 adds expression indexes** for the two per-answer offering
  queries: variants 57.7 → 2.4 ms and chunks 35.9 → 0.17 ms on the dev data.
- **nginx gzips the bundle and the API's JSON.** Conversation records reach
  hundreds of KB.
- **`EMBEDDING_THREADS` caps torch threads.** The worker defaults it to cores ÷
  concurrency.

### Embeddings

- **Ingest reuses vectors for text it has already embedded** (migration 090).
  Each vector stores a hash of its exact text, and a chunk whose text another
  readable source already embedded with the same model copies that vector. A
  text repeated within one source is embedded once. The dev data holds 52.9k
  vectors for 38.2k distinct texts.
  - This dev history saves little: the Atlas and offering formats changed
    between versions (credits, campus equivalents, instructors and meeting
    times were added). Only 1% of current Atlas chunks and 43% of program
    chunks match an older version. Once the formats stay the same, a
    re-capture skips the model for every unchanged course.
  - Rows written before 090 have no hash, so each source's next import embeds
    as before and records them. Backfilling them in SQL took 84 s under a table
    lock, too long for a startup migration.
- **The vector index holds only searchable vectors** (migration 091). Only
  11.8k of 52.9k vectors (22%) can be returned. The rest belong to superseded
  versions, hidden term cards or failed sources. They used up the 200
  nearest-neighbour slots before the filters ran, and only 57 survived on
  average.
  - Measured on the dev database with 268 real and labeled queries, the search
    now finds 99.0% of the true top 40, up from 86.7%. p50 fell from 10.4 to
    5.5 ms and p95 from 30.1 to 8.8 ms. The index is 46 MB instead of 167 MB.
  - Triggers keep a `searchable` flag in step with the source status,
    `is_current` and `search_hidden`, so reactivating an old version works.
    Superseding a 6,349-vector source flips its rows in about 1 s.
  - The migration takes about 10 s on the dev data.

### Scaling (2026-11-13)

- **Workers autoscale on the job queue.**
  - KEDA's `postgresql` rule counts running jobs plus due queued jobs, one
    replica per `WORKER_CONCURRENCY` of them. The defaults are 2-14 replicas
    of 24 slots, about 300 answers in progress.
  - This replaces the fixed single worker with 4 slots.
  - Scale-in drains for `WORKER_DRAIN_SECONDS` (570) under a 600 s grace
    period, longer than the 540 s advice limit, so no answer restarts.
    Before this, Container Apps' 30 s default killed workers in the middle of
    their 60 s drain.
- **The built-in PgBouncer carries request traffic.**
  - `DATABASE_DIRECT_URL` keeps LISTEN, migrations, backups, probes and the
    scaler on direct sessions.
  - Pooled connections send no prepared statements or startup options. The
    two timeouts become server parameters, and direct sessions clear them.
  - Behind the pooler the startup connection-budget check is skipped, so
    autoscaled replicas no longer need a fixed `DB_EXPECTED_REPLICAS`.
- **The global admission cap** went from 200 to 600 outstanding turns, so a
  peak queues instead of being refused below worker capacity.
- **Not verified on Azure yet:** whether the scaler reaches the private
  database endpoint, the PgBouncer parameter names and values, and
  `terminationGracePeriodSeconds` on API version 2024-03-01. The deployed Tier 1
  load test in `scale-6000.md` is the acceptance check.

## Follow-ups, not done (each needs a decision or a live check)

1. **Run the QA bot and compare against this baseline.** Calls per run, tokens,
   rejected finishes, and latency should all fall, while answer quality holds.
2. **Turn on `LLM_COMPATIBLE_CACHE_KEY`** if the production endpoint accepts
   `prompt_cache_key` (it is verified on LongCat; check minimax).
3. **Trim SYSTEM**, now 10.8 KB. Move the key-specific rules into `*_note` fields
   sent only when that key appears, as `no_current_course_note` already does.
   This needs an eval run first.
4. **Pre-open courses the question names** before round 1. It would save about
   two rounds on course lookups. It needs an eval, because it bypasses the
   forced-state round.
5. **Tool narrowing per round changes the tools array**, which can break prefix
   caching on providers that render tools first. Keeping one fixed tool list and
   enforcing the allowed set in the controller would cache better. Measure first.
6. **Store each scope manifest once.** Today each run copies about 40 KB
   (`research_runs` is 188 MB for 4.5k runs). Dedupe it into a table keyed by
   `scope_digest` (schema change).
7. **Ingest uploads and reindexes in workers.** Then the API process never loads
   the embedder and can run at about 1 CPU / 1-2 GiB, with more small replicas.
8. **(Done: worker autoscaling, listed under Scaling.)**
9. **(Done: built-in PgBouncer, listed under Scaling.)**
10. **Finish the blob storage adapter**, so replicas need no shared Azure Files mount.
11. **Size Postgres `shared_buffers` for the 389 MB embedding index** and
    pg_prewarm it. A cold vector search takes 367 ms against 2-4 ms warm.
12. **Cache auth `resolve()` per session for 5-10 s.** Each poll costs an auth
    query; the tradeoff is a few seconds of revocation lag.
13. **(Done: migration 091, listed under Embeddings.)**
14. **A smaller embedding model.** `granite-embedding-30m-english` (384 dims) is
    about 4× less compute per chunk and half the storage. It needs a retrieval
    eval and a full re-embed.
15. **An fp16 (halfvec) vector index.** It needs pgvector 0.7 or later.
    - In an offline test on the dev vectors, fp16 kept 99.7% of the exact top
      10 and 99.8% of the top 40, with cosine error at most 0.0002. int8 kept
      99.3%, but pgvector has no int8 type. int4 and binary need an fp32
      re-rank of about 400 candidates and still keep only 92-94% of the top 40.
    - Measured on the throwaway copy, an fp16 HNSW index over all rows is
      85.8 MB instead of 173.7 MB, finds the same results (87.5% vs 87.1%) and
      is slightly faster (p50 2.5 ms vs 2.6 ms). Over searchable rows only (now
      the index's shape) it is 24 MB instead of 48 MB, with the same 99.2%
      found and a p50 of 1.3 ms instead of 1.8 ms.
    - pgvector 0.8.7 builds against pgserver's MinGW Postgres 16 with MSYS2's
      gcc and the PGXS makefile (`make PG_CONFIG=...`). `ALTER EXTENSION vector
      UPDATE` takes an existing 0.6.2 database with an HNSW index to 0.8.7 in
      place. The test build used `-march=native`; a build to share with the
      team needs a generic `-march`.
    - Blocker: `pgserver` 0.1.4 (June 2024) is the last upstream release and
      bundles pgvector 0.6.2. `pgembed` 0.2.0 is a maintained fork, but it runs
      Postgres 17, so the dev database would need a dump and restore and would
      no longer match production's 16.
    - The compose image `pgvector/pgvector:pg16` and Azure Flexible Server
      already offer 0.7 or later; check the deployed server with
      `SELECT version FROM pg_available_extension_versions WHERE name = 'vector'`.
    - Until every database the app runs on has 0.7, the migration and the
      search query need an fp32 fallback.
