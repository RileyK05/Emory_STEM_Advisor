# Feature review: implementation handoff

> Completion review, 2026-10-19: the checkmarks below overstate several current
> implementations. Use [feature-completion-plan.md](feature-completion-plan.md)
> for the verified partial/missing work, acceptance gates and later goal sequence.
> This older handoff remains a description of intended behavior.

Requested 2026-10-18. Covers all 30 numbered additions in
[feature-review.md](../feature-review.md), using the current working tree rather
than only committed code. This is a proposed implementation sequence, not a
record that every candidate has been approved for release.
[architecture.md](../architecture.md) remains the design contract. Each delivered
slice must update that contract and the matching API types. Gated and deferred
items below retain the review's scope decisions.

## Recommended delivery order

The largest new subsystem is **executable, evidence-bound program rules**. The
existing requirement parser extracts raw lines, course mentions, and explicit
counts; it does not define how requirements combine. Degree audits and useful
what-if comparisons require this subsystem as well as an academic record.

| Wave | Deliverables, using review numbers | Exit condition |
|---|---|---|
| 0: establish the base | [x] Re-triage prior audits; [x] 29 browser coverage; contract additions for the next slice | Existing behavior is verified against current code; open data defects have owners and scoped repairs |
| 1: bounded improvements | 5 summary export, 6 memory inspector/reset, 7 applicability notices, 18 audit browsing/export, 24 cost/cache telemetry | Useful student/admin improvements with ownership, evidence, and failure behavior preserved |
| 2: access and release controls | 15 roles/support grants, 16 retention/export/delete, 17 flags, 26 storage/networking, 27 quotas, 28 alerts | Required before the corresponding student-data pilot or production expansion |
| 3: trusted advising inputs | 1 academic records, 11 provenance lifecycle, 12 requirement review; then 10 catalog refresh approval | Private record revisions and reviewed, evidence-bound rule releases can be pinned |
| 4: deterministic advising | 2 audit engine, then 3 what-if comparison | Audits explain allocation and unresolved rules; comparison uses the same frozen inputs |
| 5: quality operations | 8 feedback, 14 eval candidates, 21 replay, 22 prompt versions, 23 eval UI, then 19 dashboard | Feedback can become reviewed regression coverage; promotions use reproducible checks |
| 6: conditional expansion | 4 semester planning, 9 caseload/sharing, 13 conflicts, 20 bulk users, 30 broader accessibility/i18n | Each feature's data/product gate has been met |
| Deferred | 25 model/config comparison | Approved per-operation profile selection and cost controls exist |

Waves express dependencies, not mandatory large releases. For example, source
and rule review can precede student-record UI; telemetry and deployment work can
proceed independently of the audit engine. Basic keyboard and screen-reader
behavior belongs in every new UI even though the broader pass is wave 6.
Do not treat feature flags as a substitute for access, retention, or data gates.

### What already exists and should be extended

- `app/programs/requirements.py`, `binding.py`, and `importer.py` retain exact
  raw requirements and bind them to source revisions. Add executable rule
  releases alongside these projections; preserve raw text and historical evidence.
- `app/advising/comparison.py` explicitly returns `mention_overlap_only`.
  Its shared mentions cannot establish required-course overlap or double counting.
- `app/advising/sequence.py` supplies deterministic dependency stages, not verified
  semesters. Keep it as a pure primitive beneath any future scheduling layer.
- `api/routes/conversation_summaries.py` already provides owner-scoped checkpoint
  reads. `conversations/compact.py` preserves reference pointers; research also
  has a separate conversation working space. Inspection is mostly UI work;
  meaningful forgetting requires changes across all those context paths.
- `admin/store.py`, `api/routes/admin.py`, `debug/store.py`, and the separate
  frontend bundles provide audited institutional operations and scoped overrides.
  At the time of this handoff, institutional roles were `student|admin`; the
  delivered completion plan adds advisor and auditor plus scoped support grants.
- `llm/base.py` already represents `cached_prompt_tokens` and measured, estimated,
  and unknown usage. Extend accounting and aggregation instead of adding a
  competing usage ledger.
- `retrieval/evals.py` and `scripts.eval_retrieval` already score recall and
  negative cases. They currently identify expected evidence by filename/locator
  label; new managed suites need revision-aware identities.
- The Azure scaffold already provisions deployment resources and interim Azure
  Files. Blob-backed application storage and the school network/identity decisions
  remain work. Follow [the Azure handoff](azure-hosting-requirements.md) and
  [the capacity plan](scale-6000.md), rather than making a second deployment plan.

### Wave 0: reconcile the older findings

The October 10 audit describes older code. Inspection now finds department
boundaries in `programs/catalog_pdf.py`, canonical course identities in migration
035 and atlas import, historical graph ownership/retirement in migration 034,
and released request borrows/separate tool pools in recent runtime changes.
Those findings must be verified, not blindly implemented again. Code inspection
alone does not establish that the local corpus was regenerated or backfilled.

1. Revisit DA-01/02/06, UI-03, RT-01/02, and WF-01 in the existing audit files.
   Verify parser boundaries, canonical deduplication before retrieval quotas,
   current graph retirement, exact-ID citation navigation, one-slot pool behavior,
   and evidence-dependent resolution invalidation.
2. Extend existing invariant tests where coverage is missing. Use offline cached
   catalog fixtures and fake models; do not fetch the catalog during pytest.
3. For affected persisted data, prepare an origin-aware regeneration/backfill
   with counts and checksums and preserve historical citations. Do not equate a
   parser fix with a corrected database.
4. Update the existing audit finding with evidence and remaining limits. Fix open
   data-boundary defects before using affected programs in an audit pilot. Do not
   claim that all audit findings are closed or start unrelated audit redesigns.

## Shared implementation rules

