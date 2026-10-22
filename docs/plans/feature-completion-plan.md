# Feature completion plan

Reviewed 2026-10-19 against the current working tree, including untracked files.
Requested scope: review every numbered feature in
the feature review (now in git history), find partial implementations, and
prepare work that can be executed later in goal mode. The initial review was
read-only; implementation began after the user activated the goal. Three Luna agents reviewed features 1–9, 10–20,
and 21–30; the coordinator checked the critical findings and ran the checks below.

Use this document for completion status and execution order.
[architecture.md](../architecture.md) remains the design contract. This is a
proposed handoff, not an expansion of the approved release scope.

## Execution progress (2026-10-20)

The inventory and per-feature analysis below describe the initial review baseline.
Use this section for changes delivered during the implementation goal. A package
is not complete merely because its backend repairs passed tests.

| Package | Delivered and verified | Still open |
|---|---|---|
| C0 | Target-authorized record verification; read-only support grant constraints; record/audit HTTP commit boundaries; stable program refresh with retained review/audit history; bounded deterministic allocation and grade/verification fixes; evidence-bound rule grammar; approval-only publication pinned to source/parsed/metadata versions; consistent audit list/detail staleness | The user dev database and live corpus were not migrated or backfilled. Named-program and cohort policy stays out of scope |
| C1 | Exact-evidence summary export with per-turn citation labels, owned revisions and input digest; immutable MD/DOCX delivery; exact memory inspection/reset conflict handling; single applicability notice; applied date filters shared by audit table and export; corrected cost/cache accounting and batch tariffs; Main/Admin/Debug browser checks | Repository branch-protection settings were not observed. A provider invoice was not reconciled |
| C2 | Four-role session contract; auditor Audit/Usage UI; workspace read/write route gates and worker publication role/status fences; current scoped flag policy in query/research/tools/repair/compaction/artifact execution; direct memory/summary endpoint enforcement; rollout/cohort Admin UI; conflicting-edit protection and atomic policy/audit writes; terminal policy denials. Support-grant request, approval, revoke, expiry, and in-flight publication denial are on the API, the worker fence, and the Admin UI, including loading, empty, error, stale-edit, and retry | Production prompt-promotion authority stays with C10. Evidence: `c2-grants.txt` (7 passed, including cross-owner denial) and both browser runs |
| C4 | Invalidation lease/consumer, gap evidence ineligibility, historical source-revision text, reviewer source/program UI, and reimport retention of a saved release and audit | None in this slice. Evidence: `c4-review.txt` (22 passed) |
| C5 | Immutable academic-record revisions, digest-pinned CSV commit, stale preview retention, server-forced manual provenance, and the HTTP record screen | Real student records stay behind the C3 policy and infrastructure gates. Evidence: `c5-records.txt` (5 passed) |
| C6 | Program-audit list, detail and export share one staleness view after a source index change. The screen covers loading, empty, error, stale and retry. Coverage is `program_audit` | Full named-program and cohort policy remains out of scope. Evidence: `c6-audit.txt` (22 passed) |
| C7 | Comparison scenarios pin a record revision and two or three releases. Overlays and mention overlap do not become saved credit or a new attempt. Unequal unresolved coverage blocks an ease ranking. Cancelling a catalog refresh leaves the published edition, source and topic build current | Remaining-term estimates stay gated on an offerings contract. Corpus regeneration is not performed by the refresh batch. Evidence: `c7-c11.txt` |
| C8 | One current feedback vote per owner and response. Triage records issue type and reviewed transitions; missing evidence on a negative vote opens a gap and does not write a course or prerequisite fact. Promotion deduplicates a redacted candidate, rejects another collection's trace and student history, and requires explicit re-review after a source index change. The admin screen covers loading, empty, error, stale note, and retry | Legal retention duration for withdrawal is not decided. Evidence: `c7-c11.txt` |
| C9 | A debug experiment has its own id. Saved-request replay does not create one and does not append the experiment to the student conversation. Pending or blank historical input is not replayed. The stored snapshot omits secret fields. The admin screen keeps the conversation id across the 409 and retries | A live model rerun and a full retrieval-timeline screen stay out of this slice. Evidence: `c7-c11.txt` |
| C10 | Prompt text is an immutable version. Promotion returns 409 until a human approval and a current eval fixture both exist, and it records no model call. The admin screen keeps the draft across both 409s and publishes only after the fixture is saved | Production promotion authority and suite thresholds remain an external gate. Evidence: `c7-c11.txt` |
| C11 | Known priced calls still reconcile (including a partial 0.012 total beside one unknown call). An all-unknown call set stays null rather than zero. The usage headline says Unknown when any call is unpriced and says that cache metrics are not answer quality | A reconciled provider invoice is not available here. Evidence: `c7-c11.txt` (8 passed for the C7–C11 selection) |
| C3 | Owner export inventories records, imports, audits, feedback, and jobs. The download token is returned once and omitted from later metadata. Download requires the owner and expires. Deletion fences, removes local originals, and resumes without writing them back, including when the original path exceeds the legacy Windows path limit. A zero quota stays zero. A rejected stream start does not charge. An admitted request settles one charge, including when its provider outcome is unknown, and a replay does not charge again. A failed alert probe stays unknown and the next probe still reads. The privacy screen covers loading, empty, error, and retry | Remote blob storage, private networking, deployed alert delivery, legal retention, and real student data remain external gates. An in-memory blob adapter is not remote storage. Evidence: `c3.txt` (22 passed) and `test_generation_retry_settles_quota_once` |

Additional C0/C1 repairs found during execution:

- Manual record edits cannot claim institutional or transcript provenance.
- Empty/informational-only rules and repeated attempts cannot produce vacuous
  completion; unverified/planned work is conditional and unsupported grade
  equivalences or search exhaustion remain unresolved. Alternatives are allocated
  together, including five-or-more-course credit thresholds and larger programs.
- Source changes and program retirement invalidate pending approval/publishing,
  not only already published releases. Historical review approvals keep their
  original actor/time and are not rewritten as the publisher.
- A queued summary previously wrote an artifact before job publication and then
  returned an incompatible result type. It now pins a saved-turn boundary and
  stages one document for cancellation/access-checked atomic publication.
- Compaction calls now appear in the cost ledger. Native batches require a batch
  tariff and keep the submission pricing time. Missing cache reporting is an
  estimate when it changes cost; mixed currencies are not summed into dollars.

Verification on 2026-10-20, after the long-path, OpenAPI, profile-fence,
DA-03 ranking, fusion source-budget, and XLSX import fixes, is recorded under
the goal scratch directory `implementer`. This run replaced the earlier 1,173
figure.

- Backend: `pytest.txt` reports **1,176 passed, 2 skipped** in 221.74s, basetemp
  `data/test-runs/pytest-full-quota-20261020`. The skips are the Windows symlink
  case and the POSIX permission case. No database test was skipped. `ruff.txt`
  reports all checks passed. The suite includes the DA-03 ranking change, the
  fusion budget regression, and the quota charge that commits only with an
  admitted stream. `test_generation_retry_settles_quota_once` shows a missing
  source and a stale sequence leave the daily bucket at 0, then that request
  charges once and a replay stays at 1.
