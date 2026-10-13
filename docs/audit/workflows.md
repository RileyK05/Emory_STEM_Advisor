# Workflow audit: artifacts, jobs, tools, sandbox, conversations, budgets, and gaps

Reviewed against `AGENTS.md` and `docs/architecture.md` on 2026-10-10. This is a read-only, focused sample of `backend/app/{artifacts,jobs,tools,sandbox,conversations,operations,gaps}`, selected matching API routes and named SQL, and representative backend tests; it is not a complete review of every file in those directories. I did not run tests or exercise a live database, provider, model, HTTP endpoint, or sandbox. The worktree already contained extensive unrelated modifications before this file was added; no implementation files were changed.

The architecture explicitly labels release evidence still outstanding, including production isolation/Office evidence and live batch-provider behavior. I treat those as acceptance limits, not as defects in code. No confirmed wrong behavior was established in this pass. One source-snapshot design question remains.

## Design question

### WF-01 — Define gap-resolution source snapshot semantics

**Status:** Design question; no bug established.

**Question:** Does the resolution contract require every chosen source to remain indexed/current through the resolution commit, or only to satisfy those conditions when the resolution proof query runs? `GapStore.resolve` validates inside the same transaction but does not lock source rows. Under concurrent reindex or origin supersession, state may change between the proof SELECT and commit. The architecture says resolution checks accessible, indexed sources with locators/chunks; it does not explicitly promise they stay current after resolution. This is therefore a snapshot-semantics question, not a confirmed defect.