- New persisted entities use additive migrations after the head at the time of
  this handoff, 041 (the current head is 068; see the completion plan).
  Allocate migration numbers when implementing to avoid collisions. Never edit
  the baseline, archive, or an applied migration. Put SQL in named query blocks.
- All student records, audit results, plans, feedback, exports, and replay inputs
  are owner-scoped. Staff access is a server-side capability plus a scoped grant,
  where required; a supplied workspace key never grants access.
- Store immutable revisions and input digests. An audit pins record revision,
  program-rule release, catalog/cohort choice, course identity version, and engine
  version. Plans add offering/constraint revisions. Stale results remain readable
  as history and visibly require recomputation; never silently rewrite them.
- Use expected revisions for edits and approval, idempotency keys for job starts,
  short publication transactions, and existing job cancellation/lease fencing.
  Extraction, solving, rendering, and provider I/O occur outside held DB borrows.
- API changes land together in `api/schemas.py`, `frontend/src/api/types.ts`,
  the appropriate shared/backoffice client, mock behavior, and architecture.
  Declare response models and preserve omitted-key behavior. Model availability,
  provider construction, and configuration retain their existing seams.
- Models may explain deterministic outputs with citations. They never verify a
  transcript, approve a waiver, determine course equivalence, write prerequisite
  facts, choose unknown degree-rule semantics, or author similarity nudges.
- Extend existing tests for an existing invariant; add a focused module only for
  a genuinely new domain. Pure engines use table-driven unit tests; SQL and race
  semantics use real DB tests. Browser checks cover focus, rendering, isolation,
  and recovery. No live models/network in pytest and no saved test-output reports.

## Student-facing features

### [x] 1. Academic record and transcript import (Completed 2026-10-19)

**Scope/dependencies:** First deliver manual entry and CSV import. Before accepting
real student records, apply the ownership, retention, export/delete, and storage
policies from 15/16/26. Use synthetic records during development. PDF import is a
separate follow-up after formats and the verification workflow are agreed.

**Implementation:** Add `app/records/` with bounded types, deterministic validation,
CSV preparation, and a revisioned repository. Persist record revisions, individual
course attempts, import batches, and append-only verification events. Each attempt
has institution, submitted and resolved course identity, term, attempted/earned
credits, grade, status, provenance, and verification status/method/actor/time.
Keep completed, in-progress, planned, withdrawn, failed, and repeated attempts
distinct. Unknown mappings and disputed credits stay unresolved. An uploaded
transcript or a student's confirmation establishes provenance, not institutional
verification. Staff verification requires a capability and an explicit record of
what was checked. Transfer/AP/IB credit can be recorded as awarded by an authority;
the app does not decide equivalency.

**API/UI:** Proposed `/api/academic-record` revision reads/updates and
`/api/academic-record/imports` preview/commit jobs. Show editable staging rows,
row errors, duplicate candidates, and verification badges before commit. Keep
transcript originals in private record storage, outside ordinary retrieval and
institutional source ingestion. A later deterministic PDF extractor produces the
same staging format with page/row locators; unsupported scans remain manual until
an explicit OCR scope is chosen.

**Acceptance:** A preview commits only reviewed rows against an expected revision;
retries do not duplicate attempts; repeated courses are preserved; editing a
verified fact invalidates its verification; cross-workspace reads fail. Ambiguous
grades, mappings, and credits cannot silently satisfy a verified audit.

### 2. Deterministic degree audit

**Scope/dependencies:** Requires 1 and reviewed rule releases from 12, with source
lifecycle support from 11. Start with a small, explicitly named program/cohort
pilot. Initially call it a program audit; a whole-degree completion claim also
requires reviewed school/core, residency, credit, GPA, track, and exception rules.

**Implementation:** Add pure rule types and evaluators under `app/advising/`,
separate from prerequisite parsing. Initial nodes can support exact-course,
all-of, any-of, choose-count, and supported credit thresholds. Every node carries
source spans and release identity. Unsupported clauses become explicit unresolved
nodes; a course mention or bare `choose` field is not an executable rule. Add
versioned grade/repeat/credit and double-count policies only when evidence supports
them. Use a deterministic, bounded allocation search with stable tie-breaking;
retain alternate allocations when relevant and return unresolved if its search
limit is reached. Do not use greedy assignment that can falsely report a deficit.
Keep evidence-bound approved exceptions separate from catalog rules.

**API/UI:** Proposed `/api/audits` creates a persisted snapshot/result through the
existing job framework where needed. Return per-rule `satisfied|outstanding|
unresolved`, allocated attempts, credits, evidence, blockers, assumptions, and
coverage. Display in-progress contributions separately from completed verified
satisfaction. Unverified student data can produce a clearly conditional scenario.
The optional model explanation reads this result and cannot change statuses.

**Acceptance:** Table-drive alternatives, shared-course competition, repeats,
grade thresholds, in-progress work, unverified records, unsupported predicates,
and allocation bounds. No overall satisfied status if required rule coverage or
evidence is unresolved. Identical pinned inputs produce identical results; source
replacement marks results stale without altering the saved result.

### 3. Program comparison and what-if

**Scope/dependencies:** Requires 1/2 and applicable rule releases for each candidate.
Compare two or three majors/minors in the first UI. Existing research comparisons
remain usable as text-grounded mention comparisons in the meantime.

**Implementation:** Add a pure comparison service using one pinned record across
all candidates. Compute each program audit, remaining requirements, supported
overlap, and explicitly evidenced double-count constraints. A hypothetical record
overlay is immutable and separate from the student's real record. Separate
individual-program comparisons from a combined-program allocation problem; the
latter must obey all relevant sharing rules or report the restriction as unknown.

**API/UI:** Proposed `/api/program-comparisons` stores candidate rule releases,
record/overlay digests, and audit IDs. Render columns with completed, outstanding,
conditional, and unresolved results and links back to rules. Do not rank by a
single completion percentage when rule coverage differs. Remaining terms are
unavailable until 4's offering and constraint gates are met; any supported bound
must disclose its assumptions.

