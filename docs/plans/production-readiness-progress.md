# Production-readiness progress ledger

Branch: `goal/production-readiness` (cut from `main` at `eb968c1`).
Plan: [production-launch.md](production-launch.md) (superseding Phases 2–6 of
[production-readiness.md](production-readiness.md)). This is the only
status file for the run; every other diagnostic goes to the terminal.

Status values: `todo`, `doing`, `done`, `BLOCKED`.

## Phase 0: review findings

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| R1 | done | R1 | The history baseline is now sealed the first time a chat id is seen with its turns loaded, so a saved chat opened on page load or from New chat keeps its old runs out of the panel; a new chat gets its id before its first turn, so its baseline stays −1 and its first answer is tracked. New browser test "a saved chat keeps its old artifact run closed and only a new answer opens the panel" fails on the old Shell (assert panel count 0) and passes now. Gate: 41 browser tests pass, typecheck and format:check green; backend tree unchanged since the R2 gate. |
| R2 | done | R2 | Deleted `test_cs_171_requisites_are_cs_170_not_mus_221` (read the gitignored October catalog scrape and cited the Oxford catalog site); the Atlas-primary file already reads "CS 170 OR CS_OX 170". Baseline on `eb968c1`: 1 failed, 1766 passed, 2 skipped, 5 xfailed in 452.86s. Gate: 1766 passed, 2 skipped (Windows-only), 5 xfailed in 296.81s; ruff clean; typecheck, npm test (40 browser) and format:check green. |
| R3 | done | R3 | The inline `SELECT … FROM jobs … FOR UPDATE` in `conversations/recovery.py` is now the named query `lock_job_for_recovery` in `db/queries/jobs.sql`. Recovery now claims a candidate by locking its job first and then its conversation — the order publication uses (`lock_job_for_success_publish`, then `finish_uncommitted`'s conversation lock) — and skips a request whose conversation has moved on; the candidate scan no longer takes `FOR UPDATE OF r`. One row added to the orphan-settling test: a candidate whose conversation points at a newer request is skipped, so recovery cannot fail that turn. Gate: 1772 passed, 2 skipped (Windows-only), 0 xfailed in 790.85s; ruff clean; frontend tree unchanged since the P1.2 gate. |
| R4 | done | R4 | Removed `MTH`/`Mth`/`CMPSC`/`Cmpsc` from `SUBJECT_ALIASES`; student spellings now resolve only in the query-side lookup in `retrieval/seams/prereq.py` (`_QUERY_SUBJECT_SPELLINGS`). New row in `test_course_mentions` pins that the parser of record reads "MTH 111"/"CMPSC 170" literally and never remaps them to MATH/CS; the query rows "do mth 111…" and "…cmpsc 326" still resolve. Gate: 1767 passed, 2 skipped (Windows-only), 5 xfailed in 282.22s; ruff clean. |
| R5 | done | R5 | Deleted `frontend/tests/chat-shell-guards.test.mjs`: it tested copies of Shell logic and no npm script ran it. R1's browser test covers the real code. Gate: typecheck and format:check green; no test, build or script referenced the file (npm test's 41 browser tests unchanged at R1). |
| R6 | done | R6 | Reverted `Composer.submit` to `if (canSend) chat.send(attachments)`. Shell's `chatForView.send` already clears the manual attachments on accept, so the callback the composer passed (and Shell dropped) was dead. Gate: 41 browser tests pass (attachment clearing included), typecheck and format:check green. |
| R10 | done | R10 | (a) `generate_default_rules_tree` keeps one child per clause but marks the draft root `unresolved` with a reason, so a draft never claims a supported all-of set. (b) A new `_CHOOSE_RES` entry reads a count that opens a clause ("Three additional…", "3 Advanced QTM… electives", "Four (4) courses at the 300 level…") while "one course from each…" stays uncounted; `PARSER_VERSION` 2→3. Both xfail blocks removed; the counts test is strict. Programs re-imported with the regenerated catalog (`scrape_programs --pdf` from the recorded S3 source URL, offline re-parse): program items carrying a choose count went 76→333, 155 programs, 154 bound + 1 unresolved. Gate: 1772 passed, 2 skipped (Windows-only), 0 xfailed in 255.93s; ruff clean; frontend tree unchanged since the R6 gate. **Follow-up:** `audit_engine` now recurses into an unresolved node's children, so a draft audit reports one row per clause (new case in `test_degree_audit.py`). Two choose-count false positives found by sampling 15 of the counted clauses were fixed in the parser, not by hand: a permission or suggestion ("may be counted", "might consider") states no count, and a per-area rule ("one course in each …") is not a count; `PARSER_VERSION` 3→4 and the programs re-imported again: 333→318 counted clauses, 155 programs at parser 4, 154 bound + 1 unresolved. A second 15-clause sample matched its text; the one constraint-only clause ("One course must be at the 200-level or higher") still reports 1, which is the course it names. |