- Frontend, re-run after the quota admission fix: `frontend-typecheck.txt`,
  `frontend-format.txt`, and `frontend-build.txt` each exit 0. `frontend-test.txt`
  reports 99 passed and 0 failed. `c2-browser-1.txt` and `c2-browser-2.txt` each
  report 56 passed and 0 failed, including grant approval/revoke, auditor
  read-only behavior, the privacy export, and the citation, provider, and new-chat cases.
  The privacy list fixture holds its response until the loading text is visible,
  so a fast failure cannot skip that state.
- Disposable migration, re-run with those frontend logs: `migrate-032-head.txt`
  applies baseline `032_schema.sql` through `068_quota_settlements.sql` on
  disposable directory `data/test-runs/migrate-032-c497e420` (head 68, 37
  versions, exit 0). The user dev database was not used. `schema-parity.txt`
  says the baseline and legacy catalogs match and exits 0. Together with
  `ruff.txt` (all checks passed), `pytest.txt` (1,176 passed, 2 skipped),
  `audit-regression.txt` (19 passed), `frontend-typecheck.txt`,
  `frontend-format.txt`, `frontend-build.txt`, `frontend-test.txt` (99 passed),
  and both browser logs (56 passed each), this is the current verification set.
- Audit re-triage: `docs/audit/README.md` records the same dispositions.
  `audit-regression.txt` reports 19 passed. DA-03 is closed in the keyword
  ranking path: a set whose best raw score is below 0.05 stays absolute so the
  fusion floor can drop it, one exact term stays citable, and a longer topical
  token ranks in [1, 2) above repeated shorter words. "work", "first", and
  "fit" were not added to the stop list. A varied live comparison was not
  re-run. A cited-set budget that already covers every source keeps each source
  (`test_full_source_budget_keeps_every_covered_source`). A sparse sheet imports
  only its declared cell, and a legacy DDE formula is refused on import and
  export. DA-04 rejects a course code that is absent from the cited passage and
  does not use a model to judge free-text entailment. DA-08 is a non-defect.
  WF-01 stays a point-in-time attestation plus the invalidation outbox, without
  source-row locks. The worker lifetime was inspected in `scripts/worker.py`;
  a process regression was not added because it would start a second embedded
  database or load the model handler.

Documentation cleanup on 2026-10-20 aligned `AGENTS.md`, `README.md`, the
migration README, the architecture contract, and the older implementation
handoff to the current head (069), the four session roles, and the delivered
record/audit/review/privacy/flag/telemetry surfaces. Trailing blank lines were
removed and the tree passes `git diff --check`. No application behavior changed
in this cleanup.

C12 stays gated. Verified semester plans need an offerings contract. Advisor
caseload needs assignment authority, consent, duration, and revocation. Duplicate
and conflict curation, bulk user operations, and translated locales stay
separately accepted work. Basic keyboard, name, error, and focus behavior for
shipped screens stays required. C13 / feature 25 stays deferred. Branch-protection
settings for feature 29 were not observed. The user dev database, live providers,
school infrastructure, and remote repository were not changed. Migration head on
disk remains `068_quota_settlements.sql`. No commit, push, or deploy.

## Main finding

The concern about features being halfway done is supported by the code. Many
new domains have migrations, services, response types, and narrow tests, but
lack the frontend workflow or the production call sites that enforce them.
Several completion claims also describe stronger behavior than the code provides.
Keep the useful implementations and finish them through the full request, job,
storage, permission, and UI paths; do not restart these subsystems from scratch.

The most consequential examples are:

- Academic records and degree audits have registered backend APIs, but no
  student client methods or screens. Records have a revision counter without
  immutable historical record snapshots. The audit engine also has correctness
  defects, including a reproduced grade-check crash and false satisfaction.
- Source/program review has backend actions but no reviewer UI. Source changes
  write an invalidation outbox without a discovered worker consumer.
- Flags and quotas can be configured and queried, but their services are not
  connected to the actual feature execution/admission paths.
- The Blob adapter is an in-memory dictionary even when Azure connection
  arguments are supplied. Runtime file consumers still use local paths.
- Privacy exports have metadata/token APIs but no download endpoint; deletion
  and export inventories do not cover all newly added student domains.
- Alert rules can be evaluated locally, but spend/storage metrics remain zero
  placeholders and deployed alert delivery is missing.

### What counts as complete

A feature is complete only when its named scope passes all applicable gates:

1. A user with the intended role can finish the workflow through the real UI
   and HTTP client, including loading, empty, error, stale-edit and retry states.
   Mock mode either models that behavior or explicitly explains unavailability.
2. The server enforces ownership/capabilities, scope, validation and the feature's
   real invariants on every relevant API and worker path. Hiding a button is
   insufficient. Management endpoints do not prove runtime enforcement.
3. Persistence, revision snapshots, cancellation, idempotency and transaction
   boundaries work together. Jobs survive restart without duplicate publication
   or unknown provider outcomes being blindly repeated.
4. Evidence, provenance, applicability and stale-result behavior remain truthful
   in the UI and downloads. Historical citations remain addressable.
5. Required behavior checks pass, including real Postgres where SQL is involved
   and browser behavior where a browser is needed. Contract/type checks alone
   do not establish completion.
6. Configuration, worker scheduling, deployment, migration/backfill, cleanup and
   operational ownership exist where required. Update the architecture and API
   contract in the implementation slice, and record any remaining release gate.

Track backend, API/client, UI, worker/runtime, data migration, tests and release
evidence separately in future work. A feature-level checkbox closes only when
all required parts close. A bounded first release and a complete original
feature are separate milestones; e.g. CSV delivery does not complete PDF import.

## Current feature inventory

"Near completion" means substantial integration exists and needs the listed
closure checks. It does not mean verified released. "New" means no dedicated
end-to-end feature was found; reusable foundations may exist.