**Acceptance:** Comparison never changes the real record, never treats mention
overlap as credit savings, and never counts one attempt twice contrary to policy.
Unknown rules remain visible and cannot make a candidate look easier.

### 4. Semester plan builder

**Scope/dependencies:** Data-gated after 1/2/3. First ship a saved sequence/staging
plan using `advising/sequence.py`. Term placement requires an authorized offerings
source with term identifiers, course/section identity, credits, availability status,
refresh cadence, and provenance. Section-time conflict checks additionally require
meeting data; seat availability is time-sensitive and cannot be promised.

**Implementation:** Add a versioned offerings importer once that contract exists.
Build a pure bounded scheduler over approved degree allocations and deterministic
prerequisite/corequisite trees. Inputs include completed attempts, selected
alternatives, term loads, unavailable terms, student preferences, and any required
approvals. Do not guess an unresolved condition or select an academic alternative
without an explicit scenario choice. Record solver version/limits and distinguish
infeasible from unsupported or timed-out searches.

**API/UI:** Proposed `/api/plans` has immutable revisions and a validation operation.
A drag between terms reruns deterministic validation and reports the specific
violated rule. Show offering timestamp and approval blockers. Allow a manually
assigned term as a student scenario, labelled unverified when offerings are absent.

**Acceptance:** Reject load violations, impossible corequisites, unsupported
offerings, and prerequisite ordering errors. Refreshes invalidate affected plans;
dependency stages are never relabelled as verified semesters or graduation dates.

### 5. Advising summary and plan export

**Scope/dependencies:** Quick win on current conversations/artifacts; add typed
audit/plan sections later as 2/4 arrive.

**Implementation:** Add a deterministic summary-spec builder in `app/artifacts/`
that pins published conversation sequence, cited passages, decisions, open
questions, and selected artifact revisions. Distinguish user goals, model
recommendations, and verified deterministic results. An optional generated
narrative uses existing grounded generation and operation budgets; a template
alone can deliver the MVP. Missing support becomes an open question.

**API/UI:** Add an advising-summary kind/input to the existing artifact run contract
and a chat action. Reuse the document editor and Markdown/DOCX exports, citation
legend, exact revision download, polling, and cancellation.

**Acceptance:** Export opens the published revision, citations resolve to saved
passages, pending/failed turns are excluded, and later chat changes do not rewrite
the exported snapshot. Unsupported advice cannot become a verified plan by export.

### [x] 6. Conversation memory inspector and reset (Completed 2026-10-19)

**Scope/dependencies:** Inspection reuses existing `GET .../summaries`. Implement
forgetting as an explicit context reset, coordinated with existing compaction and
research conversation continuity.

**Implementation:** Render checkpoint state, coverage range, summary text, exact
references, and separately labelled research notebook state. Add a revisioned
context epoch/start boundary to conversations and a proposed
`POST /api/conversations/{id}/memory/reset`. Retain immutable history but exclude
pre-boundary turns from subsequent compaction, raw-history context, warm citations,
research notebook seeds, archive recall, and inferred source/topic continuity.
Explicitly reselecting a source or artifact is a new user action. Reset rejects
while a request/summary is pending, using a transactional fence so late completion
cannot publish memory into the new epoch.

**API/UI:** Explain that reset changes future context and retains the transcript;
actual erasure belongs to 16. Display empty/failure states and preserve pointer
navigation. Expose no private model reasoning beyond already persisted summaries,
task state, and evidence references.

**Acceptance:** A reset survives reload and new turns cannot recall old context
through any of the paths above. Cross-owner and concurrent resets are fenced;
failed summaries never claim coverage or hide raw turns.

### [x] 7. Source freshness and applicability banner (Completed 2026-10-19)

**Scope/dependencies:** Can ship now using verified provenance; gains richer review
state from 11 and cohort choice from 1/2.

**Implementation:** Add a shared deterministic notice serializer over evidence
actually supplied/cited, pinned metadata revision, catalog edition, and declared
student cohort. Return known scope, conflicts, unknown applicability, and whether
the saved source has since been superseded. Define freshness thresholds per source
class before calling anything stale by age. `is_current` and `reviewed_default`
do not establish that a rule applies to this student.

**API/UI:** Add a common notice type to answers, research decisions, audits, plans,
and summary exports. Show saved edition/applicability and a separate current-status
indicator; distinguish a harmless source update from a confirmed scope mismatch.
Avoid a corpus-wide banner implying that every selected document supported an answer.

**Acceptance:** Historical applicable rules are not marked invalid solely because
they are superseded; missing dates stay unknown; conflicting or changed metadata
is visible. Exported notices match the pinned evidence snapshot.

### 8. Student feedback and triage queue

**Scope/dependencies:** Owner feedback can precede 15; institutional triage of
private content requires its capability/grant policy and 16's retention policy.

**Implementation:** Add revisioned feedback records bound to a published response
or refusal, with rating, bounded reason codes/comment, trace identity, and original
scope. A negative rating creates a triage item, not an institutional fact or an
automatic retrieval-gap observation. Extend the gap workflow with typed feedback
links where the issue is missing retrieval evidence; keep provider errors,
incorrect explanations, privacy complaints, and UI failures distinguishable.

**API/UI:** Proposed `/api/responses/{id}/feedback` supports idempotent upsert;
add answer controls and a capability-gated triage view. Central lists show minimal
metadata until an authorized content read. Provide a deliberate, redacted snapshot
handoff to 14 instead of copying student conversations into shared evals.

**Acceptance:** One student's response ID cannot be rated/read by another;
resubmission does not inflate counts; feedback preserves the exact original turn
and trace; staff queue access cannot bypass private-content rules.

### 9. Advisor caseload and shared plans

**Scope/dependencies:** Product-gated after 15/16. Decide who assigns caseloads,
whether any institutional mandate permits access, consent duration/revocation,
advisor edits versus recommendations, and appointment workflow. No calendar or
SIS integration is implied.

