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
| R10 | done | R10 | (a) `generate_default_rules_tree` keeps one child per clause but marks the draft root `unresolved` with a reason, so a draft never claims a supported all-of set. (b) A new `_CHOOSE_RES` entry reads a count that opens a clause ("Three additional…", "3 Advanced QTM… electives", "Four (4) courses at the 300 level…") while "one course from each…" stays uncounted; `PARSER_VERSION` 2→3. Both xfail blocks removed; the counts test is strict. Programs re-imported with the regenerated catalog (`scrape_programs --pdf` from the recorded S3 source URL, offline re-parse): 76→333 program items carry a choose count, 155 programs at parser 3, 154 bound + 1 unresolved. Gate: 1772 passed, 2 skipped (Windows-only), 0 xfailed in 255.93s; ruff clean; frontend tree unchanged since the R6 gate. |

## Phase 1: cut the fat

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P1.1 | done | P1.1 | `AdminApp` now has exactly 7 tabs: Overview (with Jobs), Users (with the read-only audit log, no export), Sources, Courses & programs (course maintenance + a new `ProgramRulesPanel`), Student questions (feedback triage + unanswered questions), Usage & cost, Model connections. Deleted `ChatsPanel`, `ArtifactsPanel`, `CollectionsPanel`, `FeatureFlagsPanel`, `GrantsPanel`, `CatalogRefreshPanel`, `ExperimentPanel`, `PromptPromotionPanel` and `AUDITOR_TABS`; the program half of `SourceReviewPanel` moved to `ProgramRulesPanel` (source picker → its bound programs → inspection/releases). Admin browser tests: 5 ported to the new tabs (overview+Jobs with Cancel, Users+audit without export, sources, courses & programs, telemetry label), 7 deleted with their panels. Added `npm run check:callers` (`scripts/check-client-callers.mjs`, TypeScript-AST based): it finds 66 methods with no caller, which P1.3 removes. Gate: 32 browser tests pass, typecheck and format:check green. |
| P1.2 | done | P1.2 | Deleted `frontend/src/debug/` and the `debug` Vite entry; the build now emits only `main` and `admin` chunks. The Details view is a "Answer details" toggle in the student app's account menu, shown to admins only (Shell owns the state, TopBar renders it). Removed the admin "Debug" link. The two debug-bundle browser tests are replaced by one test: a student's menu has no toggle, an admin's flips it. Saved answers carry no retrieval trace (`entriesFromTurns` sets no `trace`; only the legacy query response did), so the panel is empty for saved chats — recorded under candidates. Gate: 39 browser tests pass, typecheck and format:check green; backend tree unchanged since the R10 gate. |
| P1.3 | done | P1.3 | Removed the 29 caller-less `AdvisorClient` methods and the 37 `backoffice.ts` functions whose panels P1.1/P1.2 deleted (grants, admin chats, artifacts, collections, flags, catalog refresh, debug, experiments, prompts, audit export) from `types.ts`, `http.ts`, `mock.ts` and `backoffice.ts`, with the now-unused type imports. `npm run check:callers` is green: 39 `AdvisorClient` methods and 33 backoffice helpers, every one referenced outside an import. Unit tests that only exercised removed methods went; three were rewritten to keep their live half (privacy export token, FastAPI detail, original download + run events). Gate: 32 browser tests and 19 unit tests pass, typecheck, format:check and check:callers green. |
| P1.4 | todo | | |
| P1.5 | todo | | |
| P1.6 | todo | | |
| P1.7 | todo | | |
| P1.8 | todo | | |
| P1.9 | todo | | |
| P1.10 | todo | | |
| P1.11 | todo | | |
| P1.12 | todo | | |
| P1.13 | todo | | |
| P1.14 | todo | | |
| P1.15 | todo | | |

## Phase 2: one answering path

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P2.1 | todo | | |
| P2.2 | todo | | |
| P2.3 | todo | | |
| P2.4 | todo | | |
| P2.5 | todo | | |

## Phase 3: artifact benchmark

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P3.1 | todo | | |
| P3.2 | todo | | |
| P3.3 | todo | | |
| P3.4 | todo | | |

## Phase 4: sandbox benchmark

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P4.1 | todo | | |
| P4.2 | todo | | |
| P4.3 | todo | | |
| P4.4 | todo | | |
| P4.5 | todo | | |
| P4.6 | todo | | |
| P4.7 | todo | | |

## Phase 5: scale and cost

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P5.1 | todo | | |
| P5.2 | todo | | |
| P5.3 | todo | | |
| P5.4 | todo | | |
| P5.5 | todo | | |
| P5.6 | todo | | |

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

| Measure | Before (main `eb968c1`) | After (final) | Target |
| --- | --- | --- | --- |
| `backend/app` Python lines | 52,308 | | ≤ 42,000 |
| Backend test files | 125 | | ≤ 95 |
| `frontend/src/admin` + `frontend/src/debug` lines | 4,700 | | ≤ 3,300 |
| `frontend/src/api/types.ts` lines | 2,093 | | ≤ 1,400 |
| `frontend/src/styles.css` lines | 2,887 | | record only |
| Admin tabs | 18 | | 7 |
| Roles | 4 | | 2 |

## Baseline gate (main `eb968c1`, before any item)

Pending: baseline backend and frontend gate recorded below.

## Decisions

Items blocked on the user are recorded here with what unblocks them. See the
plan's [Decisions the user owns](production-readiness.md#decisions-the-user-owns).

## Candidates for the user

Code that looks dead but is not on a cut list. Nothing is deleted without a
plan entry; each candidate below is left in place.

1. **The answer Details view has no trace for saved chats.** `entriesFromTurns`
   does not map a `trace`, and `ConversationAssistantTurnEvidence` carries only
   citations, contextBlock and traceId. Only the legacy `/api/query` response
   and the demo chat ever supplied a `RetrievalTrace`, so after P1.2 the toggle
   is present for admins but renders nothing on a saved answer. Giving it data
   needs a per-turn trace (a stored trace or a route), which no plan item lists.
2. **`frontend/tests/browser-workflows.test.mjs` still carries the debug-session
   fixture** (`/api/debug/...` handlers, `debugSessionExpired`,
   `capturedDebugPatches`). P1.9 removes the debug backend; the fixture goes with
   it then.
3. **`api/backoffice.ts` debug helpers and `api/http.ts`'s `setDebugSessionToken`**
   have no caller after P1.2. They are removed with the backend in P1.9.