**Evidence:** `GapStore.resolve` opens a transaction, locks only the gap row, then calls `prove_gap_resolution_sources` and writes resolution history/state ([store.py:218](../../backend/app/gaps/store.py#L218), [gaps.sql:103](../../backend/app/db/queries/gaps.sql#L103)). The proof query filters `status='indexed'` and `is_current`, but has no `FOR UPDATE` on source rows ([gaps.sql:111](../../backend/app/db/queries/gaps.sql#L111)). Ingest replacement deletes/replaces chunks and locators within its own transaction after it locks the source revision, and publication can supersede/activate origin rows ([pipeline.py:127](../../backend/app/ingest/pipeline.py#L127), [pipeline.py:136](../../backend/app/ingest/pipeline.py#L136), [pipeline.py:171](../../backend/app/ingest/pipeline.py#L171)).

**Coverage / reproduction:** Existing tests cover authorization, indexed source requirement, source scope, private scope, and revision checks sequentially ([test_gaps.py:176](../../backend/tests/test_gaps.py#L176), [test_gaps.py:197](../../backend/tests/test_gaps.py#L197)); I found no concurrent resolution-vs-reindex test. If the intended contract requires commit-time validity, reproduce with two DB connections by pausing after the proof SELECT, reindexing or superseding the source, and then resuming resolution. Otherwise, document that resolution captures a point-in-time human attestation.

**Recommendation:** Clarify the point-in-time semantics in `docs/architecture.md`. If commit-time validity is required, lock the proved source rows in deterministic UUID order through commit and add the two-connection regression test.

## Intentional correct behavior (surprisingly careful safeguards)

### WF-02 — Recovery does not blindly repeat uncertain provider calls

**Severity:** Informational; intentional guard.

**Behavior:** Expired ordinary jobs may be reclaimed, but artifact generation distinguishes pre-call work and known-invalid persisted output from an ambiguous call. Reserved/unknown calls become terminal `unknown_outcome`; accounted output without a validated checkpoint becomes terminal `lease_expired`; late exact usage can settle accounting without reopening publication. That avoids duplicate billed work and avoids publishing output from a stale lease.

**Evidence:** The SQL recovery transition accounts reserved calls as unknown and chooses plan/job recovery based on persisted unit/call state ([jobs.sql:227](../../backend/app/db/queries/jobs.sql#L227), [jobs.sql:236](../../backend/app/db/queries/jobs.sql#L236)). The tests exercise unknown reservation, accounted output, late measured usage, and requeue only for pre-call/known-invalid work ([test_artifact_run_recovery.py:44](../../backend/tests/test_artifact_run_recovery.py#L44), [test_artifact_run_recovery.py:79](../../backend/tests/test_artifact_run_recovery.py#L79)).

### WF-03 — Cancellation and publication share a transaction fence

**Severity:** Informational; intentional guard.

**Behavior:** A running job cancellation is cooperative, but publication is protected even if the executor returns late: the publisher and terminal success transition share the job transaction/row lock, and cancellation clears the result. An expired or replaced lease cannot publish.

**Evidence:** `run_once` checks cancellation after execution and delegates publication to `_finish_success` ([worker.py:112](../../backend/app/jobs/worker.py#L112)); `_finish_success` locks the job, checks token, expiry, and cancellation before invoking publisher, then rechecks expiry before success ([store.py:245](../../backend/app/jobs/store.py#L245)). Existing DB tests cover cancellation before publication, a concurrent cancel while publisher is held, and lease-loss fencing ([test_jobs_runtime.py:138](../../backend/tests/test_jobs_runtime.py#L138), [test_jobs_runtime.py:170](../../backend/tests/test_jobs_runtime.py#L170), [test_jobs.py:88](../../backend/tests/test_jobs.py#L88)).

### WF-04 — Budget reservations are conservative when usage is unknown

**Severity:** Informational; intentional guard.

**Behavior:** Calls reserve prompt and bounded output before provider I/O. Unknown/estimated usage does not release capacity; measured usage can replace a reservation, and late exact usage is accepted for a previously unknown attempt once. Publication rechecks persisted operation budget/deadline/workspace fences. This is a budget guard, not a hard dollar ceiling, as the architecture correctly says.

**Evidence:** The controller persists immutable limits, computes the output grant from remaining tokens, and retains reservations for unknown usage ([budget.py:122](../../backend/app/operations/budget.py#L122), [budget.py:155](../../backend/app/operations/budget.py#L155), [budget.py:267](../../backend/app/operations/budget.py#L267)). Artifact and conversation publishers check their operation fence before publishing ([runtime.py:144](../../backend/app/jobs/runtime.py#L144), [worker.py:256](../../backend/app/conversations/worker.py#L256)). Budget tests cover concurrent reservation, measured/unknown accounting, and late measurements ([test_operation_budget.py](../../backend/tests/test_operation_budget.py#L55), [test_artifact_run_recovery.py:97](../../backend/tests/test_artifact_run_recovery.py#L97)).

### WF-05 — Native artifacts use scoped immutable revisions and preserve original imports

**Severity:** Informational; intentional guard.

**Behavior:** Artifact lookups are workspace-scoped; edits use expected-parent compare-and-swap and draft replay checks; import preserves original bytes separately with a digest, while parsed specs are constrained by strict models. Office ZIP bounds, XML parsing, path checks, and unsupported-feature rejection make imports fail closed when content cannot be faithfully represented. Export URLs select an exact revision and return its revision ID in headers.

**Evidence:** Workspace-scoped lookup and CAS are in [storage.py:40](../../backend/app/artifacts/storage.py#L40) and [storage.py:95](../../backend/app/artifacts/storage.py#L95); original bytes are hash-verified on read ([storage.py:259](../../backend/app/artifacts/storage.py#L259)). Import applies the upload cap and stores the original ([artifact_routes.py:75](../../backend/app/api/routes/artifacts.py#L75)); Office archive/path/XML and feature checks are in [safety.py:27](../../backend/app/artifacts/safety.py#L27). Tests cover immutable revisions, unsafe/unsupported Office features, and exact revision exports ([test_artifact_api.py](../../backend/tests/test_artifact_api.py#L23), [test_artifacts_formats.py](../../backend/tests/test_artifacts_formats.py#L111)).

### WF-06 — Formula previews do not imply calculation in the API process

**Severity:** Informational; intentional guard.

**Behavior:** Formula cells remain `needs_recalculation` without an isolated runner. Recalculation output is accepted only if formulas and literal inputs are unchanged and formula cached values are present and non-error; formula PDF preview is withheld unless this verification passes. This does not establish LibreOffice fidelity across real workbooks.

**Evidence:** [xlsx.py:364](../../backend/app/artifacts/xlsx.py#L364) compares recalculated output to the original/spec; [xlsx.py:395](../../backend/app/artifacts/xlsx.py#L395) fails closed when no isolated runner or when verification fails. Offline tests cover missing runner, formula preview, and export state ([test_artifacts_formats.py:335](../../backend/tests/test_artifacts_formats.py#L335), [test_artifact_pdf_preview.py](../../backend/tests/test_artifact_pdf_preview.py#L29)). Architecture separately records that representative Office round-trips and production Office rendering remain unverified ([architecture.md:1326](../architecture.md#L1326)).

### WF-07 — HTML preview/export uses an inert allowlist; executable HTML remains explicitly unfinished

**Severity:** Informational; intentional guard.

**Behavior:** The artifact stores original HTML for editing, but preview/export embeds only sanitized allowlisted markup in a sandboxed iframe with scripts, connections, forms, and nested frames disabled. The code comment records a browser-observed self-navigation issue and explains why CSP alone is insufficient.

**Evidence:** [active_html.py:7](../../backend/app/artifacts/active_html.py#L7) and [inert_html.py:36](../../backend/app/artifacts/inert_html.py#L36) implement the wrapper and allowlist; the format test checks script suppression and CSP ([test_artifacts_formats.py:431](../../backend/tests/test_artifacts_formats.py#L431)). The architecture explicitly says arbitrary JavaScript remains unfinished pending stronger containment/browser evidence ([architecture.md:1081](../architecture.md#L1081)).

### WF-08 — Tool calls are scoped, typed, bounded, and fail closed on ambiguous replay

**Severity:** Informational; intentional guard.

**Behavior:** The registry checks capability, strict input schema, resource IDs, cancellation, strict output schema, and output byte limits. The persistent ledger binds operation/workspace/call/tool/argument/scope identity; completed calls replay, while changed, concurrent, or unknown calls cannot silently execute again.

**Evidence:** [registry.py:118](../../backend/app/tools/registry.py#L118) enforces the execution boundary. [executions.py:69](../../backend/app/tools/executions.py#L69) validates the persistent claim and replay identity; unknown calls raise an indeterminate error ([executions.py:103](../../backend/app/tools/executions.py#L103)). Coverage is in [test_tools.py](../../backend/tests/test_tools.py#L13) and [test_tool_executions.py](../../backend/tests/test_tool_executions.py#L42). Arbitrary code tools are still inactive per [architecture.md:1196](../architecture.md#L1196).

### WF-09 — Sandbox containers constrain network, filesystem, identity, and resources

**Severity:** Informational; intentional guard; deployment evidence remains open.

**Behavior:** The local command is argv-only, uses no network, a read-only root, dropped capabilities, no-new-privileges, an unprivileged user, fixed resource limits, read-only input mount, and scoped output mount. Output collection rejects symlinks, non-regular files, path escapes, and excess bytes/counts. Production configuration requires a separate host plus gVisor; Office conversion independently refuses non-production mode.

**Evidence:** [runner.py:112](../../backend/app/sandbox/runner.py#L112) builds the command; [runner.py:527](../../backend/app/sandbox/runner.py#L527) validates collected output; [types.py:87](../../backend/app/sandbox/types.py#L87) enforces production isolation; [office.py:138](../../backend/app/sandbox/office.py#L138) requires production isolation for Office. Offline command and fake-runtime checks exist ([test_sandbox.py](../../backend/tests/test_sandbox.py#L68), [test_office_sandbox.py](../../backend/tests/test_office_sandbox.py#L70)); they do not prove deployed host separation, gVisor runtime, or Office fidelity. The architecture makes this distinction explicit ([architecture.md:1714](../architecture.md#L1714), [architecture.md:1880](../architecture.md#L1880)).

### WF-10 — Conversation replay and streamed completion retain request and evidence identity

**Severity:** Informational; intentional guard.

**Behavior:** A streamed request atomically binds its fingerprint, immutable user turn, and one-attempt job. Replays with changed input conflict; abandoned or stale completion cannot replace the saved turn. Successful publication rechecks source index revisions, selected profile revisions, operation budget, conversation request state, and job lease/cancellation fences. Event reads expose a whitelist of event fields; provisional text remains non-final.

**Evidence:** Atomic enqueue/replay is in [store.py:149](../../backend/app/conversations/store.py#L149); final publication checks are in [worker.py:206](../../backend/app/conversations/worker.py#L206) and [worker.py:220](../../backend/app/conversations/worker.py#L220). Tests cover request replay, abandon-vs-late completion, source/profile fences, and event scoping ([test_conversations.py](../../backend/tests/test_conversations.py#L20)). Summary generation/use remains disabled pending explicit transfer authorization per [architecture.md:1494](../architecture.md#L1494).

## Scope and limits

This was a focused sample, not a line-by-line audit of every module in the named areas. Files directly inspected included `jobs/store.py`, `jobs/worker.py`, `jobs/runtime.py`, `jobs/artifact_plans.py`, `jobs/provider_batches.py`, `jobs/native_artifact_batches.py`; `artifacts/storage.py`, `service.py`, `safety.py`, `xlsx.py`, `active_html.py`, `inert_html.py`; `tools/registry.py`, `tools/executions.py`; `sandbox/types.py`, `runner.py`, `office.py`, `remote.py`; `conversations/store.py`, `worker.py`; `operations/budget.py`; `gaps/store.py`; selected API routes and matching named SQL; and representative backend tests. I did not exhaustively inspect every module in those directories, auth/OIDC internals, provider adapter correctness, frontend behavior, the general retrieval/generation answer pipeline, or deployment configuration beyond the sandbox isolation gates. No DB race probe or full suite was run. WF-01 is a contract-clarification question, not a confirmed defect.