**Implementation:** Add advisor assignments and separate resource grants for
records, named plan revisions, and optional conversations. An assignment alone
does not grant all student content. Default to student-controlled sharing of an
immutable plan revision. Record access and advisor recommendations separately
from authoritative record verification or rule exceptions.

**API/UI:** Add advisor-specific APIs/surface with student-controlled sharing and
revocation in Main. Server authorization checks assignment, active identity,
grant scope, expiry, and revocation on each read/export/job publication.

**Acceptance:** Revocation blocks new reads and pending export publication;
unassigned students and unrelated conversations remain inaccessible. Shared
revision changes require explicit re-sharing or an explicitly chosen live grant.

## Data and content operations

### 10. Catalog refresh diff and approval

**Scope/dependencies:** Extend existing scraper/importers and origin-aware
publication after wave 0 and 11/12. MVP is a reviewed refresh of a bounded corpus;
whole-edition consistency needs a new release manifest, not a loop of independent
source replacements.

**Implementation:** Persist refresh candidates with exact input hashes, parser
versions, origin mappings, proposed source/program/course changes, graph inputs,
and validation results. Prepare into staging/shadow projections without invoking
the live-publication side effects of current importers. Compute deterministic
add/remove/change diffs of raw requisites, parsed trees, canonical identities,
requirement text, bindings, and provenance. Stage a compatible graph or explicitly
retire it with an unavailable state. Pin every approval to the candidate digest.
Use a release pointer and one short transaction to activate a coherent manifest;
keep the prior edition/history readable. A rollback publishes a new activation
event rather than mutating historical evidence or migration files.

**API/UI:** Add Admin refresh jobs, diff paging, impact preview, approve/reject,
and publish status. Scraping continues to stop on WAF challenges and uses approved
public sources/cache; it never routes around a challenge.

**Acceptance:** Failed preparation or stale approval leaves the last-good corpus
intact. Concurrent import changes reject publication. Readers cannot see mixed
program/catalog releases, and historical citations survive removal of a listing.

### [x] 11. Source and provenance lifecycle UI (Completed 2026-10-19)

**Scope/dependencies:** Extend trusted ingestion/provenance seams, with a dedicated
source-review capability from 15 for institutional use.

**Implementation:** Add review proposals/history for title, classification,
institution, issuing unit, roles, edition, applicability, authenticity review,
and replacement intent. Approval calls the same bounded validators used by
verified importers. Keep content identity, latest origin version, applicability,
and metadata revision separate. List dependencies on programs, graph builds,
active research scopes, gap resolutions, audits, and plans. If work is invalidated
asynchronously, write a transactional outbox event with publication; never leave
an unrecorded interval where dependent approvals appear valid.

**API/UI:** Add curated Admin source detail/proposal/approval actions, lifecycle
history, and impact preview. A deprecation is a recorded state change; replacement
uses normal staged ingestion. Ordinary upload endpoints still cannot self-classify.

**Acceptance:** Competing metadata edits require expected revision, provenance
changes fence research reads/publication, invalidated approvals require renewed
review, and evidence snapshots keep their historical labels. Deletion is never
an incidental metadata edit.

### [x] 12. Program requirement review UI (Completed 2026-10-19)

**Scope/dependencies:** Begin with the existing bound program projection. Deliver
raw inspection first, then the executable rule authoring/release step required by 2.

**Implementation:** Show raw lines and their exact spans, parsed mentions/counts,
binding status, unrecognized clauses, and parser version. Add immutable rule-set
drafts/releases with node types, logical composition, references to raw evidence,
coverage classification for every substantive clause, supported policy predicates,
and human review events. Require evidence for composition across school core,
major, track, and concentration. Reviewers can explicitly map a supported rule or
mark it unresolved; they cannot certify missing text by checking a box. Corrections
to extraction belong in parser fixes/re-imports or separately evidenced reviewed
overlays, preserving the original line.

**API/UI:** Add Admin program inspection, rule draft validation, source-linked diff,
approval, and release. Only bound matching revisions can be published as supported
rules. Keep candidate rule releases out of the default audit path.

**Acceptance:** Alternatives/exclusions never become all-required courses, every
clause has a coverage disposition, stale evidence prevents release, and a reviewer
cannot edit the prerequisite graph through this surface.

### 13. Duplicate and conflict detection

**Scope/dependencies:** Later, after 10/11/12 provide stable identities and editions.

**Implementation:** Add deterministic candidate detection for equal content hashes,
overlapping origins, same titles across editions, intersecting applicability, and
contradictory supported structured rules. Textual or semantic similarity is only
a review lead, not proof of policy conflict. Persist a versioned issue with exact
sources/spans, reason, reviewer disposition, and dependency digest. No automatic
merge, precedence decision, cross-list inference, or deletion.

**API/UI:** Add an Admin issue queue and compare view with dismiss/link/escalate
actions. Reopen a disposition when its supporting revisions change.

**Acceptance:** Same-name sources with different applicability remain distinct;
the system distinguishes a duplicate candidate from a verified rule conflict;
resolutions preserve source identity and historical citations.

### 14. Gap resolution to eval case

**Scope/dependencies:** Requires reviewed gap resolution semantics from wave 0;
accept feedback from 8 when available. Managed suite membership arrives with 23.

**Implementation:** Add a candidate builder beside `gaps/store.py` and a small
`app/evaluation/` store. A human chooses a redacted query, expected refusal or
evidence, exact source revisions/spans, expected deterministic checks, and scope.
Freeze the candidate with its originating gap/feedback link and reviewer. Promotion
is explicit and idempotent; resolution notes and model answers are not gold labels.
Extend the eval identity loader to accept revision-aware cases while preserving
legacy JSONL fixtures.

