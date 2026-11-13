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
8. **Worker autoscaling.** Workers are a fixed count in `infra/modules/apps.bicep`.
   Add a queue-depth scaler (KEDA postgres) with min=1.
9. **Connection budget.** Pools of 20 per role times 10 API replicas is 400
   connections. Lower `DB_POOL_MAX_SIZE` to 5-8 or put PgBouncer in front.
10. **Finish the blob storage adapter**, so replicas need no shared Azure Files mount.
11. **Size Postgres `shared_buffers` for the 389 MB embedding index** and
    pg_prewarm it. A cold vector search takes 367 ms against 2-4 ms warm.
12. **Cache auth `resolve()` per session for 5-10 s.** Each poll costs an auth
    query; the tradeoff is a few seconds of revocation lag.
