# Review readiness: from working tree to reviewable pull requests

Status: requested plan (2026-10-12). Phases 0–2 are implemented (snapshot branch,
line-ending policy, `.opencode/` ignored, Prettier/ruff clean, CI workflow, corpus
runbook in `README.md`). Phase 3 (splitting into focused PRs) is not done; the
working tree was published to `main` as a platform snapshot for further testing.
`architecture.md` stays the design contract; this file only sequences the work.
Delete it once its PRs have merged.

## 1. What "review-worthy" means here

This is a semester project, not a deployed service, so the bar is not "zero risk". It is:

1. **A reviewer can understand each PR in one sitting.** It has one purpose, a
   description of why, and a diff small enough to read (aim for under ~1,500 changed
   lines of non-generated code; split anything larger).
2. **Every PR passes the same automated checks on a clean machine**, not just locally.
3. **Behavior changes come with evidence of behavior**: test results plus, for
   retrieval and generation changes, the live behavior-eval summary in the PR
   description. "Tests pass" alone is not enough evidence for answer quality.
4. **The repo rules in `AGENTS.md` hold**: deterministic prerequisite graph, named
   SQL only, provider seams respected, behavior tests rather than plumbing tests, no
   saved test output.
5. **Known gaps are written down** as issues or in the PR text, not left implicit.

## 2. Where things stand (measured 2026-10-12)

- **Everything is uncommitted on `main`.** The last commit is `feda7d3` (2026-10-07).
  `git status` shows 303 paths: 85 modified, 27 deleted, 191 untracked; the tracked
  diff alone is 112 files, +11,114 / −9,546 lines. Most of it predates the last two
  working sessions: auth, artifacts, sandbox, jobs, conversations, the frontend
  rebuild, the schema baseline consolidation.
- **No CI.** There is no `.github/workflows/`. Nothing checks a PR except whoever runs
  the commands locally.
- **Line endings are mixed.** `.gitattributes` only pins `*.sql`. Git warns about
  LF↔CRLF conversion on dozens of files, so diffs will be noisy until normalized.
- **Stray files in the tree**: `.opencode/goals/` (tool state) and `docs/references/`.
  Decide on each before committing.