| # | Feature | Current status | Completion package |
|---|---|---|---|
| 1 | Academic record / transcript | Partial backend; missing UI and immutable revisions | C5 |
| 2 | Deterministic audit | Core defects repaired; policy, immutable inputs and audit UI remain | C0, C6 |
| 3 | What-if comparison | New; mention comparison is a different capability | C7 |
| 4 | Semester planning | Data-gated; deterministic sequence primitive exists | C12 |
| 5 | Summary export | Direct export verified; queued publication closure in progress | C1 |
| 6 | Memory inspector/reset | Exact pointers, reset paths and browser workflow verified | C1 |
| 7 | Freshness/applicability | Ordinary/research notice integrated; duplicate removed; future audits/scenarios pending | C1, C6, C7 |
| 8 | Feedback/triage | New | C8 |
| 9 | Advisor caseload/sharing | Product-gated; grant foundation only | C12 |
| 10 | Catalog refresh approval | New workflow over existing importers | C7 |
| 11 | Source lifecycle UI | Partial backend; no reviewer UI/worker consumption | C4 |
| 12 | Program requirement review | Safe grammar/publication base repaired; reviewer UI pending | C0, C4 |
| 13 | Duplicate/conflict detection | New; later scope | C12 |
| 14 | Gap resolution to eval | New bridge; gap review already exists | C8 |
| 15 | Roles/support grants | Partial; enforcement and surfaces inconsistent | C2 |
| 16 | Retention/export/delete | Partial; incomplete inventory/delivery/cleanup | C3 |
| 17 | Cohort feature flags | Runtime and Admin rollout UI connected; versioned decision manifests pending | C2 |
| 18 | Audit filters/export | Shared applied date filters and browser export workflow verified | C1 |
| 19 | Usage/quality dashboard | Cost/cache subset exists; quality/attribution missing | C11 |
| 20 | Bulk user operations | New; later scope | C12 |
| 21 | Replay/trace timeline | New execution workflow; saved-result replay exists | C9 |
| 22 | Prompt versions/promotion | New registry; constants/temporary overrides exist | C10 |
| 23 | Eval suite UI | New managed workflow; CLI scorer exists | C10 |
| 24 | Cache/cost telemetry | Accounting/cache/currency/batch repairs verified; deployment prices operational | C1, C3 |
| 25 | Model/config comparison | Explicitly deferred | C13 |
| 26 | Blob/private networking | Storage scaffold/stub; release gate open | C3 |
| 27 | Student quotas | Management foundation; generation disconnected | C3 |
| 28 | Operational alerts | Local evaluator only; release gate open | C3 |
| 29 | Admin/Debug required browser CI | Coverage/workflow present; execution/settings unverified | C0, C1 |
| 30 | Accessibility/i18n | Some accessible controls; broad pass/localization missing | Every UI, C12 |

## Correctness repairs before expanding the audit pilot

These are concrete findings, not reasons to discard the surrounding code.

| Finding | Evidence | Required repair |
|---|---|---|
| Verification lacks target authorization | `records/service.py:420` checks `VERIFY_ACADEMIC_RECORD`, then uses the supplied workspace/attempt without a resource-grant check | Require both verification authority and target/resource authorization before any read/create/write; test an advisor without a target grant |
| Grade thresholds crash | `audit_engine.py:260` passes `att.grade` to `_grade_satisfies`, which expects an `EvaluatedAttempt`; existing grade test fails | Repair the contract and table-drive missing/unknown/pass-fail/threshold grades against reviewed policy |
| Empty course requirement can pass | A leaf `ALL_OF` with `course_codes=("CS 170", "CS 171")` and no attempts returns `satisfied`; draft generator creates this shape | Normalize rules into explicitly supported structures and reject unsupported shapes; no default-success fallback |
| Unverified facts can satisfy | An unverified completed CS 170 returns overall `satisfied`, with unverified credits merely reported separately | Separate authoritative satisfaction from conditional student scenarios at rule and overall levels |
| Credit allocation can miss valid solutions | 15-credit rule plus five verified 3-credit courses returns outstanding with only 12 credits allocated | Remove the fixed four-course allocation; solve supported credit/allocation semantics with explicit bounds |
| Search is described more strongly than implemented | `_solve_allocation` tries the first and shifted candidate slices and scores allocated course count | Use a complete bounded search for the supported rule language, stable objective/ties, and explicit unresolved-on-limit behavior |
| Historical inputs are not reproducible | Migration 052 stores mutable attempts and a revision integer; 053 stores record revision/attempt IDs, without an immutable attempt snapshot | Persist immutable record revisions and audit input manifests before relying on saved audits |
| Program refresh can erase saved releases/audits | `programs/importer.py:53` deletes collection programs before reinserting; migrations 051/053 cascade program deletion into rule releases and audits | Preserve versioned program identities/history and remove destructive publication ownership in an additive migration; prove identical reimport and replacement retain saved results |
| Unknown rule semantics become supported drafts | `programs/rules.py` infers all-of/any-of from mentions and a simple word check; one mention also becomes an exact-course requirement | Keep ambiguous clauses unresolved; reviewed executable semantics must cover the full raw clause, including qualifications |
| Invalidation is unfinished | `source_review.py` inserts `source_invalidation_outbox`; search found no worker consumer | Implement leased/idempotent consumption and synchronous invalidity checks at dependent reads/publication |

The three small synthetic audit probes above were terminal-only pure-code
checks. Add their cases to the existing degree-audit/rule tests during repairs.
Additional source-level concerns to resolve include zero-credit values being
replaced by truthy defaults, repeat selection discarding relevant attempts,
duplicate node IDs, policy-free pass/fail conversions, nested choice allocations,
and stale source/cohort eligibility. These need behavior cases, not assumptions
that fixing the first crash establishes correctness.

Re-triage DA-01/02/06, UI-03, RT-01/02 and WF-01 against current code and actual
persisted data. Migrations 034/035 and recent pool changes already address parts
of the older findings. Do not reimplement them blindly or claim the corpus was
regenerated merely because a parser changed. Verify split boundaries, canonical
deduplication before quotas, compatible topic-build publication, exact-ID source
navigation, one-slot pool behavior, and evidence-dependent review invalidation.
Use the existing [audit status](../audit/README.md).

## Completion work by feature

### 1. Academic record and transcript import

**Reuse:** `app/records/{service,importer,types}.py`, registered
`api/routes/records.py`, migration 052, `academic_records.sql`, API/TS types and
`test_academic_records.py`. Manual CRUD, CSV preview/commit and staff verification
are present on the backend. There is no matching student screen or client flow.

**Finish:** Add immutable record/attempt revisions with expected-revision edits,
deletions and verification; current data can remain a projection. Preserve repeat
attempts rather than merging them away. Validate finite credits, zero values,
unknown grades/statuses and submitted versus institutionally resolved identity.
Do not accept client provenance as institutional verification. Store import
originals privately with retention rules; current batches retain raw CSV in SQL.
Add typed `AdvisorClient` methods, HTTP/mock behavior and a records screen with
manual editing, CSV staging, row corrections, row selection/rejection, duplicates,
verification badges and conflict recovery. Commit the reviewed preview digest,
not an independently reinterpreted upload. Add staff verification only within
the capability/grant policy from 15. Repair the current verification path's
missing target authorization before exposing it to staff: a verification
capability by itself does not authorize any student's record.

**Done when:** A synthetic student can enter/edit/import/review records in the
browser, resolve a stale edit without losing a draft, retry import without
duplicates, and retrieve an old record revision. Edited facts lose verification;
unknown mappings cannot satisfy authoritative audits; another user cannot read
the record or original. Real data requires 15/16 and the deployment's storage
controls. PDF extraction remains a separate explicit milestone: supported formats
to the same staging model with page/row evidence, unsupported scans to manual
review, and no automated equivalency decisions.

### 2. Deterministic program/degree audit

**Reuse:** `advising/audit_engine.py`, `audit_service.py`, `api/routes/audits.py`,
rule releases from 12, migration 053 and `test_degree_audit.py`.

**Finish:** Repair the defects above before adding presentation. Define the
supported rule grammar and reviewed grade/repeat/credit/sharing policy. Normalize
containers and leaves; validate node IDs, source spans, numeric bounds and clause
coverage. An allocation must satisfy the rule semantics, including nested choices,
instead of maximizing raw assignment count. Return unresolved if a configured
search bound prevents a conclusion. Separate completed verified, completed
unverified, in-progress and planned contributions. Never silently resolve an
unknown predicate, exception, equivalent course or minimum-grade policy.