## Phase 1: cut the fat

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P1.1 | done | P1.1 | `AdminApp` now has exactly 7 tabs: Overview (with Jobs), Users (with the read-only audit log, no export), Sources, Courses & programs (course maintenance + a new `ProgramRulesPanel`), Student questions (feedback triage + unanswered questions), Usage & cost, Model connections. Deleted `ChatsPanel`, `ArtifactsPanel`, `CollectionsPanel`, `FeatureFlagsPanel`, `GrantsPanel`, `CatalogRefreshPanel`, `ExperimentPanel`, `PromptPromotionPanel` and `AUDITOR_TABS`; the program half of `SourceReviewPanel` moved to `ProgramRulesPanel` (source picker → its bound programs → inspection/releases). Admin browser tests: 5 ported to the new tabs (overview+Jobs with Cancel, Users+audit without export, sources, courses & programs, telemetry label), 7 deleted with their panels. Added `npm run check:callers` (`scripts/check-client-callers.mjs`, TypeScript-AST based): it finds 66 methods with no caller, which P1.3 removes. Gate: 32 browser tests pass, typecheck and format:check green. |
| P1.2 | done | P1.2 | Deleted `frontend/src/debug/` and the `debug` Vite entry; the build emits only `main` and `admin`. Per the user's decision the admin Details toggle and `DebugPanel` are cut entirely (no saved answer carries the retrieval trace they need): `showDetails` is gone from Shell, TopBar, ChatView and MessageView. Removed the admin Debug link. The two debug-bundle browser tests are gone. Gate: 32 browser tests pass, typecheck, format:check and check:callers green; backend tree unchanged since the R10 follow-up gate. |
| P1.3 | done | P1.3 | Removed the 29 caller-less `AdvisorClient` methods and the 37 `backoffice.ts` functions whose panels P1.1/P1.2 deleted (grants, admin chats, artifacts, collections, flags, catalog refresh, debug, experiments, prompts, audit export) from `types.ts`, `http.ts`, `mock.ts` and `backoffice.ts`, with the now-unused type imports. `npm run check:callers` is green: 39 `AdvisorClient` methods and 33 backoffice helpers, every one referenced outside an import. Unit tests that only exercised removed methods went; three were rewritten to keep their live half (privacy export token, FastAPI detail, original download + run events). Gate: 32 browser tests and 19 unit tests pass, typecheck, format:check and check:callers green. |
| P1.4 | done | P1.4 | Support grants and admin chat access are gone end to end: `auth/grants.py`, the admin `/grants*`, `/grant-requests*`, `/conversations*` (list, transcript, download, delete) and granted-result routes, their store methods, the grant/transcript schemas, `GRANT_SUPPORT_ACCESS`/`REVOKE_GRANTS`, the worker and runtime publication fences, and `db/queries/grants.sql` (its two workspace-execution queries moved to `auth.sql`). `check_workspace_access` is ownership-only; migration 080 drops `resource_grants`, `support_grant_requests` and `granted_result_staging`. No route under `/api/admin` reads another user's conversation. Grant/transcript tests deleted; `test_feedback` now proves a staff role has no route onto another workspace, and the records lifecycle drops its grant-driven verification case (records go in P1.6). Gate: 1746 passed, 2 skipped (Windows-only), 0 xfailed in 431.97s; ruff clean; 32 browser tests, typecheck, format:check and check:callers green. |
| P1.5 | done | P1.5 | Removed the admin `/artifacts` read and the `/collections` GET/POST/PATCH/DELETE routes, `AdminStore.artifacts/collections/create_collection/rename_collection/delete_collection`, their eight named queries in `db/queries/admin.sql`, the `AdminArtifact`/`AdminCollection`/`AdminCollectionWrite` schemas, and `test_admin_collection_crud`. The gap queue reads sources through `listSources`, so no collection read was needed. `docs/architecture.md` no longer lists the routes. Gate: `test_admin_debug`, `test_api`, `test_contract_drift`, `test_openapi_complete` pass (57 tests); ruff clean. |
| P1.6 | done | P1.6, P1.14 and data layer | Added `advisor_audit_program` and `advisor_suggest_courses`, `RESEARCH_TOOLS_VERSION` 15, one research-scenario row each. Removed `routes/audits.py`, `routes/comparisons.py`, `routes/records.py`, `advising/audit_service.py`, `advising/comparison_service.py`, `records/` and their queries; migration 081 drops the 9 tables and the `degree_audit_current_state` view. Published rules: 0, so every audit today takes the `reviewed_rules: false` line-match path, which the plan did not name. Reported courses carry no credit hours (`courses` has no credits column), so credit rules stay unresolved on the reviewed path. Gate: 1769 passed, 2 skipped; ruff clean; typecheck and format clean; `npm test` 32 passed. Live, `minimax-m3.1-flash`, 9 cases: audit called on all 4 that reported courses, the Oxford codes matched their lines, and the no-history case asked. In one Oxford answer the model still wrote that courses satisfy lines, so that wording has no enforcement. |
| P1.7 | done | e250fa6 | Roles are student and admin. Migration 086 maps `advisor` and `auditor` to `student` (0 users remapped on the dev database, which holds no users) and narrows the check. `EXPORT_AUDIT` and `VERIFY_ACADEMIC_RECORD` are gone; `/audit/export` went earlier. |
| P1.8 | done | e250fa6 | `flags/` and `routes/flags.py` are gone; `auth.capabilities.execution_settings` keeps the workspace check and the setting ceilings. Migration 085. See [data-layer-changes.md](data-layer-changes.md). |
| P1.9 | BLOCKED | e250fa6 | Catalog refresh is gone (migration 085). BLOCKED on U1: debug sessions, experiments and `DEBUG_*` in `apps.bicep` stay until the user decides whether to keep or cut the debug backend. |
| P1.10 | done | e250fa6 | Bulk drafts are gone. Migration 083. |
| P1.11 | done | e250fa6 | Routes are gone (migrations 083, 084). `import_docx`, `import_xlsx`, `parse_markdown`, `artifacts/revisions.py`, `/api/artifacts/{id}/original` and the PDF render dispatch are gone too. Stored-original columns stay: old backups restore through them. `claims` stays. |
| P1.12 | done | P1.12 | Deleted the `/v1/execute` endpoint, `ExecuteBody`/`InputFile`, `_decode_inputs`/`_program_response`, and the program-execution `run`/`_payload`/`_parse_result` from `sandbox/remote.py`; the worker health now reports the Office image and, when configured, the browser image. `DockerCliRuntime.probe` reads only Office (+browser) image IDs; `host_app` no longer reads the removed image env vars. Deleted `sandbox/Dockerfile.python`, `sandbox/Dockerfile.javascript`, their lines in `WORKER.md` and `compose.worker.yaml`, and `docs/architecture.md`'s code-execution claims. Program-execution tests removed; the shared runtime/transport tests stay. **Follow-up:** deleted `SandboxConfig.python_image`/`javascript_image`, `SandboxRunner.run`, `build_create_argv` and `_check_run_limits` (all program-execution paths), with the seven tests that only drove them; `test_sandbox.py` keeps the screenshot argv, isolation, cleanup, cancellation, path-escape and output-symlink tests. Full-suite gate: 1753 passed, 2 skipped (Windows-only), 0 xfailed in 900.48s; ruff clean. |
| P1.13 | done | e250fa6 | 18 test files merged into their area file; 1655 collected before and after, none lost. 93 backend test files (target 95). `test_chunker_consistency.py` stays: its helper collides with `test_chunker.py`. `test_program_rule_drafts.py`, `test_campus_equivalence.py`, `test_passage_identity_reading.py`, `test_citation_integrity.py`, `test_course_identity_guard.py`, `test_enrollment_restrictions.py` and `test_provider_bounds.py` stay as area files. No test reads the repository `data/` folder. |
| P1.14 | done | P1.6, P1.14 and data layer | Republished the Atlas-primary master with `scripts.reconcile_atlas --publish` and reran the sweep; `db-data-review.md` holds the 2026-11-06 counts. A 340, B 0, C 0, D two kinds, E 229 (all campus equivalence), F 56, cycles 0. No hand edits to `prereq_nodes` or `prereq_mentions`. |
| P1.15 | done | e250fa6 | `types.ts` 1,406 to about 1,160 lines, `styles.css` 2,550 to 2,487. A script finds no orphan type or schema model; the one absent class, `gap-state-resolved`, is built at runtime in `GapItem.tsx`. |