**API/UI:** Add a 'Create eval candidate' action to resolution/triage with preview,
redaction, and evidence validation. Export to versioned fixture JSONL or add to a
draft suite. Shared cases require approved non-private evidence and redacted text;
private cases stay in their authorized evaluation scope.

**Acceptance:** Repeated promotion does not duplicate a case; missing/changed
expected evidence is unresolved and fails promotion; no hidden student text or
private evidence enters a shared suite.

## Governance and admin

### [x] 15. Finer roles and time-boxed support access (Completed 2026-10-19)

**Scope/dependencies:** Before a pilot that involves staff access or real academic
records. Product owners must define actual responsibilities; implement capabilities
so later decisions do not require scattered role-name conditionals.

**Implementation:** Extend durable roles to student/advisor/admin/auditor and map
them centrally to capabilities. Student owns private data; advisor gets only
authorized assigned/shared resources; auditor gets bounded audit metadata by
default; admin handles operations. Add expiring support grants with actor, target
workspace/resources, allowed read actions, reason, approver, expiry, and revocation.
Define the approval authority explicitly, such as owner consent or an authorized
institutional approver; no silent self-approved content access. Review existing
admin transcript-read powers during migration and preserve an explicit restricted
compatibility policy until the new one is configured. Debug remains separately
gated and cannot impersonate a student.

**API/UI:** Extend `get_principal`-based authorization, admin user types, role controls,
and grant management. Record successful sensitive reads and bounded access-denial
events without copying content/secrets into audit details.

**Acceptance:** Exercise the role/capability matrix, disabled users, last-admin
protection, grant expiry/revocation, direct URL/API bypasses, and cross-workspace
jobs. Role changes take effect for existing sessions and pending publication.

### [x] 16. Retention, student export, and deletion (Completed 2026-10-19)

**Scope/dependencies:** Establish policy before real-record collection and before
production. This plan does not choose legal retention periods or certify compliance;
the institution must supply retention, access, legal-hold, and backup requirements.

**Implementation:** Add an inventory of owned data and dependencies: records,
transcript originals, conversations/summaries, research snapshots, citations,
artifacts, traces, jobs/events, feedback/evals, sessions, accounting, and audit.
Build resumable export/delete jobs and a retention worker using existing jobs.
Export a documented manifest with original files and structured data. Deletion
first fences new work and revokes applicable access, then cancels/drains pending
publication and removes owned content in dependency order. Shared content and
required minimal audit/accounting records follow an explicit policy. Content hashes
do not authorize deleting a file still referenced by another owner/revision.
Record tombstones for replay/restore suppression and bounded progress without
retaining the deleted content in the tombstone.

**API/UI:** Proposed `/api/privacy/exports` and `/api/privacy/deletions`, status,
expiring authenticated downloads, and an explicit destructive-action flow. Explain
the distinction between active-store deletion, legal holds, and backup expiry.
Backups must reapply deletion tombstones before restoring service to users.

**Acceptance:** Jobs resume after failures; cross-owner files survive; deleted
content cannot return through summaries, eval copies, exports, or late jobs.
Restore drills verify deletion handling alongside DB/object recovery. Choose actual
retention intervals only after the institution supplies them.

### [x] 17. Feature flags and cohort rollout (Completed 2026-10-19)

**Scope/dependencies:** Use the existing env controls as global ceilings; new
cohort policy must never enable a feature disabled globally.

**Implementation:** Add typed, allowlisted flag definitions and revisioned global,
cohort, and workspace assignments with deterministic precedence. Cohort membership
is trusted administrative data, never an arbitrary client claim. Persist the
effective flag/config revision in operation inputs; workers execute the admitted
snapshot. Separate ordinary rollout edits from a kill switch: security disablement,
revoked authorization, and emergency stops are rechecked before expensive calls
and publication, even for older snapshots. Prompt rollout references a released
version from 22, not arbitrary prompt text.

**API/UI:** Add Admin rollout preview, affected-cohort counts, audit history, and
kill-switch controls. Return only relevant effective capabilities to Main.

**Acceptance:** Assignment is stable across replicas/restarts, scope precedence is
predictable, clients cannot self-enable features, and rollback/kill behavior is
tested for pending work. No per-cohort secret or credential overrides.

### [x] 18. Audit filters, pagination, and export (Completed 2026-10-19)

**Scope/dependencies:** Quick win extending the current limit-only `/api/admin/audit`
and the existing `admin_audit` table; use 15's auditor capability when available.

**Implementation:** Add bounded actor/action/target/date filters and a stable
keyset cursor ordered by `(created_at, audit_id)`. Add indexes justified by query
plans. Capture a snapshot upper bound for consistent multi-page export. Export
JSON/CSV through a bounded authenticated download or an existing job for large
ranges; redact details by capability and neutralize spreadsheet-formula prefixes
in CSV. Audit export actions without creating recursive export churn.

**API/UI:** Extend the existing list contract compatibly or add an explicit paged
contract, updating callers together. Retain filters in navigation, show loading,
empty/error states, and visibly identify the exported time range.

**Acceptance:** Concurrent inserts cause no missing/duplicated rows across the
snapshot, filter combinations agree with export, invalid cursors fail safely,
and CSV does not execute user-controlled cell formulas.

### 19. Usage and quality dashboard

**Scope/dependencies:** After 24 telemetry and 8 feedback; add program-level views
only when a result explicitly identifies its program. Do not infer it from private
query text.

**Implementation:** Build bounded aggregate queries/materialized rollups over
operations, responses, traces, gaps, feedback, and jobs. Define denominators:
validated answer, valid refusal, withheld generation, provider failure,
cancellation, and overload rejection. Show sample sizes and unknown usage.
Feedback and structural citation validity are signals, not semantic accuracy.
Program, collection, model, and operation dimensions must be recorded explicitly.

**API/UI:** Add capability-gated Admin aggregates with time windows and drill-down
to authorized metadata; no raw content in metric labels. Suppress or coarsen small
cohorts according to institutional policy. Keep operational alerts in 28.

