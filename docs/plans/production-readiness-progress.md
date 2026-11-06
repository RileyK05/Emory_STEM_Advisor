# Production-readiness progress ledger

Branch: `goal/production-readiness` (cut from `main` at `eb968c1`).
Plan: [production-readiness.md](production-readiness.md). This is the only
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
| P1.7 | todo | | |
| P1.8 | todo | | |
| P1.9 | todo | | |
| P1.10 | todo | | |
| P1.11 | todo | | |
| P1.12 | done | P1.12 | Deleted the `/v1/execute` endpoint, `ExecuteBody`/`InputFile`, `_decode_inputs`/`_program_response`, and the program-execution `run`/`_payload`/`_parse_result` from `sandbox/remote.py`; the worker health now reports the Office image and, when configured, the browser image. `DockerCliRuntime.probe` reads only Office (+browser) image IDs; `host_app` no longer reads the removed image env vars. Deleted `sandbox/Dockerfile.python`, `sandbox/Dockerfile.javascript`, their lines in `WORKER.md` and `compose.worker.yaml`, and `docs/architecture.md`'s code-execution claims. Program-execution tests removed; the shared runtime/transport tests stay. **Follow-up:** deleted `SandboxConfig.python_image`/`javascript_image`, `SandboxRunner.run`, `build_create_argv` and `_check_run_limits` (all program-execution paths), with the seven tests that only drove them; `test_sandbox.py` keeps the screenshot argv, isolation, cleanup, cancellation, path-escape and output-symlink tests. Full-suite gate: 1753 passed, 2 skipped (Windows-only), 0 xfailed in 900.48s; ruff clean. |
| P1.13 | todo | | |
| P1.14 | done | P1.6, P1.14 and data layer | Republished the Atlas-primary master with `scripts.reconcile_atlas --publish` and reran the sweep; `db-data-review.md` holds the 2026-11-06 counts. A 340, B 0, C 0, D two kinds, E 229 (all campus equivalence), F 56, cycles 0. No hand edits to `prereq_nodes` or `prereq_mentions`. |
| P1.15 | todo | | |

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
| P3.1 | todo | | |
| P3.2 | todo | | |
| P3.3 | todo | | |
| P3.4 | BLOCKED | - | Needs the user to score 10 artifacts 1-5 (D6). |

## Phase 4: sandbox benchmark

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P4.1 | todo | | |
| P4.2 | todo | | |
| P4.3 | BLOCKED | - | No Linux host with Docker and gVisor: `docker` is not installed on Windows, and `wsl -d Ubuntu -- bash -lc "docker --version; runsc --version"` prints `bash: line 1: docker: command not found` and `bash: line 1: runsc: command not found`. Reserved for the user's Claude session, which sets up the WSL2 host; this run installs nothing. |
| P4.4 | BLOCKED | - | Depends on P4.3 (host). |
| P4.5 | BLOCKED | - | Depends on P4.3 (host). |
| P4.6 | BLOCKED | - | Depends on P4.3 (host). |
| P4.7 | BLOCKED | - | Depends on P4.3 (host). |

## Phase 5: scale and cost

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P5.1 | todo | | Needs P2.1's numbers (D4 is settled). |
| P5.2 | todo | | D5 settled: build the limiter, off by default. |
| P5.3 | todo | | |
| P5.4 | todo | | |
| P5.5 | todo | | D4 settled: two tiers, 300 gating and 6000 recorded. |
| P5.6 | todo | | Needs P2.1's numbers (D4 is settled). |

## Phase 6: no dead ends

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P6.1 | todo | | |
| P6.2 | todo | | |
| P6.3 | todo | | |
| P6.4 | todo | | |
| P6.5 | todo | | |
| P6.6 | todo | | |
| P6.7 | todo | | |
| P6.8 | todo | | |

## Size table

Measured with the same method as the plan (`wc -l` equivalent).

| Measure | Before (main `eb968c1`) | After (this commit) | Target |
| --- | --- | --- | --- |
| `backend/app` Python lines | 52,308 | 49,385 | ≤ 42,000 |
| Backend test files | 125 | 123 | ≤ 95 |
| `frontend/src/admin` + `frontend/src/debug` lines | 4,700 | 3,168 | ≤ 3,300 |
| `frontend/src/api/types.ts` lines | 2,093 | 1,719 | ≤ 1,400 |
| `frontend/src/styles.css` lines | 2,887 | 2,887 | record only |
| Admin tabs | 18 | 7 | 7 |
| Roles | 4 | 4 | 2 |

## Run status: stopped before the final gate

This run implemented Phase 0 (R1, R2, R3, R4, R5, R6, R10) and P1.1-P1.6 and
P1.12. It stopped with the final gate **not** met: P1.7-P1.11, P1.13-P1.15,
Phases 2-6 and the benchmark work are not done, so no benchmark score exists.

Nothing below is BLOCKED except where the plan makes it depend on a user
decision or a host. The rest is simply not started, and is `todo` in the tables
above.

| Item | Why it cannot be finished by the agent | What unblocks it |
| --- | --- | --- |
| P2.1, P2.2 | needs live runs on the configured model; no runs were made in this session | a session that runs the probes |
| P3.2, P3.3 | needs P3.1's benchmark built and run; P3.3 needs Office on this machine | building and running P3.1 |
| P3.4 | needs the user to score 10 artifacts 1-5 (D6) | the user's scores |
| P4.3-P4.7 | needs a Linux host with Docker and gVisor `runsc`; this machine has none | the user's Claude session sets up WSL2 |
| P5.4 | open risk 3 of `docs/audit/security-attack-notes.md`; not started | implementation |
| All of D6 | human review of 51 gold entries and 48 eval cases | the user's review |

## Candidates for the user

Code that looks dead but is not on a cut list. Nothing is deleted without a
plan entry; each candidate below is left in place.

1. **`frontend/tests/browser-workflows.test.mjs` still carries the debug-session
   fixture** (`/api/debug/...` handlers, `debugSessionExpired`,
   `capturedDebugPatches`). P1.9 removes the debug backend; the fixture goes with
   it then.
2. **`api/backoffice.ts` debug helpers and `api/http.ts`'s `setDebugSessionToken`**
   have no caller after P1.2. They are removed with the backend in P1.9.