## Phase 2: one answering path

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P2.1 | todo | | |
| P2.2 | todo | | |
| P2.3 | todo | | |
| P2.4 | todo | | |
| P2.5 | todo | | |
| P2.6 | todo | | |

## Phase 3: artifact benchmark

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P3.1 | done | 7531d0a | Added backend/scripts/manual/artifact_benchmark.py and frozen 70 cases in artifact_benchmark_cases.jsonl (30 artifact cases A01-A30, 40 scope cases S01-S40). Runner exercises mechanical checks K1-K10 and prints per-case, per-check table and summary. Verified offline dry-run: 70/70 cases passed. Gate: pytest 1682 passed, 2 skipped in 284.17s; ruff clean; typecheck pass; npm test 19 unit passed, 38 browser passed in 63.10s; format:check pass; check:callers pass (38 AdvisorClient, 32 backoffice). |
| P3.2 | todo | | |
| P3.3 | todo | | |
| P3.4 | BLOCKED | - | BLOCKED on U4: needs the user to score 10 artifacts 1-5 from `data/manual-checks/artifact-sample/`. |

## Phase 4: sandbox benchmark

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P4.1 | todo | | |
| P4.2 | todo | | |
| P4.3 | BLOCKED | - | reserved for Claude (Stage H: Linux host with Docker and gVisor `runsc`; this run never installs or touches WSL, Docker or gVisor). |
| P4.4 | BLOCKED | - | reserved for Claude (depends on P4.3 host). |
| P4.5 | BLOCKED | - | reserved for Claude (depends on P4.3 host). |
| P4.6 | BLOCKED | - | reserved for Claude (depends on P4.3 host). |
| P4.7 | BLOCKED | - | reserved for Claude (depends on P4.3 host). |