- **The local dev database has state that the code alone cannot reproduce.**
  Migrations 034–036 are applied. Program data was regenerated and re-ingested; legacy
  duplicate sources were reconciled from a manifest; one bogus source ("Department of
  Veterans Affairs") was retired. A teammate's database will not match this without a
  runbook (§8).
- **Verification already done:** 855 backend tests pass, ruff is clean, frontend
  `typecheck` and `build` pass.
- **Not yet run since the latest changes:** frontend `npm test` and
  `npm run test:integration`, `python -m scripts.schema_baseline` (migration 036 is
  new), and `npm run format:check` over the whole tree (only the two changed files
  were checked).
- **The live behavior harness exists only in a temporary scratch folder**, outside the
  repo. Its results (below) are therefore not reproducible by anyone else yet.

### Work from the last two sessions (what the newest PRs will contain)

Bug fixes found by live behavior testing:
- pgvector registration on pooled connections (`db/connection.register_vector_adapter`).
- Tool controller: the last allowed model call must answer, not request more tools.
  Rejected tool requests become replayable results the model can correct, instead of
  a 422.
- Follow-up retrieval carries the course codes the previous answer named.
- Catalog PDF splitter no longer treats a policies-page line as a department.
- Legacy source reconciliation accepts the current catalog header format.
- Conversation history (and its byte budget) now carries only the passages an answer
  cited, not every retrieved passage.

New feature (opt-in, `QUERY_PLANNING_ENABLED`):
- A model reads a scoped catalog directory and proposes bounded searches before
  retrieval (`retrieval/directory.py`, `generation/query_plan.py`, the planned path in
  `retrieval/retrieve.py`, the restructure of `api/query_service.py`).
- Provider prompt caching: a `cache_key` on requests, sent as `prompt_cache_key` by
  the compatible adapter when `LLM_COMPATIBLE_CACHE_KEY=true`. Reported cache hits
  are stored in `model_calls.cached_prompt_tokens` (migration 036).

Measured results (LongCat, live):
- Exploration set (18 questions): 12/18 pass with planning off, 16/18 with it on. The
  two remaining failures are one grader false negative and one real miss (lists 5 of
  ~7 math programs).
- Original suite (66 questions): no regressions with planning on, after the
  conversation history fix.
- Prompt cache: 0/5 hits without the key, 5/5 with it. Planning calls cost about 67k
  input tokens each and add roughly 5–10 s warm, 11 s cold.

## 3. Phase 0 — Safety net (do first; ~30 minutes)

Nothing below should be able to lose work.

1. **Snapshot the working tree on a side branch** before splitting anything:
   `git switch -c wip/snapshot-2026-10-12`, `git add -A`, then commit with the message
   "WIP snapshot (unreviewed)". Push it. This branch is never merged; it is the
   reference copy the PRs are carved from. Then return to `main`, which still has the
   uncommitted tree.
2. **Back up the dev database** with the project's own tool
   (`python -m scripts.backup`) into `data/backups/`. The earlier `pre-034.dump` sits
   in a temporary scratch folder that the OS may clear; copy it into `data/backups/`
   too.
3. Record the exact dev-DB repair steps from the last session in the runbook (§8)
   while they are still fresh.

## 4. Phase 1 — Mechanical hygiene (one PR, no behavior change)

Goal: later diffs show only real changes.

1. Normalize line endings. Extend `.gitattributes` to `* text=auto eol=lf`, keeping
   explicit binary patterns for images, PDFs and fixtures. Then run
   `git add --renormalize .` and commit **only** that, so reviewers can skip it.
2. `.gitignore`: add `.opencode/` and `data/test-runs/` (already under `data/`, but
   say so explicitly). Decide whether `docs/references/` is source material that
   belongs in the repo or local notes that don't.
3. Run `npm run format` (Prettier) over `frontend/src` and `ruff format --check` (or
   just `ruff check`, the configured linter) so formatting churn lands here, not in
   feature PRs.

**Done when:** `git diff --stat` after renormalization shows only content changes, and
the PR contains no logic.

## 5. Phase 2 — Continuous integration (one PR)

Goal: every later PR is checked on a clean machine. Add
`.github/workflows/ci.yml` with three jobs:

| Job | Steps | Notes |
|---|---|---|
| backend | Python 3.12, `pip install -e "backend[dev]"`, `ruff check .`, `pytest` | A Postgres service with pgvector (e.g. `pgvector/pgvector:pg16`). Set `TEST_DATABASE_URL`, because the suite treats a skipped DB test as a failure. No model downloads: the suite uses fakes. |
| schema | `python -m scripts.schema_baseline` | Catches baseline/legacy drift, including migration 036. |
| frontend | `npm ci`, `npm run typecheck`, `npm run build`, `npm run format:check`, `npm test` | Add `npm run test:integration` as a separate job, allowed to fail at first if Chromium setup on CI needs work; fix, then make it required. |

Things to verify while setting this up:
- The backend suite relies on Windows-specific notes (pgserver, `--basetemp`); confirm
  it runs on Linux with `TEST_DATABASE_URL` and no pgserver.
- Pip caching keyed on `backend/pyproject.toml`. Torch and transformers make
  installs slow; consider a CPU-only torch index URL.

**Done when:** a no-op PR shows all required checks green.

## 6. Phase 3 — Split the working tree into PRs

### Recommended approach: one baseline PR, then focused PRs

Splitting ~20k lines of mostly older work into perfectly independent PRs would take
longer than it is worth for a semester project. Instead:

1. **Baseline PR — "Platform snapshot (pre-review)."** Everything that predates the
   last two sessions: auth, connections, artifacts, sandbox, jobs, conversations, the
   frontend rebuild, the schema baseline consolidation, docs/audit. The review is
   **architectural, not line-by-line**: does the layout match `architecture.md`, do
   the boundaries in `AGENTS.md` hold, does CI pass? State plainly in the PR text that
   it was not line-reviewed. Known issues go into a checklist in the description.
2. **Focused PRs for the recent behavior work**, each reviewed properly and stacked
   in this order:

| # | PR | Contents | Depends on |
|---|---|---|---|
| A | Behavior-test fixes | pool/pgvector registration, tool-controller final call, replayable tool rejections, catalog splitter, reconcile header. Each fix has its regression test. | Baseline |
| B | Conversation follow-ups | follow-up course carry-over; history and budget carry only cited passages; architecture text for both | A |
| C | Behavior eval harness | moves the scratch harness into the repo (§7) | Baseline |
| D | Query planning (opt-in) | directory, planner, planned retrieval, query-service restructure, `plan` seam and `planning` stage contract, docs | B, C |
| E | Prompt-cache plumbing | `cache_key`, compatible adapter, cached-token usage, migration 036, plus the portability work in §6.2 | D |

Each focused PR must pass CI on its own branch before the next is opened. If a
focused change turns out to be tangled with baseline code, move the smallest
necessary piece into the baseline PR rather than inventing a shim.

**Stricter alternative**, if a teammate wants line review of the platform code: split
the baseline into the areas in this table, in order. Database (baseline, migrations,
queries), then retrieval and atlas, generation and tools, jobs and operations,
conversations, artifacts and sandbox, auth and connections, and frontend last, because
it depends on the API contract. Build each PR from the snapshot branch with
`git checkout wip/snapshot-2026-10-12 -- <paths>`. History is no help here: the work
was never committed in pieces.

### 6.1 Per-PR definition of done

- One purpose, stated in the first line of the description.
- CI green.
- New behavior has a test that would fail without the change: a row in an existing
  table-driven test where one exists, per the testing policy.
- `architecture.md` updated in the same PR whenever a contract or behavior it
  describes changed. API/frontend type changes ship together, and
  `test_contract_drift.py` passes.
- Retrieval/generation PRs paste the behavior-eval summary (pass counts plus notable
  regressions) into the description.
- No new inline SQL, no concrete provider imports outside factories, no LLM touching
  the prerequisite graph.
- Known limitations listed under "Not in this PR".

### 6.2 Work to finish inside PR D/E before review

These are real gaps in the newest code. Each has an acceptance check.

**Prompt caching across providers** (the "no worky worky after switching models" risk):
1. Move `LLM_COMPATIBLE_CACHE_KEY` from a global setting to a per-connection option
   in the profile's `provider_options`, with the global value as the default for the
   env-configured provider. *Check:* a second OpenAI-compatible profile pointed at
   another endpoint does not send `prompt_cache_key` unless its own option says so.
2. Native OpenAI adapter: send `prompt_cache_key` and read
   `usage.input_tokens_details.cached_tokens`. *Check:* adapter test with a mock
   transport, like the existing compatible test.
3. Anthropic adapter: put a `cache_control` marker on the directory block when
   `cache_key` is set, and **fix accounting**. Anthropic reports
   `cache_read_input_tokens` and `cache_creation_input_tokens` separately from
   `input_tokens`, so the prompt total must be their sum, or budgets undercount.
   *Check:* adapter test asserting the summed `prompt_tokens` and the
   `cached_prompt_tokens` value.
4. Google adapter: record `usage_metadata.cached_content_token_count`.
5. Add `scripts/smoke/prompt_cache.py`, a manual, network-using script. It sends one
   long prefix with several different questions through the configured provider, with
   and without the key, and prints hit counts. Document in `AGENTS.md` that it must be
   run after any provider change.

**Planner correctness and tests** that are missing today:
- A planned document outside the request's source selection is dropped (fence test).
- Context assembly order and the `context_size` cap, including the round-robin
  between search queries.
- A cross-listed alias planned twice appears once.
- Private workspace: another workspace's filenames never appear in the directory
  through the API path (store-level coverage exists; add the end-to-end case).
- Cancellation during planning propagates and does not fall back.
- A planning call that fails is still accounted. Budget exhaustion before the call
  falls back with a warning.
- `cache_key` changes when the directory content changes, and is stable when it
  doesn't.
- `cached_prompt_tokens` is persisted on `model_calls`.
- The streaming worker emits the `planning` stage (`conversations/worker.py`).

**Budget realism:** reservation units are serialized bytes. The directory is ~197 KB,
so the planning reservation is ~200k units against a 300k planning allowance.
Document this. Add a check at startup (or in the planner) that fails loudly when the
directory exceeds the allowance, instead of silently falling back on every query.

**Latency:** measured warm planning is 5–10 s. Before making planning the default:
- Skip planning when the question names explicit course codes and has no exploration
  wording. Measure first: only skip if the eval shows no loss.
- Embed the planned search queries in one batch instead of one call each.
- Experiment: a two-step directory (subjects and programs first, then the chosen
  departments' courses). Keep it only if the eval holds and latency drops.

**Frontend:** add a `plan` seam example to `api/mock.ts` so demo mode exercises it.
Check that any trace or debug view renders the `plan` seam's `reason` (the plan
summary) readably.

**Provenance of `configs/catalog-directory.md`:** the hint words drive discovery
("robots" found Engineering Sciences through them). Record how the file was produced
and how to regenerate it when the atlas changes. The catalog-context plan forbids a
model-written summarization job in the first milestone, and `AGENTS.md` calls this
file "supplied". Make the regeneration procedure and its author explicit, so it does
not silently drift from the atlas. Codes absent from the atlas are already ignored.

**Default:** keep planning opt-in until the eval in §7 shows that the full suite does
not regress and the exploration set improves, with cost and latency noted in the PR.

## 7. Behavior evaluation as a repeatable check (PR C)

The most valuable evidence from the last sessions came from asking the real system
real questions. Make that repeatable:

1. Move the scratch harness to `backend/scripts/manual/behavior_eval.py`, with cases
   in `backend/scripts/manual/behavior_cases.py`. It is manual because it calls the
   live model, so it never runs in pytest. It prints a summary to the terminal and,
   per `AGENTS.md`, writes no result files unless asked (`--out` optional, default
   off).
2. Cases: the 66 original questions (prerequisites, facts, programs, sequencing,
   eligibility, exploration, negatives, adversarial, multi-turn), the 18
   exploration/career questions, and a held-out group of ~10 paraphrases nobody
   tunes against.
3. Fix the known grader bugs:
   - Word-boundary escapes were mangled, so the "56 hours" check could never match.
   - Accept "Economics 101" as well as "ECON 101".
   - Accept refusal phrasings such as "No supplied course …".
4. Flags: `--planning on|off` (and both, side by side), `--only ids`, `--group`.
5. Grading stays twofold: regex checks for the obvious cases, plus a short
   hand-review list printed for anything in the exploration group. Regex passes have
   hidden bad answers before.
6. Write the **baseline numbers** into the PR description, not a file. Hand-graded so
   far: about 57/66 on the original suite with planning off; on the exploration set,
   12/18 regex-graded with planning off and 16/18 with it on.

## 8. Data reproducibility runbook

Add a short section to `README.md` (or `docs/plans/` until merged) so another machine
reaches the same corpus. Use exact commands from `backend/`:

1. `python -m scripts.migrate`, which reaches 036.
2. `python -m scripts.scrape_programs --skip-departments`, then `import_programs`, then
   ingest `../data/programs/catalog/*` with the origin manifest.
3. `python -m scripts.reconcile_source_origins` with a manifest of verified legacy
   groups. Record how the manifest was produced. The last session generated it with a
   scratch script that grouped legacy sources by their catalog header. Either commit
   that script under `scripts/manual/`, or document the manual verification.
4. Retire the bogus "Department of Veterans Affairs" program source with the existing
   named query, and say why. The splitter fix prevents it on a fresh scrape, so on a
   clean rebuild this step is a no-op.
5. Rebuild the atlas and simgraph only if the atlas changed.

**Done when:** a teammate following it from an empty `data/` gets the same source
counts (last measured: 218 current of 372 total sources, 152 programs).

## 9. Known issues to track (not blocking; open as GitHub issues)

- **Parser:** the "or equivalent transfer credit" suffix makes CS 224/326/350 and
  ECON 220 ambiguous, which blocks the deterministic sequence planner for them.
  MATH 111's exclusion-only text is unparsed. This needs a decision on the grammar
  rule; it is a parser fix, never a model fallback.
- **Credits are absent from course chunks**, so "How many credits is MATH 221?"
  fails. The fix needs a change to the course chunk format (`render_courses`, fixed
  by `architecture.md`) plus an atlas re-import and simgraph rebuild.
- **Program listings are incomplete**: "What math majors exist?" names 5 of ~7 with
  or without planning. "What should I complete before the Data Science BS?" answers
  with course prerequisites instead of program entry requirements.
- **Unplanned keyword noise**: with planning off, "coding" still matches the Honor
  Code. Planning fixes this; the unplanned fallback path still has it.
- **Department pages can be stale** relative to the catalog PDF (already noted in
  `AGENTS.md`); answers can cite both.

## 10. Suggested order and rough effort

| Step | Effort |
|---|---|
| Phase 0 safety net | 0.5 h |
| Phase 1 hygiene PR | 1–2 h |
| Phase 2 CI PR | 3–6 h (most of it is getting pgvector and Chromium right on CI) |
| Baseline PR (architectural review) | 2–3 h to write the description and checklist |
| PR A, PR B | 1–2 h each (code exists; mostly descriptions and test review) |
| PR C (eval harness) | 3–4 h |
| PR D finishing work (§6.2 tests, budget check, frontend mock) | 1–2 days |
| PR E portability (per-connection option, OpenAI/Anthropic/Google, smoke script) | 1 day |
| Runbook (§8) | 1–2 h |

Start with Phases 0–2. They make every later step cheaper and catch environment
problems early. PRs A and B can be reviewed while D and E are finished.