Pin an immutable record revision, rule release, source/index/metadata revisions,
catalog/cohort, identity mapping, policy and engine versions in each audit. Check
the selected program belongs to the authorized collection/scope. Read inputs in
a short coherent transaction, solve without a held pool borrow, and fence result
publication or mark the captured result stale. Provide stable idempotency for
starts and job execution when necessary. Add client/UI to select an applicable
program, inspect each rule/allocation/evidence/blocker, reopen saved results and
recompute stale ones. List/detail/export must agree about staleness; source
changes matter even if `current_rule_release_id` has not changed yet.

**Done when:** Existing tests plus cases for false satisfaction, alternatives,
repeats, zero/fractional credits, missing policy, shared-course competition and
solver limits pass. A user completes the record → reviewed rules → audit → saved
result workflow through HTTP/browser. Old results remain intelligible after
record/source changes. Call the pilot a **program audit** until school/core,
residency, GPA, total-credit, track and exception coverage justify degree claims.

### 3. Program comparison and what-if

**Reuse:** The audit engine once corrected. Existing `advising/comparison.py`
explicitly implements `mention_overlap_only`; preserve that narrower contract.

**Finish:** Add immutable comparison scenarios over one record revision and 2–3
applicable program releases. Hypothetical additions/removals are overlays, never
record edits. Persist audit IDs and all input/overlay digests. Compare supported
requirements and unresolved coverage visibly; distinguish independent audits
from a combined-program allocation with evidenced double-count rules. Add API,
client, mock behavior and comparison UI with links to original rules/evidence.

**Done when:** Scenarios cannot mutate the academic record, shared mentions never
become claimed credit savings, unknown sharing policy stays unknown, and unequal
rule coverage cannot make a program look easier. Remaining-term estimates wait
for 4; no unsupported graduation date or universal completion percentage.

### 4. Semester plan builder

**Reuse:** Pure `advising/sequence.py` and deterministic requisite trees.

**Finish:** Deliver saved sequence/staging plans first if desired, with explicit
alternative choices and blockers. Verified term placement waits for an approved
offerings contract: term/course/section identities, credits, availability status,
source provenance and refresh cadence. Meeting-time conflicts need meeting data.
Add versioned offerings/constraints, a bounded deterministic scheduler and
immutable plan revisions; validate every drag/edit against loads, prerequisites,
corequisites, selected audit allocations and student constraints. Distinguish
infeasible from unsupported or search-limit outcomes. A manual term assignment
without offerings is an unverified scenario.

**Done when:** A user can build/save/reopen a plan and receive specific violation
messages; changed offerings/records/rules mark affected plans stale. Verified
terms have actual offering support. Seat availability and registration writes
are outside scope; dependency stages never become verified semesters by renaming.

### 5. Advising summary and export

**Reuse:** `artifacts/advising_summary.py`, conversation summary-export route,
`HttpClient.exportSummary`, Shell action, existing native document/export system
and `test_advising_summary.py`. This is already an integrated first slice.

**Finish:** Remove the generated document's unsupported "Official advising
summary" claim. Define an explicit snapshot manifest: published sequence, cited
evidence, decisions, selected immutable artifact revisions, and later audit/plan
IDs. Distinguish student goals, generated recommendations and verified results.
Validate the real saved-turn evidence shapes for ordinary and research answers;
missing pointers become disclosed omissions, not silently dropped citations.
Associate the created artifact with its conversation in the normal library/panel
flow. Preserve citation numbering/legend and revision-specific Markdown/DOCX
download behavior. Add explicit next steps and open questions without turning
absence of recorded blockers into proof of completeness.

**Done when:** Browser action produces an editable saved artifact and readable
export with exact references; pending/failed turns are excluded; later chat edits
cannot rewrite it. Reopening/downloading the same revision returns the same
saved content. Research judgments and conditional audits retain their labels.

### 6. Conversation memory inspector and reset

**Reuse:** `MemoryDialog.tsx`, summary/memory routes, migration 044, reset lock and
context epoch, boundary-filtered conversation/research queries and existing reset
tests. Both UI and backend exist; this is a closure task, not a rewrite.

**Finish:** Review all context inputs as one inventory: raw history, compaction
checkpoints, warm citations, last advising scope, research checkpoint, paged
conversation archive and recalled evidence handles. Queries already contain
boundary filters; prove each execution path uses them, including old cursors and
queued request snapshots. Show covered turns, omitted history, exact pointers and
separately labelled research notebook. Clarify reset affects future model context,
while privacy deletion belongs to 16. Preserve lock/epoch fencing for pending
turns/summaries and late completion; disclose explicitly reselected evidence.

**Done when:** Ordinary, compacted and research follow-ups cannot recover
pre-reset context indirectly; saved history remains owner-readable. Two sessions
and a pending-worker/reset race cannot republish forgotten context. Cross-owner
reads fail and reset errors preserve the dialog state. Extend the existing reset,
research-workspace and browser coverage where each behavior lives.

### 7. Source freshness and applicability

**Reuse:** `advising/applicability.py`, query-service notices and
`ApplicabilityBanner.tsx`. `MessageView.tsx` currently renders the banner twice.

**Finish:** Remove duplicate rendering. Define one notice builder from pinned
evidence with edition, cohort/program applicability, status and missing metadata.
Use it for ordinary/refused answers where relevant, saved/research answers,
audits, comparisons, plans and exports. Preserve the notice snapshot on history;
show a separate current freshness assessment instead of rewriting past evidence.
Distinguish a superseded edition from rules that remain applicable to an earlier
cohort. Missing dates or unknown applicability cannot produce an all-clear.

**Done when:** Synthetic current/historical/cohort-conflict/unknown cases render
once, agree across result and export, and link to exact source identity. A source
replacement changes freshness without falsely invalidating all older cohorts.

### 8. Student feedback and triage

**Reuse:** Published response/trace identities and the existing gap review UI.

**Finish:** Add owner-scoped idempotent feedback revisions tied to a published
response/refusal, reason code, optional bounded comment and original scope/trace.
Add answer controls and a capability-gated metadata-first triage surface. Route
missing-evidence issues to gaps explicitly; provider, explanation, privacy and
UI failures remain different issue types. Record reviewed transitions and expose
feedback correction/withdrawal under retention policy. A negative vote creates
an investigation, not a new institutional fact.

**Done when:** Changing a vote does not inflate totals, another student cannot
rate/read the response, triage content needs authorization, and feedback survives
the original result becoming stale with its original provenance intact. Promotion
to a shared eval requires the deliberate redacted snapshot from 14.

### 9. Advisor caseload and shared plans

**Gate:** Decide assignment authority, consent/legal basis, access duration,
revocation and advisor edits versus recommendations. Requires 15/16; adding an
advisor role does not deliver a caseload product.

**Finish:** Add assignments separately from resource grants. Let students see
who can access a named record/plan revision or optional conversation, consent,
expire and revoke. Add advisor caseload/preparation UI with only authorized
resources. Check access on reads, downloads, job starts and result publication;
revocation must stop continued access and new publication. Keep advisor proposals
separate from student-owned plans and institutionally verified facts.