## Phase 5: scale and cost

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P5.1 | todo | | Needs P2.1's numbers (D4 is settled). |
| P5.2 | done | 617254d | Added settings-driven per-student limiter (`QUOTA_QUERIES_PER_MINUTE=0`, `QUOTA_QUERIES_PER_DAY=0`, `QUOTA_TOKENS_PER_DAY=0`, `QUOTA_SPEND_USD_PER_DAY=0`, `QUOTA_MAX_ACTIVE_JOBS=0`; `0` = unlimited default) in `config.py` and `.env.example`, wired through `QuotaService` (`quotas/service.py`), `conversations/store.py`, `routes/conversations.py`, `routes/corpus.py`, `query_service.py`, `jobs/artifact_runs.py` and `jobs/store.py`. Daily exhaustion returns `"You've reached today's limit. It resets at 00:00 UTC."` with `Retry-After` and `X-Quota-Reset-At`; `frontend/src/api/http.ts` strips raw HTTP status prefixes from student-facing errors, normalizes any raw 429 to the reset-time sentence, and `useSavedChat.ts` preserves both the error banner and the student's draft on send failure. Verified by parametrized rows in `test_rate_limits_and_quotas.py` and browser test `"reaching the daily query limit shows the reset time without a raw 429 and keeps the draft"`. Gate: `.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml --rootdir backend --basetemp data/test-runs/pytest-p52-gate backend/tests` -> 1670 passed, 2 skipped (Windows-only), 0 xfailed in 518.37s; `.venv\Scripts\python.exe -m ruff check backend` -> All checks passed; `npm.cmd --prefix frontend run typecheck` -> pass; `npm.cmd --prefix frontend test` -> 19 unit passed, 33 browser passed; `npm.cmd --prefix frontend run format:check` -> pass; `npm.cmd --prefix frontend run check:callers` -> pass (37 AdvisorClient, 32 backoffice). |
| P5.3 | done | 95ea6f7 | Added `LLM_MAX_CONCURRENCY=8` in `config.py` and `.env.example`, wired per-process `ProviderGate` across cloud/compatible SDK sync and stream calls bounded by remaining operation deadline, and updated `retry_transient` (shared by legacy and research controllers) for exponential backoff with jitter, `Retry-After` honouring, immediate rate-limited failure when `Retry-After >= remaining_seconds`, deadline ceiling on backoff waits, and no replay on `ProviderTimeoutError(request_sent=True)`. `FakeClock` rows in `test_generation.py` and `test_provider_bounds.py` verify all four invariants with zero real `sleep`. Gate: `.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml --rootdir backend --basetemp data/test-runs/pytest-p53-gate backend/tests` -> 1666 passed, 2 skipped (Windows-only), 0 xfailed in 731.12s; `.venv\Scripts\python.exe -m ruff check backend` -> All checks passed; `npm.cmd --prefix frontend run typecheck` -> pass; `npm.cmd --prefix frontend test` -> 19 unit passed, 32 browser passed; `npm.cmd --prefix frontend run format:check` -> pass; `npm.cmd --prefix frontend run check:callers` -> pass (37 AdvisorClient, 32 backoffice). |
| P5.4 | done | P5.4 | Added `WORKSPACE_STORAGE_MAX_BYTES=104857600` (100 MiB = 5x the 20 MiB single-upload cap, explained in comments) in `config.py` and `.env.example`, workspace storage byte queries in `db/queries/quotas.sql` (`lock_workspace_storage`, `get_workspace_artifact_spec_bytes`, `list_workspace_storage_artifact_originals`, `list_workspace_storage_sources`) and `delete_artifact_for_workspace` in `db/queries/artifacts.sql`, and `QuotaService.check_storage_quota` / `workspace_storage_bytes` in `quotas/service.py`. Enforced across `ArtifactStore` (`create_uncommitted`, `revise_uncommitted`, `create`, `revise`), `ingest_file` (`ingest/pipeline.py`), `routes/corpus.py` (`upload_source`, `reindex_source`), `routes/artifacts.py`, `jobs/runtime.py`, and `scripts/worker.py`, refusing over-quota writes with HTTP 413 `"Your workspace storage is full. Remove an uploaded file or saved artifact before adding more."`. Verified by parametrized rows in `test_ingest_revision.py` and `test_artifact_api.py` proving refusal and space reclamation after deletion. Gate: `.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml --rootdir backend --basetemp data/test-runs/pytest-p54-gate backend/tests` -> 1673 passed, 2 skipped (Windows-only), 0 xfailed in 270.38s; `.venv\Scripts\python.exe -m ruff check backend` -> All checks passed; `npm.cmd --prefix frontend run typecheck` -> pass; `npm.cmd --prefix frontend test` -> 19 unit passed, 33 browser passed; `npm.cmd --prefix frontend run format:check` -> pass; `npm.cmd --prefix frontend run check:callers` -> pass (37 AdvisorClient, 32 backoffice). |
| P5.5 | todo | | D4 settled: two tiers, 300 gating and 6000 recorded. |
| P5.6 | todo | | Needs P2.1's numbers (D4 is settled). |
| P5.7 | done | 90f4a88 | Added migration 087 (`backend/app/db/migrations/087_search_speed.sql`) with stored generated tsvectors on `source_provenance` (`roles_vector`, `title_vector`), `courses` (`title_vector`), `sources` (`filename_vector`), `locators` (`label_vector`), GIN indexes on `source_provenance(roles_vector)` and `locators(label_vector)`, and HNSW cosine index `chunk_embeddings_cosine_768_hnsw_idx` on `chunk_embeddings USING hnsw (((embedding)::public.vector(768)) vector_cosine_ops) WHERE (dim = 768)`. Refactored `keyword_search` in `db/queries/keyword.sql` to eliminate per-row `to_tsvector` calls (pre-parsed query vectors + pre-computed column vectors) and switched ranking to `ts_rank`. Added `embed_search_768` and `set_hnsw_ef_search` in `db/queries/embeddings.sql`, wired `ef_search: int = 40` (default) / `200` (configured in `retrieval.toml`) in `retrieval/config.py` and `retrieval/seams/embed.py`. Benchmark verification: `scripts.eval_retrieval` recall unchanged at 0.9 (keyword 0.8375, embed 0.6); 50 sweep questions top-10 overlap between exact and HNSW = 0.962 (target >= 0.95); query latencies Q1 embed 66.0 ms (was 784.6 ms) / keyword 116.7 ms (was 386.9 ms), Q2 embed 13.1 ms (was 404.4 ms) / keyword 140.0 ms (was 1,278.3 ms), Q3 embed 14.9 ms (was 422.3 ms) / keyword 147.0 ms (was 2,332.5 ms); 50 sweep questions DB search latency p50 = 96.0 ms, p90 = 154.7 ms; `test_db_migrate.py` passes 12/12. Gate: pytest 1673 passed, 2 skipped (Windows-only), 0 xfailed; ruff clean; typecheck pass; npm test 19 unit passed, 33 browser passed; format:check pass; check:callers pass (37 AdvisorClient, 32 backoffice). |