**Acceptance:** Retries do not inflate logical answer counts, empty denominators
show unavailable rather than zero quality, and rollups reconcile with scoped
ledger rows across restarts and multiple workers.

### 20. Bulk user operations

**Scope/dependencies:** Later, after 15/17. CSV pre-provisioning requires an exact
trusted issuer/subject identity policy; email alone cannot assign an existing
account or establish institutional eligibility.

**Implementation:** Add dry-run CSV parsing with bounded rows, deterministic
identity validation, duplicate handling, expected user revisions, and a signed or
persisted preview digest. Commit through a resumable job with per-row outcomes
and audit events. Bulk role/status/cohort changes and session revocation use the
same protected service methods as single-user operations. Choose and display
partial-success semantics rather than pretending an entire long import is atomic.

**API/UI:** Admin upload, preview, explicit commit, cancellation, and row-level
result download. No automatic invitations/email are included.

**Acceptance:** CSV retries are idempotent, changed previews require re-review,
last-admin and self-demotion protections survive bulk concurrency, and a wrong
identity mapping cannot assign another student's role or cohort.

## Debug and evaluation

### 21. Turn replay and trace timeline

**Scope/dependencies:** Extend saved request/response snapshots, traces, model-call
accounting, and Debug sessions. Apply 15/16 access/retention controls.

**Implementation:** Add an explicit experiment/replay entity referencing the
original published turn. Separate three actions: inspect recorded evidence with
no provider call; regenerate from a frozen compatible input snapshot; and re-run
retrieval against current sources. The last is a new experiment, not exact replay.
Before calling a provider, verify the snapshot includes prompt, context, source
revisions, artifact revisions, resolved settings, tool versions, and approved
profile/model revision. Older incomplete snapshots are inspectable but cannot
claim exact replay. Greedy generation does not guarantee provider reproducibility.
Replay read-only tools against pinned evidence; disable side-effecting tools or
run them only in a new isolated experiment namespace. Never resubmit an unknown
original provider outcome.

**API/UI:** Proposed `/api/debug/replays` starts a new budgeted job. Timeline shows
retrieval seams, selected passages, tool events, prompt version, timings, usage,
and validation outcome without exposing credentials or hidden reasoning. It does
not append turns or change artifacts in the original conversation.

**Acceptance:** A replay has its own operation IDs/cost; missing historical inputs
fail visibly rather than widening scope; cancellation leaves the original intact;
source replacement is distinguished from a like-for-like experiment.

### 22. Prompt versioning, diff, and promotion

**Scope/dependencies:** Versioning can ship before 23; production promotion waits
for its eval gates. Begin with the answer system prompt already exposed by Debug;
research, planning, and compaction prompts get explicit separate families.

**Implementation:** Add immutable prompt/preset versions, template-variable schema,
content digest, parent, release state, and approval history. Keep a code-owned
fallback. Debug edits create drafts/overrides; a named preset references validated
override keys. Promotion pins candidate digest, required eval suite/results, reviewer,
and baseline version, then atomically changes the active pointer. Pending operations
retain their admitted version. Rollback is a new activation of a prior version.
No preset may alter credentials, auth, source access, or prerequisite facts.

**API/UI:** Add version browser, textual diff, candidate testing, approve/promote,
and activation history. Record effective prompt version/text digest in all new
operation snapshots, including tool loops and summary calls where applicable.

**Acceptance:** Drafts cannot affect Main, stale approvals fail, a regressed or
incomplete evaluation blocks promotion, and rollback is deterministic and audited.

### 23. Eval suite management UI

**Scope/dependencies:** Builds on 14 and the existing retrieval runner; coordinates
with 21/22. Keep human review as authority for semantic judgments.

**Implementation:** Add immutable case/suite versions and eval run results under
`app/evaluation/`. Extend the runner to exact revision-aware evidence with a
compatibility adapter for old filename/label cases. Distinguish retrieval recall,
negative-case false positives, deterministic rule/record outcomes, citation
structure, and human semantic review. A prompt cannot be approved merely because
retrieval recall stayed constant; prompt releases require relevant answer-level
coverage/review. Freeze suite, corpus manifest, prompt/config, profile revision,
engine versions, and budget per run. Missing cases/evidence are unresolved and
block gating rather than disappearing from denominators.

**API/UI:** Add Debug case drafts, suite freeze, budgeted run/cancel, baseline diff,
per-case failures, and promotion eligibility. No model judge is release authority.
Maintain a held-out suite policy so repeated candidate tuning does not consume all
validation evidence.

**Acceptance:** Identical frozen retrieval inputs reproduce metrics; known negative
cases remain separately scored; changed corpus labels cannot silently pass a case;
required regressions, unresolved cases, or absent human signoff prevent promotion.

### [x] 24. Cache and cost telemetry (Completed 2026-10-19)

**Scope/dependencies:** Quick win extending existing `Usage` and model-call records.
No hardcoded current provider prices are introduced by this plan.

**Implementation:** Verify cached-token capture/persistence across each existing
adapter and operation path before adding fields. Add a versioned, dated price
schedule with currency, model/profile, effective period, cache-read/write treatment,
and provenance. Persist which schedule priced each attempt and distinguish measured
usage, estimated cost, and unknown cost. Prompt tokens already include cached
tokens where the provider contract says so; avoid adding them twice. Define both
cached-token share and the fraction of calls reporting cache hits, with reporting
coverage and explicit denominators. Unknown is not zero.

**API/UI:** Add bounded aggregate queries and an Admin/Debug usage panel by operation,
model, and date. Include failed, repaired, fallback, summary, research, and replay
attempts. Reuse aggregate definitions in 19 and quota reconciliation in 27.

**Acceptance:** Fake usage outcomes cover partial/unknown counts, cached tokens,
price changes, failures, and retries. Historical costs keep their price revision;
UI totals reconcile with attempts without changing conservative reservations.