**Done when:** Assignment alone grants no transcript access; expired/revoked
grants fail on deep links and exports; student and advisor see the same scope.
Access and changes are audited. Calendar/SIS integration is not implied.

### 10. Catalog refresh diff and approval

**Reuse:** Cached scrape/import pipelines, exact origin manifests, current-source
publication, graph snapshots and the review primitives in 11/12.

**Finish:** Add a refresh batch with immutable edition/source manifest and staged
courses, aliases, prerequisites, programs, rule drafts and topic inputs. Compute
deterministic additions/removals/changes/conflicts with raw-source links. Give a
reviewer an impact summary and explicit approval against the expected published
revision. Prepare parser/embedding/graph work outside the publication transaction.
Switch compatible current pointers together, or explicitly retire an incompatible
topic graph until rebuilt. Persist review decisions and consume invalidation
events for dependent rules, audits, plans, gap reviews and evals. Add job progress,
cancel/retry and reviewer UI; retain old evidence.

**Done when:** Failed/cancelled staging leaves the previous edition usable;
concurrent approval cannot mix editions; removal does not destroy historical
citations or topic snapshots; a batch can be reviewed and published through UI.
Corpus regeneration/backfill is origin-aware and verified separately from code.
Respect cached acquisition and stop on WAF challenges; no bypass.

### 11. Source and provenance lifecycle UI

**Reuse:** `admin/source_review.py`, admin detail/impact/proposal/approve/reject
and lifecycle endpoints, migration 050 and `test_source_review.py`.

**Finish:** Add typed backoffice methods and a reviewer surface for exact source
revision, verified classification, title/institution/unit, roles, applicability,
proposed changes, impact and revision diff. Preserve untrusted upload versus
curator-verified origin. Approval/publication uses expected metadata revision and
records who checked what. Wire source lifecycle status into actual retrieval and
research eligibility; an isolated status field is insufficient. Add the outbox
consumer with fencing/retries and visible pending/failed propagation. Make stale
dependent reviews/results ineligible immediately even if propagation is delayed.

**Done when:** Reviewer can propose/reject/approve/deprecate/replace in-browser;
stale approvals fail without lost drafts; affected results show truthful state;
historical evidence remains accessible. Ordinary uploaders cannot self-classify
as institutional sources. Keep source metadata review distinct from catalog
batch publication in 10.

### 12. Program requirement review and executable releases

**Reuse:** `programs/review_service.py`, `rules.py`, admin release endpoints,
migration 051 and existing program/rule tests.

**Finish:** Render raw requirement lines, exact source spans, course mentions,
parser status, executable tree and coverage disposition side by side. Provide a
bounded rule editor and draft/diff/reject/approve/publish actions. Repair default
draft generation and validation: ambiguous mentions remain unresolved, every
substantive clause is represented, evidence points to the bound source revision,
node IDs/shapes are valid and no generic all-of leaf silently succeeds. Waivers
are authorized student exceptions, not a way to omit catalog rules globally.
Decide reviewer/publisher separation explicitly; current publish accepts draft
or approved status, so do not assume independent approval is already required.
Serialize concurrent release publication and check source/cohort applicability.
Repair the current delete-and-reinsert importer and cascade ownership before
reimporting a reviewed program: even an identical reimport must retain its saved
releases, reviews and audits. Version the projection and retire current pointers
without treating publication as permission to erase evidence.

**Done when:** Reviewer can release a small named program/cohort through UI with
complete clause accounting. Unsupported predicates remain blockers in audits.
Source replacement or stale draft revision prevents an old approval being used.
Published trees and their evidence stay immutable and inspectable; no model
authors executable rule semantics or prerequisite facts.

### 13. Duplicate and conflict detection

**Reuse:** Origin keys, content digests, provenance, canonical course identity and
edition/applicability metadata. Current duplicate filenames are not a conflict
resolution workflow.

**Finish later:** Build deterministic candidate detection for exact duplicates,
same-origin revisions, suspicious same-name sources, overlapping applicability
and inconsistent reviewed rule facts. Return explanations/evidence and confidence
categories; text similarity can nominate review, not decide authority. Add a
curator queue with explicit keep/replace/link/dismiss actions, revision fencing
and impact preview. Never merge sources because filenames match.

**Done when:** Candidate dismissal is stable for unchanged inputs, changed inputs
reopen review, and no automatic action deletes evidence or decides policy precedence.

### 14. Gap resolution to eval case

**Reuse:** Existing human gap resolutions, trace/evidence records and CLI scorer.

**Finish:** Add an explicit reviewed promotion action that prepares a redacted
immutable candidate: normalized query, expected evidence IDs/revisions, negative
expectations, original scope and resolution rationale. Separate student-private
origin from the shared candidate; require authorization and redaction review.
Reject stale depended-on resolutions or clearly freeze a reviewed historical
fixture. Deduplicate promotions by candidate digest. Cases join a suite only
after 23's review workflow.

**Done when:** A gap/feedback item can create one reproducible candidate without
copying student history into shared fixtures; source changes invalidate pending
promotion or explicitly require re-review. Wrong query/trace ownership is rejected.

### 15. Finer roles and support access

**Reuse:** `auth/capabilities.py`, migration 045, grant endpoints, last-admin
protection and existing roles/grants tests. Grant checks currently concentrate in
records/audits and an Admin transcript path; Admin largely retains admin-only
dependencies/UI gates.

**Finish:** Define an operation-to-capability matrix covering Main/Admin/Debug,
audit export, telemetry, source/rule review, conversations, artifacts, research,
records, privacy, tools and worker result delivery. Apply it server-side and let
the UI reflect effective permissions. The auditor should reach allowed audit
views without gaining mutations. Time-boxed support grants remain read-only;
advisor verification needs its own explicit authority. Add request/reason,
approval, resource/action scope, expiry and revoke screens plus audited reads.
Check role changes/disable/revocation against active sessions and background
operations. Establish behavior for dev/local mode separately from institutional
authorization; ownership alone must not accidentally give read-only roles writes.

**Done when:** Role/resource/action tables drive behavior tests through HTTP and
jobs, including expiry/revocation mid-operation, self-approval policy, last-admin
races, direct download URLs and cross-owner requests. No route trusts a supplied
workspace key as authorization or relies solely on the navigation gate.

### 16. Retention, student export and deletion

**Reuse:** `privacy/service.py`, registered `api/routes/privacy.py`, migration 046
and current inventory/deletion tests. Present export creation is synchronous;
metadata includes a download token but there is no delivery route. The inventory
predates records/audits and lacks complete originals/backup coverage.

**Finish:** Agree policy before real records: per-domain retention, export scope,
holds, institutional audit minimums and backup expiry. Inventory conversations,
research, records/import originals, audits/plans/scenarios, artifacts/exports,
jobs/batches/tool ledgers, feedback, private sources/files and operational traces.
Implement resumable export/deletion jobs with progress, retries and idempotency.
Export a checksummed bundle through owner-authenticated expiring download; do
not expose raw bearer tokens in general metadata. Add student privacy UI.