## Phase 6: no dead ends

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P6.1 | done | 132b54c | Provider failures/timeouts and exhausted research budgets without valid finish settle as "The advisor couldn't answer that. Try again." with Retry sending the same question text: updated ConversationTurnExecutor.on_failure in `conversations/worker.py`, error mapping in `research_service.py`, and `ResearchController.run` in `generation/research_controller.py`; added `ToolConversationError` row to `test_a_provider_dropout_keeps_what_the_run_already_proposed` in `test_research_types.py` asserting the student-readable outcome, plus assertion in `test_conversation_failure_hook_settles_the_pending_request` in `test_conversations.py`; in frontend, added `chat.retry` and Retry button in `callout-error` in `MessageView.tsx`, `ChatView.tsx`, and `useSavedChat.ts`, verified by browser test "provider error settles with 'The advisor couldn't answer that. Try again.' and Retry sends the same text". Gate: pytest 1674 passed, 2 skipped (Windows-only), 0 xfailed; ruff clean; typecheck pass; npm test 19 unit passed, 34 browser passed; format:check pass; check:callers pass (37 AdvisorClient, 32 backoffice). |
| P6.2 | done | bac640e | After 20 s with a queued/non-starting turn, MessageView displays "This is taking longer than usual." with Retry; clicking Retry abandons the pending request via POST /api/conversations/{id}/turns/{requestId}/abandon and resends the user question. Added requestId and onRetry to PendingAnswer in MessageView.tsx, retryPending in useSavedChat.ts with conversationRef for un-rendered conversation state access. Verified by browser test "worker down turn never starts: after 20 s shows 'This is taking longer than usual.' and Retry abandons and resends". Gate: pytest 1674 passed, 2 skipped (Windows-only), 0 xfailed in 259.87s; ruff clean; typecheck pass; npm test 19 unit passed, 35 browser passed in 54.67s; format:check pass; check:callers pass (37 AdvisorClient, 32 backoffice). |
| P6.3 | done | a1d22a5 | Settled expired running leases in recovery with "The advisor couldn't answer that. Try again." in `conversations/recovery.py`, asserted terminal status and student-facing reason in `test_reconcile_settles_an_expired_running_lease` in `test_conversations.py`, and verified via browser test "worker dies mid-turn: expired lease settles turn as failed with 'The advisor couldn't answer that. Try again.' and Retry". Gate: pytest 1674 passed, 2 skipped (Windows-only), 0 xfailed in 291.30s; ruff clean; typecheck pass; npm test 19 unit passed, 36 browser passed in 58.03s; format:check pass; check:callers pass (37 AdvisorClient, 32 backoffice). |
| P6.4 | done | f45563d | Added route `POST /api/runs/{runId}/retry` in `backend/app/api/routes/runs.py` which validates failed job status, runs a fresh expected-revision check against `ArtifactStore` when revising an artifact, releases pooled connection borrow before re-admission, and re-admits frozen inputs via `admit_artifact_run`; in frontend, added `retryArtifactRun` to `AdvisorClient`, `HttpClient`, and `MockClient`, wired `retryRun` in `useArtifactWorkspace.ts`, and updated `RunCard` in `ArtifactPanel.tsx` to display `"Couldn't make this. Try again."` with a `Retry` button on failed runs. Verified by parametrized table row tests in `test_run_routes.py` covering matching/stale revision checks, non-failed rejection, workspace scoping, and re-admission, Postgres integration test, contract drift check, and browser test `"artifact run fails: panel card says 'Couldn't make this. Try again.' and Retry calls POST /api/runs/{runId}/retry"`. Gate: pytest 1682 passed, 2 skipped (Windows-only), 0 xfailed in 259.13s; ruff clean; typecheck pass; npm test 19 unit passed, 37 browser passed in 61.56s; format:check pass; check:callers pass (38 AdvisorClient, 32 backoffice). |
| P6.5 | todo | | |
| P6.6 | todo | | |
| P6.7 | done | 02dcb7c | Stream polling in `useSavedChat.ts` preserves provisional preview draft upon transport failure, displaying `'Reconnecting to the answer in progress… Your question will not be sent twice.'` without clearing the draft, and clears notice and replaces the provisional draft when stream events resume and the turn completes. Verified by browser test `"network drops mid-stream: draft stays during reconnect and saved answer replaces it when turn finishes"`. Gate: pytest 1682 passed, 2 skipped (Windows-only), 0 xfailed in 290.38s; ruff clean; typecheck pass; npm test 19 unit passed, 38 browser passed in 63.18s; format:check pass; check:callers pass (38 AdvisorClient, 32 backoffice). |
| P6.8 | done | 266f8c3 | Added `WORKER_HEALTH_PORT=0` in `config.py` and `.env.example`, `install_stop_signals`, `WorkerHealthServer` (`/health` 200 and `/ready` 200 while accepting jobs / 503 `draining` after stop), and `WorkerRunner` (claims no new jobs on stop and drains in-flight work up to `lease_seconds`) in `app/jobs/worker.py` and `scripts/worker.py`, plus `WORKER_HEALTH_PORT=8081` and HTTP liveness/readiness probes on the `worker` Container App in `infra/modules/apps.bicep`. Parametrized `FakeClock` rows in `test_jobs.py` verify clean drain within lease and bounded exit on hung handler + readiness flip. Gate: `.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml --rootdir backend --basetemp data/test-runs/pytest-p68-gate backend/tests` -> 1668 passed, 2 skipped (Windows-only), 0 xfailed in 632.93s; `.venv\Scripts\python.exe -m ruff check backend` -> All checks passed; `npm.cmd --prefix frontend run typecheck` -> pass; `npm.cmd --prefix frontend test` -> 19 unit passed, 32 browser passed; `npm.cmd --prefix frontend run format:check` -> pass; `npm.cmd --prefix frontend run check:callers` -> pass (37 AdvisorClient, 32 backoffice). |