### 25. Side-by-side model/config comparison

**Scope/dependencies:** Deferred, as recommended in the review. 21/23 can provide
serial comparisons first. Full comparison needs an approved server-side mechanism
to pin different evaluated profile/config revisions per experiment.

**Implementation when unblocked:** Add a comparison job with two isolated child
experiments, one frozen input/evidence manifest, and independently pinned profiles,
prompts, and configs. Both branches reserve budget before launch. Never expose
arbitrary model credentials or unapproved model IDs as client/debug overrides.
Distinguish fixed-context generation comparison from different-retrieval comparison.

**API/UI:** A Debug comparison view can show answer/citation diff, refusals,
latency, cost, and human review; there is no automatic winner or production
promotion. Cancellation fences both branches and preserves completed diagnostics.

**Acceptance:** Neither branch mutates the original turn or the other branch's
state; profiles obey data permissions; unknown cost/outcomes remain visible;
partial failures cannot be presented as a valid quality ranking.

## Platform and release

### [x] 26. Blob storage and private networking (Completed 2026-10-19)

**Scope/dependencies:** Release gate for the target replicated/school deployment.
Extend the Azure and scale handoffs; do not provision infrastructure as part of
this planning task. Confirm school topology and identity requirements first.

**Implementation:** Introduce an application storage Protocol with local and Blob
adapters for private originals, transcript imports, raw uploads, and durable
artifact files. DB references become opaque object keys plus checksum/size/version;
temporary processing paths stay disposable. Inventory existing absolute-path
callers in ingest, artifact reads/imports/downloads, and backup/restore before
migrating. Use a resumable checksum-verified copy, dual-read transition, and explicit
cutover. Upload outside DB transactions, publish the reference after integrity
checks, and clean unreferenced staged objects after a safe grace period. Preserve
all referenced historic revisions. Add private endpoints/DNS/egress rules to the
existing Bicep modules once the topology is approved; use managed identity with
minimum storage permissions.

**API/UI:** Keep existing private download contracts; stream or use short-lived
authorized delivery, never public blob URLs. Expose recoverable storage errors.

**Acceptance:** Replica A uploads and B reads matching bytes after restart; backfill
preserves old revisions; unavailable storage cannot publish a completed import.
Restore DB plus referenced object versions and deletion policy together. Verify
private DNS, allowed provider/IdP egress, probes, drain, and rollback in the actual
deployment, following the scale plan's release gates.

### [x] 27. Per-student rate limits and quotas (Completed 2026-10-19)

**Scope/dependencies:** Extend pending-work admission and per-operation budgets;
release gate before cohort expansion. 24 supplies auditable price inputs but
correct admission cannot wait for a dashboard.

**Implementation:** Add durable student/workspace and global budget windows for
query count, tokens, spend, outstanding work, and expensive job classes. Reserve
worst-case permitted usage atomically with operation admission; provider/tool
fan-out and retries consume the same reservation. Resolve request idempotency
before charging again. Count all APIs and batch children, not only chat. Add a
short-window submission limit where needed, including rejected-abuse traffic,
without treating polling as a query. Persist unknown-outcome reservations until
reconciled; no restart or UTC day rollover can erase already admitted liability.
Define whether student caps aggregate multiple workspaces; identity is server-derived.

**API/UI:** Return bounded effective limits and `429`/`Retry-After` before expensive
work. Explain daily quota versus temporary queue saturation. Reads, cancellation,
logout, and privacy workflows retain control capacity. Debug overrides cannot
raise institutional/global hard caps.

**Acceptance:** Concurrent replicas, duplicate request IDs, retries, cancellations,
unknown usage, price changes, day boundaries, and batch fan-out cannot overspend or
double-charge. Hard cap enforcement is tested separately from estimated reporting.

### [x] 28. Observability alerts (Completed 2026-10-19)

**Scope/dependencies:** Extend the current loopback-only `/metrics` and Azure
monitoring base. Define launch service classes and SLOs with the capacity plan.

**Implementation:** Add histograms/counters for queue wait/age, per-role pool waits
and timeouts, lease failures, provider error/429/timeout classes, completion latency,
embedding latency, worker capacity, storage failures, unknown usage, and budget burn.
Define aggregation per metric: process-local pools are summed or inspected per
replica, while DB-wide queue gauges must not be multiplied by replica count.
Scrape through an internal collector/sidecar and configure alerts as code. Each
release-critical alert has threshold/window, severity, owner, and a short runbook.
Missing telemetry also needs an alert; do not expose local-admin endpoints publicly
to make scraping convenient.

**API/UI:** Operational dashboards link to bounded metadata and deployment/runbook
information; student content and high-cardinality identifiers stay out of labels.
24 supplies cache/cost coverage; 19 supplies product-quality aggregates.

**Acceptance:** Controlled faults exercise alert delivery and recovery, not just
rule syntax. Load/drain checks verify sustained queue/pool failures and spend caps
are detected across replicas without duplicate fleet totals or leaked query text.

### [x] 29. Admin/Debug browser tests in required CI (Completed 2026-10-18)

**Scope/dependencies:** First implementation slice. Existing required frontend CI
already runs `npm test`; extend that suite to exercise the two additional bundles.
The real-API integration job is currently experimental and does not substitute for
required browser behavior checks.

**Implementation:** Extend `frontend/tests/browser-workflows.test.mjs` and its
offline fixtures to navigate `/admin` and `/debug`, using the existing multi-entry
build and shared session gate. Add cases for student rejection, admin reads and
mutations, last-admin errors, role/session changes, debug expiry, override draft
recovery, and Main isolation. A browser-test-only entry/harness must not introduce
production auth bypasses. Reuse HTTP client contract tests for payload rules rather
than retesting implementation details in the browser.