Fence new work, revoke sessions/grants, cancel and fence workers, delete dependent
SQL/object data in reviewed order, retain only the permitted tombstone/audit data,
and verify cross-owner/shared institutional resources survive. Use a retention
scheduler/worker, hold checks, failed-item reconciliation and object cleanup.
Restore must reapply deletion tombstones before serving restored data; backup
expiry and retained copies must be explicit. Keep the existing restore/backup
contract and extend it rather than inventing a separate recovery mechanism.

**Done when:** A user can download a complete verified export, tokens expire,
another user cannot download it, deletion resumes after partial failure and old
jobs cannot resurrect content. New records/audits are covered. Holds and restore
behavior are exercised. Do not call a synchronous selected-table deletion a
complete retention policy or claim legal compliance from code alone.

### 17. Feature flags and cohort rollout

**Reuse:** `flags/service.py`, flag/cohort admin routes, migration 047 and tests
for precedence/environment ceilings. Current effective-flags endpoint is not
runtime enforcement.

**Finish:** Wire the central evaluator into each intended admission path:
research, planning, tools, compaction and chosen prompt rollout. Pin effective
flag/prompt versions in job snapshots; define which kill switches stop already
admitted work and check them at safe worker boundaries. Environment settings stay
upper bounds. Keep all configuration in `config.py`; remove direct environment
fallbacks that bypass the settings seam. Add backoffice/client/UI for cohorts,
overrides, effective decisions and reasons. Define cache invalidation, precedence,
failure behavior and privacy-safe cohort membership.

**Done when:** A flag change changes actual HTTP/worker behavior in a scoped
integration test; disabled features cannot be invoked directly or via tools.
Conflicting cohorts and a global kill switch behave consistently, and no override
raises deployment/security ceilings. UI can explain an unavailable feature.

### 18. Audit browsing and export

**Reuse:** Admin filter/keyset/export APIs, named queries, Admin audit tab and
browser coverage. Most of this feature is present.

**Finish:** Expose API date bounds in UI and use one filter state for both table
and CSV/JSON export. Preserve keyset order/snapshot bounds, validate dates/cursors
and handle expired/invalid cursors. Confirm exports are bounded, authorization is
capability-based, and export operations themselves are audited. Neutralize CSV
formula injection in user-originated fields without damaging the JSON data.

**Done when:** Actor/action/target/date combinations paginate/export consistently
under concurrent inserts; empty/error cases recover; auditor access is correct.
Extend existing tests rather than create a second audit-browser suite.

### 19. Usage and quality dashboard

**Reuse:** Existing cost/cache Admin tab and telemetry endpoints from 24.

**Finish:** Add trustworthy refusal/gap/latency/feedback and quality measures with
versioned definitions, time/model/collection filters and sample counts. Attribute
programs only from explicit pinned program scope; queries without reliable
attribution remain unknown, and multi-program totals must not double-count fleet
usage. Use privacy-safe aggregation/minimum cohort sizes, bounded queries and
indexes or rollups after measurement. Add dashboard views/drilldowns whose content
reads still require permission. Feedback depends on 8; regressions depend on 23.

**Done when:** Known fixture operations reconcile to totals without duplicated
calls or excluded unknown costs disappearing. Filters and denominators are
visible; a small cohort cannot expose student query text. Cache metrics are not
presented as answer quality.

### 20. Bulk user operations

**Reuse:** Single-user role/status/session operations and last-admin protections.

**Finish later:** Add CSV preview/validation with explicit institutional identity
matching, duplicate/unknown rows, intended role/cohort/status changes and impact.
Commit a reviewed digest idempotently; define all-or-nothing versus per-row
outcomes by operation. Add bulk status/role/session actions, progress, result
download and per-row audit correlation. Do not infer accounts from display names.

**Done when:** Malformed/duplicate rows are reviewable, retries do not double-apply,
concurrent changes fail safely, protected admins cannot be removed collectively,
and session revocation is enforced. No bulk grant bypasses the policy from 15.

### 21. Turn replay and trace timeline

**Reuse:** Saved requests/results, traces, operation/tool ledgers and Debug
sessions. Existing conversation `replay` reconnects to a saved outcome; it does
not execute a fresh model experiment and must keep that meaning.

**Finish:** Add an explicit debug experiment job from an authorized immutable
snapshot. Pin query, source scope/revisions, retrieval configuration, prompt,
provider/profile revision, settings and tool policy. Distinguish inspection,
exact-input rerun and rerun with selected overrides. Missing historical inputs
produce a clear non-replayable result. Never inject the experiment into the
student conversation. Render retrieval seams/candidates/selected chunks, exact
prompt, tool steps, timings and usage as a timeline with safe redaction. Apply
budgets, quota, cancellation and grant checks to experiment execution.

**Done when:** Original result is unchanged; a rerun has its own identity/manifest;
unknown prior provider outcomes are never replayed automatically. Source drift
and access revocation are visible. Secrets and unrelated student context cannot
appear in prompt/trace inspection.

### 22. Prompt versioning, diff and promotion

**Reuse:** Existing prompt constants/version labels, research prompt metadata
and ephemeral Debug `systemPrompt` overrides. These are useful foundations, not
a managed registry.

**Finish:** Store immutable named prompt versions, role/template/schema identity,
hash, creator and review state; map active pointers by allowed task/scope. Add
registry/diff/preset UI, compare overrides with active versions, and promote only
an explicitly reviewed version after required eval gates. Pin resolved versions
in every generation/job record; queued work retains its captured version unless
cancelled. Audit promotion/rollback and retain history. Prompts cannot override
auth, deterministic graph facts, hard budgets or tools policy.

**Done when:** Different text cannot reuse one version identity, failed gates
block publication, competing promotions are fenced and rollback selects a known
version. Existing greedy generation and provider/factory seams remain intact.

### 23. Eval suite management UI

**Reuse:** `retrieval/evals.py`, `scripts.eval_retrieval`, reviewed fixtures and
the existing job framework. Filename/locator expectations need revision-aware
identity for a managed suite.

**Finish:** Add immutable case/suite revisions and reviewed evidence/negative
expectations, including candidates from 14. Pin fixtures/source manifests,
config/prompt/model versions and evaluator version; separate offline fixture
evaluation from any explicitly approved live-provider experiment. Add queued
run/cancel/results APIs and UI with per-case explanations, recall/refusal/citation
checks and baseline differences. Define thresholds and reproducible blocking
regressions for 22. Unavailable evidence is reported, not silently skipped. Human
semantic review remains explicit; an LLM judge is not a release authority.

**Done when:** Frozen inputs reproduce supported metrics, edits create revisions,
regressions block the intended promotion path, and stale/missing fixtures cannot
make a run pass by dropping cases. Private feedback is redacted before entering
shared suites. Reuse existing scorer tests and add only new workflow invariants.

### 24. Cache and cost telemetry

**Reuse:** `pricing/service.py`, dated price schedules, model-call cost fields,
generation/claim/decision/batch accounting and the Admin telemetry tab. This is
substantially implemented and should be finished, not duplicated.

**Finish:** Inventory every provider call path: ordinary answers, query planning,
research/tool loops, compaction, repairs, artifacts/decisions and provider batches.
Reconcile each logical call/retry to one ledger entry with effective price-version
identity. Keep cached/uncached/output usage and unknown/estimated/provider-reported
usage distinct. Audit schedule overlap/model matching, unsupported usage, cache
token bounds, batch prices, failure/timeout settlement and currency aggregation.
Expose unknown-priced counts and denominator definitions; unknown is not zero.
Distinguish rate-based cost estimates from a reconciled invoice.