## Phase 7: hosting

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P7.1 | todo | | |
| P7.2 | todo | | |
| P7.3 | todo | | |
| P7.4 | todo | | |
| P7.5 | todo | | |
| P7.6 | todo | | |
| P7.7 | todo | | |
| P7.8 | todo | | |

## Phase 8: known answer misses

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P8.1 | todo | | Reproduce and fix during Stage D (`P2.2`). |

## Stage I: user items

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| U1 | BLOCKED | - | User decision: keep or cut the debug backend (`debug/`, `api/routes/debug.py`, `conversations/experiments.py`, `DEBUG_*` in `apps.bicep`, debug fixture/client helpers). Unblocks P1.9. |
| U2 | BLOCKED | - | User review: publish reviewed requirement rules for top programs (`0` published today). Unblocks real degree audits. |
| U3 | BLOCKED | - | User review: review 51 entries in `requirement_gold.toml` and 48 retrieval eval cases. Unblocks trustworthy eval numbers. |
| U4 | BLOCKED | - | User review: score 10 artifacts 1–5 from `data/manual-checks/artifact-sample/`. Unblocks P3.4. |
| U5 | BLOCKED | - | User decision: confirm production model (D1) and rerun P2.1/P3.2 if different from `minimax-m3.1-flash`. Unblocks launch sign-off. |
| U6 | BLOCKED | - | User confirmation: verify provider plan capacity against P5.6's tier-1 call rate. Unblocks launch. |
| U7 | BLOCKED | - | User/hosting team answers: 14 questions in `azure-hosting-requirements.md` §9. Unblocks school auth adapter and real deploy. |
| U8 | BLOCKED | - | User/Emory confirmation: graduation credits (127 vs 126 in `configs/source_conflicts.toml`) and Math BS hours/courses figure. Unblocks removing disclosed conflicts. |
| U9 | BLOCKED | - | User action: upload `data/team/advisor-20261107.zip` privately (never in git). Unblocks teammates. |