**API/UI:** Exercise the existing Admin/Debug routes through the backoffice client
and offline fixtures; no new production endpoints are needed for this feature.
Give the test harness isolated session and debug-token state per browser context.

**Acceptance:** Browser tests prove focus/dialog behavior, stale-edit draft
preservation, reload/logout cleanup, and that Debug credentials/headers never
reach Main. Backend tests in `test_admin_debug.py` continue to prove server
authorization. New browser coverage runs in the existing required frontend job;
repository branch-protection configuration must also require that job if not
already configured. Stabilizing/promoting the experimental integration job is a
separate follow-up, not a reason to leave these checks optional.

### 30. Accessibility and i18n pass

**Scope/dependencies:** Broader pass later; include basic accessibility in every
earlier UI. Confirm supported languages, translation owner, and accessibility
target with the institution before claiming completion against a standard.

**Implementation:** Inventory Main/Admin/Debug keyboard routes, dialogs, tables,
streaming announcements, citations, editors, errors, and downloads. Fix semantic
controls, names, focus return, keyboard reachability, contrast, zoom/reflow, and
reduced-motion behavior for both themes. Add message catalogs and locale-aware
dates/numbers/plurals. Preserve course codes, exact institutional quotations,
citations, and source edition identifiers. Translate explanations only under an
explicit product policy; a translated explanation does not replace official rules.

**API/UI:** Keep error codes stable and map display text centrally. Use configured
user/locale time presentation while storage timestamps remain unambiguous. Include
long-string layouts and distinguish UI language from document language.

**Acceptance:** Extend browser checks for tab order, focus recovery, live-region
behavior, zoom/reflow, and locale expansion. Perform manual screen-reader and
keyboard checks across all three surfaces; automated checks alone do not establish
accessibility. No runtime translation of evidence is treated as authoritative.

## Implementation slices and unresolved decisions

Start with small reviewable changes rather than one feature branch for all 30:

1. [x] Reconcile wave 0 against current code/corpus and add required Admin/Debug browser
   coverage (29). Avoid redoing repairs already delivered in migrations 034/035. (Completed 2026-10-18)
2. [x] Ship filtered audit browsing/export (18), then cache/cost accounting views (24). (Completed 2026-10-19)
3. [x] Slice 3: (Completed 2026-10-19)
   - [x] Applicability notices (7) (Completed 2026-10-19)
   - [x] Template-based advising summary export (5) (Completed 2026-10-19)
   - [x] Memory inspection followed by the separately fenced reset behavior (6) (Completed 2026-10-19)
4. [x] Slice 4: Define and implement access/retention/rollout controls (15/16/17); integrate
   quotas, storage migration, and alerts with the existing release handoffs (27/26/28) (Completed 2026-10-19):
   - [x] Finer roles and time-boxed support access (15) (Completed 2026-10-19)
   - [x] Retention, student export, and deletion (16) (Completed 2026-10-19)
   - [x] Feature flags and gradual rollout controls (17) (Completed 2026-10-19)
   - [x] Per-student rate limits and quotas (27) (Completed 2026-10-19)
   - [x] Storage layout migration (26) (Completed 2026-10-19)
   - [x] Observability runbooks and alert thresholds (28) (Completed 2026-10-19)
5. Build source and program review (11/12), then a synthetic manual/CSV academic
   record workflow (1), then a named-program deterministic audit pilot (2).
   Bring in real records only once the applicable data controls are ready.
6. Add what-if (3), catalog batch refresh (10), and the feedback-to-eval pipeline
   (8/14/21/22/23/19). These can be split around their explicit dependencies.
7. Reassess gated/deferred features using actual product decisions and data
   availability; implement 4/9/13/20/30 and eventually 25 only within that scope.

Decisions that change implementation scope, to resolve at the relevant slice:

| Decision | Needed before | Conservative working assumption |
|---|---|---|
| Who can verify academic attempts, credit awards, and exceptions? | 1 verification / 2 authoritative audit | Student entry/import remains unverified; no automated equivalence decisions |
| Initial programs, catalog cohorts, core/track rules, and supported predicates | 12 rule release / 2 pilot | Small program audit with explicit unresolved coverage |
| Record/feedback/transcript retention, legal holds, backup expiry, export scope | Real student-data collection / 16 | Use synthetic data until the policy and its controls exist |
| Support approval authority and advisor consent/resource scopes | 15 content access / 9 | No blanket advisor or auditor transcript access |
| Offerings/section source, refresh cadence, and approved acquisition method | 4 verified terms | Dependency stages and visibly unverified scenarios only |
| School identity protocol and private-network topology | 26 institutional rollout | Retain existing auth seam; do not implement a guessed protocol |
| Model price schedule, daily/global caps, service-class SLOs, alert owners | 24 pricing / 27/28 release | Unknown cost stays unknown and reservations remain conservative |
| Required suite thresholds, semantic reviewer, and prompt promotion authority | 22/23 production promotion | No promotion until the applicable checks and human review pass |
| Languages and institutional accessibility target | 30 completion claim | Accessible English UI and localization-ready structure first |

Effort labels in the review are directional, not estimates from measured work.
Estimate each slice after the decisions above and a schema/fixture spike. In
particular, 2 includes a new rule/allocation engine, 10 includes coherent staged
publication, and 16 includes cross-store deletion/restore semantics; none is a
small UI extension.

For each implementation PR: update the architecture contract, run focused behavior
checks for the changed domain, run schema/type drift checks when adding API fields,
and complete the standard backend/real-DB/frontend checks before release. Storage,
quotas, recovery, and capacity additionally require the existing production-like
gates. This planning change itself does not run tests or establish release readiness.

The unnumbered exclusions in the review remain excluded: autonomous registration
or messaging, native mobile/PWA, public social sharing, SIS writes, automated
equivalency decisions, model-judged release authority, expanded executable HTML,
generic table CRUD, multi-institution tenancy, and unsupported availability promises.