**Done when:** Seeded operations reconcile through API/dashboard, historical costs
retain their price schedule and no path double-charges cached input or retry
records. 27 consumes the same authoritative usage/accounting events.

### 25. Side-by-side model/config comparison

**Scope remains deferred.** Do not implement it as part of completing the accepted
features. Debug overrides and two manual requests are not this product feature.
If later accepted, reuse 21–24 with approved per-operation profile selection,
shared pinned inputs, isolated result identities, separate reservations and a
comparison view for answer/evidence/timing/cost. It requires 15/16/27 controls and
clear nondeterminism/provider-data policy; no uncontrolled fan-out.

### 26. Blob storage and private networking

**Reuse:** `storage/{base,local,dual,factory}.py` and migration 049 as a starting
seam, plus the [Azure handoff](azure-hosting-requirements.md),
[capacity plan](scale-6000.md) and `infra/`. `storage/blob.py` currently uses only
`self._store`, a dictionary; its replica test shares that dictionary. This does
not demonstrate remote persistence or replica correctness.

**Finish application storage:** Implement a real Blob provider with approved
identity/credentials, durable keys, checksum verification, immutable/conditional
writes, bounded streaming and error handling. Keep dictionary behavior in test
fakes; production must fail clearly if required backing storage is unavailable.
Construct/inject storage through runtime/config and route every relevant
artifact revision/export, raw upload, transcript/import original, sandbox
handoff and backup/restore path through it. DB stores object identity/digest,
not a replica-local absolute path. Coordinate object publication with DB state,
clean orphaned writes safely, and integrate privacy/retention.

Inventory existing files; migrate with verified checksums, resumable backfill,
explicit dual-read and a bounded rollback window. A fallback read is not proof
all files migrated. Complete backup/restore manifests and removal of old copies.

**Finish infrastructure:** Use the institution's chosen topology for private
ingress, database/storage private connectivity/DNS, worker/Office/sandbox
reachability and approved outbound services. The current Azure base deliberately
uses public ingress/database allowance and Azure Files. Extend existing Bicep and
deployment scripts; do not guess school identity/network decisions or claim
private operation because a storage class exists.

**Done when:** Separate API/worker replicas read the same durable bytes without
shared process memory/local disk, restart preserves data, corruption and partial
writes are handled, migration/rollback/restore pass, and deployed connectivity
plus intended public-access restrictions are tested. Development/local storage
can remain a supported mode; cloud production requires its explicit release gate.

### 27. Per-student rate limits and quotas

**Reuse:** Quota policy/bucket APIs and migration 048, existing operation budgets
and `AdmissionLimits`. Pending-work admission is useful, but not a daily student
token/query/spend cap. `QuotaService` currently has no generation consumer.

**Finish:** Define server-derived billing identity, scope aggregation and time
windows. Resolve idempotency before atomically reserving queries/tokens/spend and
concurrency with job admission. Reuse the operation ledger through retries/tool
fan-out, artifact/batch children, uploads/embedding and expensive sandbox classes.
Settle measured usage once; retain conservative liabilities for unknown outcomes
and reconcile later. A simple post-call counter cannot enforce a hard cap.
Support global/institutional ceilings and price-version-aware reservations;
student/cohort/debug overrides cannot raise hard limits. Add policy/effective
usage UI, truthful 429/Retry-After behavior and control capacity for cancel,
logout, privacy and reads. Define zero/disabled limits without truthy-default bugs.

**Done when:** Concurrent replicas cannot overspend a shared cap; duplicate starts
and retries do not double-charge; cancellation and restart cannot erase unknown
liability; day rollover and price changes do not free already admitted spend.
Tests exercise real generation admission/settlement, not only the quota service.

### 28. Observability alerts

**Reuse:** Loopback `/metrics`, pool/queue metrics and `observability/alerts.py`.
The current evaluator initializes spend/storage failure metrics to zero and does
not deliver alerts through deployed infrastructure.

**Finish:** Instrument provider errors/429/timeouts and latency, worker/lease
failure, queue wait/age, pool pressure, storage/integrity failures, unknown usage,
cache and budget burn. Missing telemetry is unknown/unhealthy, not a healthy
zero; isolate failed SQL probes so they do not abort subsequent readings. Define
fleet aggregation explicitly: per-process pools versus DB-wide gauges. Scrape
through a private authenticated collector/approved monitoring channel, retain
the loopback boundary, and add alert rules/actions as deployment code. Replace
placeholder owner names, thresholds/windows and runbooks with accepted values.

**Done when:** Controlled provider/storage/queue/pool faults deliver notifications,
recover cleanly, and missing collectors also alert. Multi-replica totals do not
multiply the same DB gauge. No student text/secrets/high-cardinality identifiers
leak in metrics. Evaluator tests with overridden values alone do not close this.

### 29. Admin/Debug browser coverage in required CI

**Reuse:** Existing cases in `browser-workflows.test.mjs`, offline fixtures and
the non-optional frontend CI job that runs `npm test`. The separate real-API
integration job is still experimental; do not confuse the two.

**Finish:** Run existing Admin/Debug behavior checks and retain role rejection,
session cleanup, override expiry/draft recovery, focus and Main credential
isolation. Extend these cases for newly completed reviewer/grant/flag/privacy
surfaces as they ship; payload rules remain client/backend tests. Verify actual
branch-protection settings require the frontend check at release time; workflow
source cannot prove repository settings. Stabilizing the experimental integration
job is a separate bounded task and does not excuse missing required browser tests.

**Done when:** Both bundles pass required offline browser CI without test-only
auth bypasses reaching production, and the required status check is confirmed.

### 30. Accessibility and localization

**Reuse:** Existing semantic controls, dialog helpers and browser focus tests.

**Finish:** Basic keyboard/focus/name/error/live-status behavior is part of every
earlier UI package. For the broad pass, inventory all Main/Admin/Debug flows,
editors/tables/citations and both themes. Check keyboard order/traps, focus
return, stream announcements, contrast, zoom/reflow and reduced motion; perform
manual screen-reader checks as well as automation. Externalize UI messages and
error-code display, use locale-aware dates/numbers/plurals and test expanded
strings. Keep evidence quotations/course codes/citations/editions exact. Agree
target accessibility criteria and supported languages/translation ownership
before claiming the full feature; English localization readiness is a milestone,
not delivered translations.

**Done when:** All shipped workflows pass the agreed manual/automated checks,
locale changes do not corrupt evidence or course identity and translated UI does
not imply translated rules are authoritative. Include accessible recovery and
downloads, not just the happy-path chat screen.

## Execution packages for later goal mode

These are completion goals, not instructions to start implementation now. Keep
changes reviewable and split a package into PRs at domain boundaries. Each goal
must reopen current code/tests, since this working tree may change after review.