## Size table

Measured with the same method as the plan (`wc -l` equivalent).

| Measure | Before (main `eb968c1`) | After (`e250fa6`) | Target |
| --- | --- | --- | --- |
| `backend/app` Python lines | 52,308 | 40,860 | ≤ 42,000 |
| Backend test files | 125 | 93 | ≤ 95 |
| `frontend/src/admin` + `frontend/src/debug` lines | 4,700 | 3,132 | ≤ 3,300 |
| `frontend/src/api/types.ts` lines | 2,093 | 1,155 | ≤ 1,400 |
| `frontend/src/styles.css` lines | 2,887 | 2,487 | record only |
| Admin tabs | 18 | 7 | 7 |
| Roles | 4 | 2 | 2 |

## Run status: in progress

Implementing `docs/plans/production-launch.md` stage by stage on `goal/production-readiness`.

## Candidates for the user

Code that looks dead but is not on a cut list. Nothing is deleted without a
plan entry; each candidate below is left in place.

1. **`frontend/tests/browser-workflows.test.mjs` still carries the debug-session
   fixture** (`/api/debug/...` handlers, `debugSessionExpired`,
   `capturedDebugPatches`). P1.9 removes the debug backend; the fixture goes with
   it then.
2. **`api/backoffice.ts` debug helpers and `api/http.ts`'s `setDebugSessionToken`**
   have no caller after P1.2. They are removed with the backend in P1.9.
