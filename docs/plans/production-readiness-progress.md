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
| R3 | todo | | |
| R4 | todo | | |
| R5 | done | R5 | Deleted `frontend/tests/chat-shell-guards.test.mjs`: it tested copies of Shell logic and no npm script ran it. R1's browser test covers the real code. Gate: typecheck and format:check green; no test, build or script referenced the file (npm test's 41 browser tests unchanged at R1). |
| R6 | todo | | |
| R10 | todo | | |

## Phase 1: cut the fat

| ID | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P1.1 | todo | | |
| P1.2 | todo | | |
| P1.3 | todo | | |
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