| Package | Concrete objective | Prerequisites / exit gate |
|---|---|---|
| C0 | Establish current baseline; repair verification target authorization, destructive program-refresh history, and audit/rule false-success/crash cases; re-triage old audit/corpus evidence | No staff access bypass or refresh history loss; stop authoritative audit claims; existing invariant tests pass; no unrelated refactor |
| C1 | Finish 5/6/7/18/24/29 first-slice workflows and browser proof | C0 baseline; accurate wording/evidence, date filters, single notices and memory-path coverage |
| C2 | Finish role/grant enforcement and wire flag decisions into execution (15/17) | Capability matrix, real denial tests, scoped UI and revocation/kill-switch behavior |
| C3 | Complete privacy inventory/jobs/download + real storage + quota settlement + deployed telemetry (16/26/27/28) | C2 for real student data; split into the subgoals below; external topology/policy gates explicit |
| C4 | Finish source/rule review UI, safe release validation and invalidation consumer (11/12) | C0 rule repairs, C2 reviewer access; evidence-bound reviewed releases |
| C5 | Finish synthetic manual/CSV record workflow and immutable revisions (1) | C2 ownership and C0 invariants; real records wait for applicable C3 gates |
| C6 | Finish persisted deterministic audit UI and end-to-end pilot (2, audit notices from 7) | C4+C5; named program/cohort, all supported rules correct, unknowns visible |
| C7 | Finish comparison scenarios (3) and staged catalog refresh workflow (10) as separate goals | Comparison after C6; refresh after C4, with compatible publication and invalidation |
| C8 | Deliver feedback → triage → reviewed redacted eval candidate (8/14) | C2+C3 private-content policies; gap workflow retained |
| C9 | Deliver authorized experiment replay and trace timeline (21) | C1 accounting, C2 scope, C3 admission/privacy; immutable inputs |
| C10 | Deliver prompt registry, managed suites and gated promotion (22/23) | C8 candidates and C9 manifests; no promotion until checks/review work |
| C11 | Complete quality/usage dashboard (19) | C1 telemetry, C8 feedback and C10 quality results; trustworthy attribution |
| C12 | Implement gated/later 4/9/13/20/30 as separately accepted goals | Offerings/consent/language gates satisfied; basic accessibility already required |
| C13 | Reconsider 25 only after an explicit scope decision | Approved profile selection, C9/C10 and all cost/data controls |

Recommended first goal: **C0, then C1**, rather than adding more new backend
domains. C2/C3 and C4/C5 can proceed as separate tracks once their interfaces are
clear; a synthetic local audit pilot need not wait for school private-network
deployment. A real-data or production release does need its corresponding gates.

Split C3 into independently reviewable goals:

1. Complete inventory/policy and immutable job/download contracts; add real
   storage provider/runtime injection and file-consumer migration.
2. Complete privacy export/deletion/retention and backup/restore behavior using
   that storage seam. Test resumption and resurrection prevention.
3. Implement transactional quota reservation/settlement on all cost-bearing
   paths, sharing the existing operation/usage ledger.
4. Instrument actual metrics, deploy internal collection/alerts and conduct
   delivery/recovery checks. Complete approved private-network changes and
   multi-replica/storage/restore/capacity gates from existing release handoffs.

Each goal should carry this compact completion checklist in its working plan:

- Inventory existing code and classify reuse, repair and missing integration.
- Set the exact user workflow and acceptance cases before changing contracts.
- Implement backend/persistence and real API/client/UI/worker consumers together.
- Exercise authorization, conflicts, cancellation/retries and stale evidence.
- Verify data migration/cleanup/deployment where applicable; document external
  gates without marking them complete.
- Update architecture, matching schemas/TS, clients/mocks and existing tests;
  report focused and required check results with remaining limitations.

### Decisions to resolve at their dependency gate

| Decision | Blocks | Working assumption meanwhile |
|---|---|---|
| Who verifies attempts/credit awards/exceptions; acceptable evidence | Authoritative 1/2 | Student input stays unverified; synthetic scenarios only |
| Named programs/cohorts and grade/repeat/sharing/core policies | 12 releases / authoritative 2/3 | Narrow program audit; unresolved unsupported predicates |
| Record/transcript/feedback retention, holds, export inventory, backup expiry | Real-data 16/pilot | No claim selected-table cleanup is sufficient |
| Support approval authority; advisor consent/assignment/revocation | 15 staff-content access / 9 | Read-only scoped support; no blanket advisor access |
| Authorized term/section data and refresh cadence | Verified 4 | Sequence plans and clearly unverified manual scenarios |
| School identity protocol/private network and service connectivity | Institutional 26 release | Preserve auth/runtime seams; no guessed school topology |
| Price versions, caps/windows, SLOs, alert owners/delivery destination | 24/27/28 production gates | Unknown cost remains unknown; no placeholder alerts as readiness proof |
| Suite thresholds and human prompt promotion authority | 22/23 production promotion | Promotion blocked until applicable checks/review pass |
| Accessibility target, languages and translation owner | Full 30 | Accessible English and localization structure first |

## Initial review validation and limits

The initial review used source inspection and targeted offline checks. Execution
results above supersede the initial failures below. It did not run
live providers, scrape catalog data, change cloud infrastructure, migrate the dev
database, or establish that old corpus defects have been backfilled.

| Check | Observed result |
|---|---|
| Frontend `npm run typecheck` | Passed |
| Frontend `npm run format:check` | Passed |
| Backend `ruff check .` | Failed: one unused unpacked variable, `release_id`, in `tests/test_degree_audit.py:240` |
| Contract drift + no-LLM prerequisite guard + pure degree-audit cases | 26 passed, 1 failed, 2 DB cases deselected; failure is the grade-threshold crash |
| 14 targeted new-domain test modules, including DB tests | 15 passed, 1 failed, 48 setup errors; repeated common embedded-Postgres startup failure, not 48 established application defects |
| Pure synthetic audit probes | Empty all-of course leaf falsely satisfied; unverified completed work satisfied; five 3-credit attempts missed a 15-credit threshold |

Embedded Postgres startup failed with Windows `pg_ctl: could not create
restricted token: error code 87`, followed by a missing startup-log error. At review time this
prevented database-backed acceptance conclusions; implementation testing has since
resolved the environment restriction and exercised real disposable Postgres. The test runs
used fresh unique basetemp paths; the fixture creates its separate temporary
Postgres data directory. A later implementation goal must restore a working
disposable Postgres environment and rerun the relevant DB checks. No skipped DB
test counts as a pass. Do not infer feature readiness from existing unexecuted
test files. Browser workflows, full backend/frontend suites, branch-protection
settings and deployment/load/restore checks were not verified here.

Implementation must allocate additive migrations after the highest migration
present at that time (053 is present in this reviewed working tree; docs still
mention 041). Do not edit the baseline/applied archive or assume an untracked
migration has not been applied locally. Update stale architecture/AGENTS head
references with the delivered contract. Keep SQL in named blocks, provider and
connection factories intact, external I/O outside held borrows, exact evidence
identities stable and prerequisite graphs entirely deterministic. Similarity
nudges remain human-authored.

All exclusions from the feature review remain excluded, including autonomous
registration/messaging, SIS writes, automated equivalency decisions, public social
sharing, multi-institution tenancy, expanded script execution and model-judged
release authority. Planning all 30 entries does not silently approve deferred
or data-gated product scope.
