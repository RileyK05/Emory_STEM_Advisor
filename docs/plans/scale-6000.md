# Scale to 6,000 concurrent turns

Written 2026-11-08 from the P5.5 load run on the development host. `scripts.load_test`
drives the real API, Postgres schema and job workers with fake model seams, so the
numbers isolate application and database limits rather than provider latency.

The load host is an 8-core Windows laptop (Intel Core Ultra 7 258V, 8 logical
processors). It is smaller than the target deployment and runs the API and workers
in one process; use these numbers as a lower bound on the production topology, not
as a substitute for a run against the deployed stack.

## Targets

| Tier | Load | Gate |
| --- | --- | --- |
| 1 | 300 concurrent turns | p95 first token < 5 s; p95 full answer < 60 s; zero failures apart from the honest rate-limited outcome during the injected 429 burst |
| 2 | 6,000 concurrent turns | Record p50/p95; zero lost turns; no exhausted DB connection; queue drains |

## Measured Tier 1

Command (from `backend/`):

```bash
python -m scripts.load_test --scenario conversation --requests 300 --concurrency 300 \
  --workers 300 --documents 40 --chunks-per-document 25 \
  --provider-p50-ms 3140 --provider-p95-ms 4700 \
  --token-p50 8925 --token-p95 12800 \
  --rate-limit-fraction 0.1 --rate-limit-retry-after 1 \
  --max-concurrency 300 --pool-size 30
```

The latency and token medians/percentiles are P2.1's per-call split (its per-turn
p50/p95 divided by its calls-per-turn p50/p95), so the one-call legacy load path
carries the same token and time shape as the measured research turns. Each turn
gets its own fake-auth workspace.

| Run | Completed | Failed | p95 first token | p95 full answer | Pool waits |
| --- | --- | --- | --- | --- | --- |
| best clean-host run | 284 | 4 failed + 12 pending | 42.7 s | 62.4 s | conversation pool 30/30, 10 errors |
| earlier 300-worker run | 299 | 1 | 43.9 s | 64.3 s | 0 errors |
| 60-worker pilot | 60 | 0 | 12.4 s | 15.7 s | 0 errors |

**Tier 1 does not pass on this host.** The first token misses by about 8x and the
full answer misses the 60 s gate. The binding limit is the per-role DB pool: 30
conversation connections cannot start 300 turns and finish their setup/retrieval
work within five seconds, so turns queue before the provider is called. Raising
`--pool-size` to 100 with a disposable `max_connections=500` did not help on this
8-core host (179 completed, 121 pool timeouts, p95 first token 54.5 s), and the
other runs were affected by a concurrent local workload. A larger host or
multiple API/worker replicas is needed before this gate can be re-run.

The failed turns are job failures after the 429 injection; their worker detail is
the honest rate-limited path, but the harness only records the generic terminal
failure, so the exact count is not used as a pass.

## Tier 2

Tier 2 was not run. It would require a production-sized host and connection
pooler; extrapolating the Tier 1 queueing gives no trustworthy 6,000-turn number.
The target remains recorded here, and the deployed run is the acceptance test.

## Connection budget

`DB_POOL_MAX_SIZE` is per role per process. The API uses two roles (`default`,
`tool_execution`); a conversation worker uses three (`default`,
`conversation_execution`, `tool_execution`). The dev run used 30 per role, so one
API process and one worker process would already request 5 × 30 = 150
connections, above a default `max_connections=100`. The production preflight
refuses that startup. Tier 1 needs either:

- a connection pooler (pgbouncer) in front of Postgres, so application pools are
  not server connections; or
- a server `max_connections` large enough for `pool × roles × replicas` plus the
  ten-connection reserve; or
- fewer per-role connections and enough replicas to keep the turn queue short.

The 300-turn run shows that simply lowering the pool makes queueing worse. The
connection budget and the latency target must be sized together.

## Provider call rate

P2.1's 300 analysed turns:

| Measure | p50 | p95 | Mean |
| --- | --- | --- | --- |
| Calls per turn | 4.0 | 12.1 | 5.2 |
| Tokens per turn | 35,702 | 154,683 | 52,824 |
| Seconds per turn | 12.57 | 56.98 | 19.71 |

At 300 concurrent turns and the p50 shape, the sustained call rate is
`300 / 19.71 × 5.2 ≈ 79 calls/s`, or about **854,000 calls per 3 hours**. At the
p95 shape it is still about 27 calls/s, or **295,000 calls per 3 hours**. The
observed provider allowance was about **4,700 calls per 3 hours** (0.44 calls/s).
Even a single turn per second at the mean call count is about 56,000 calls per
3 hours.

**The current provider plan cannot carry Tier 1.** It is short by roughly two
orders of magnitude at the p50 shape and by more than an order of magnitude at
the p95 shape. Tier 1 requires either a much larger provider allowance or a
different answering shape with fewer calls per turn and a longer acceptable
turn time. This is U6: the user must confirm the production provider plan against
this rate before launch.

## Cost per turn and per student

The token distribution is the cost driver. `model_price_schedules` is empty in
the base, so this document gives the formula and the token volumes rather than a
dollar total that would imply a rate nobody entered.

For input rate `Rin` and output rate `Rout` in USD per million tokens, with the
observed 95/5 input/output split:

- p50 turn: `0.033917 × Rin + 0.001785 × Rout`
- p95 turn: `0.146949 × Rin + 0.007734 × Rout`
- per student per day at 10 turns: 10× the per-turn figure
- per student per day at 50 turns: 50× the per-turn figure

Enter the provider's current rates in the Admin price schedule before launch;
the spend alerts and usage dashboard stay Unknown until then. At any plausible
rate, the p95 turn costs about 4.3× the p50 turn in tokens.

## Worker and replica counts

The base Bicep sets API 1–10 replicas, one worker replica and
`--concurrency 4`. That base is sized for correctness, not Tier 1.

A defensible Tier 1 target, subject to a pooler and provider capacity:

| Component | Count | Why |
| --- | --- | --- |
| API replicas | 3 minimum, 10 maximum | 50 concurrent requests per replica |
| Worker replicas | 8 | 32 in-flight jobs each gives 256 slots; 300 turns then queue briefly |
| Worker concurrency | 32 per process | near the process thread cap, with a pooler holding server connections |
| `LLM_MAX_CONCURRENCY` | 40 per worker process | 8 × 40 = 320 provider calls in flight, matching 300 turns |
| Postgres | pgbouncer or `max_connections ≥ 500` | without a pooler, 8 workers × 3 roles × 20 = 480 connections |

That topology has not been run. Until it is, Tier 1 remains unproven and the
provider plan remains the harder constraint: even with enough application slots,
4,700 calls per 3 hours cannot serve 300 research turns at 4–12 calls each.

## What the deployed run must record

Run Tier 1 and Tier 2 against the deployed stack with the same fake-provider
distribution and record: completed/failed counts, p50/p95 first token, p50/p95
full answer, pool waits and errors, `max_connections`, provider calls per turn,
and cost per turn/student/day from a populated price schedule. A Tier 1 pass on
the dev host is not claimed here.
