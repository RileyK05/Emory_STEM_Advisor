# Advising quality and data management: implementation handoff

Status: phases A and B implemented 2026-10-22; phases C–G planned. This
document does not claim the remaining features are implemented.
Scope reflects the user's edited decisions in the October 22, 2026 conversation.
This is an execution handoff. [The architecture](../architecture.md) remains the
single design contract; update that contract for approved implementation changes.

## Agreed scope

1. Admins can add, update, remove and restore individual courses, with traceable
   sources and consistent updates to the data used by the advisor.
2. Students get useful course and program comparison charts, incorporating goals
   and coursework reported within the conversation.
3. Comparisons explain small but important differences between similar majors
   and tracks, including programs within the same department.
4. Improve instructions, context selection, tools and completion checks using
   Anthropic's guidance. Keep deterministic academic checks in application code.
5. Continue acquiring grounded external material, uploading it, reviewing it and
   using it with correct source attribution and applicability.

Permanent student profiles are deferred. Model selection, cross-model benchmarks,
provider replacement and automatic model fallback are deferred. Use the existing
configured model for functional verification. Keep PostgreSQL, current provider
seams, retrieval and deterministic prerequisite planning.

Coverage remains all Emory schools, with Oxford kept as a distinct campus and
exact course identity. Use the captured Spring 2027 Atlas evidence within its
actual scope; do not treat it as verification of another term or of records
that still carry catalog-fallback authority.

## Starting point: existing pieces and actual gaps

| Area | Existing implementation | Work still needed |
| --- | --- | --- |
| Admin | `backend/app/admin/source_review.py`, `catalog_refresh.py`, admin routes and frontend source/refresh panels | Individual-course CRUD, reviewed corrections, removal/restore and publication of their downstream effects |
| Course data | Atlas import/authority/offering modules, parser, lifecycle and requirement holds | A supported admin editing path that preserves source history, source authority and deterministic graph consistency |
| Comparisons | `backend/app/advising/comparison_service.py`, `/api/comparisons`, `ProgramComparisonPanel.tsx` | Useful charts, evidence per comparison cell, course comparisons and a conversation-scoped path |
| Degree rules | `backend/app/programs/rules.py`, review/publish services and deterministic audit engine | Confirm reviewed coverage for each compared program; distinguish raw mentions from executable requirements |
| Conversations | Private saved turns, notebook, archive/recall, corrected history selection and candidate evidence retention | Reliable course-status corrections across long conversations, explicit request coverage and accurate reporting of executed checks |
| External material | Upload/extract/chunk/embed pipeline, provenance manifests, advising-source allowlist and source review | A repeatable curation workflow for new material, better table/syllabus handling and targeted coverage expansion |

Important integration gap: the existing saved-comparison service invokes the
academic-record/degree-audit path, which can create a workspace academic record.
Do not use that behavior for ordinary conversation comparisons. Reuse its pure
comparison/evaluation components where suitable; provide a conversation-scoped
entry point that does not create or modify a permanent academic record.

## Implementation order

| Phase | Deliverable | Completion gate |
| --- | --- | --- |
| A | Document the contracts and inventory actual data coverage | Defined publication, comparison and completion semantics; existing partial features identified |
| B | Admin course maintenance from draft through effective publication | Authorized edit/remove/restore works through API and UI, including downstream invalidation |
| C | Conversation-scoped course comparison service and chart | Corrected reported coursework drives evidence-linked comparisons without profile writes |
| D | Related-major comparison and reviewed program families | Shared requirements and exact differences are visible for real, separately identified programs |
| E | Prompt/context improvements and request completion checks | Required work is completed or specifically reported unavailable; check reporting matches execution |
| F | Repeatable external-document curation and ingestion | Reviewed material becomes correctly scoped, citable evidence and can be replaced safely |
| G | Integration verification and handoff | Acceptance scenarios pass; limitations and unfinished items are explicit |

External source collection can proceed alongside B-E. Publication and the charts
must use the same source revisions and applicability rules. Each phase includes
its own appropriate checks; G connects the completed workflows.

## B. Admin course maintenance

### Behavior

Provide a Courses surface in the existing Admin bundle. Support search/filter by
school, campus, subject, term, authority and lifecycle, then add, edit, remove,
restore and inspect history. Expose title, description and raw requisite wording
with the source evidence, scope, edit reason and review status.

Removal means retirement from the current active course set. Preserve historical
sources and citations so old answers remain readable. Restoration is a reviewed
publication, with a preview of what will become active.

### Implementation

1. Define versioned course changes with actor, reason, base revision, original and
   proposed values, source references and draft/published status. Use existing
   admin authorization and audit mechanisms; enforce them on the server.
2. Represent imported baseline records, reviewed corrections and retirement
   decisions explicitly. A manual correction must not silently claim to be an
   observed Atlas statement. New courses without verified institutional evidence
   remain clearly classified and cannot acquire Atlas authority through editing.
3. Make preview show changed fields, affected prerequisites, related program
   references and stale derived artifacts. Parse raw requisite edits with the
   deterministic parser; display unresolved wording without inventing edges.
4. Publish through immutable evidence revisions and existing source/publication
   fences. Do not patch mutable course rows while leaving indexed text or graph
   facts inconsistent. Stage required extraction/indexing work before activation.
5. Update or invalidate affected course text, embeddings, keyword results,
   prerequisite nodes/mentions, topic builds and saved scoped results as required.
   Retired requirements referenced by another course must remain an explicit
   unresolved dependency; retirement cannot satisfy or erase that prerequisite.
6. Handle a later Atlas import explicitly: retain the admin change/history,
   compare it to the new baseline, and queue conflicts for review. Do not silently
   resurrect a removed course or apply an old correction to changed evidence.
7. Add typed API contracts, named SQL queries, worker jobs where needed and the
   Admin UI. Allocate new migrations from the actual head at implementation time;
   never edit applied migrations or the baseline.

Acceptance: edit, publish, retire and restore a course through the UI; verify the
advisor sees the effective revision and historical citations still resolve.
Unauthorized edits, stale concurrent edits and failed indexing must leave the
published state intact. A baseline refresh must surface conflicting edits.

## C. Conversation-scoped comparisons

Use a bounded comparison service that assembles the evidence and deterministic
results needed for selected courses. The advisor should not have to rediscover
and manually join the same facts through many separate model calls.

Inputs include conversation identity, exact selected course/program identities,
applicable term/cohort where known, current goals/preferences and reported course
statuses. Keep completed, in-progress, planned and explicitly not completed
distinct. Preserve the originating user turn and correction order. Hypothetical
changes are scenario overlays; they do not rewrite the student's reported history.

Store this context only with the existing private conversation/scenario. A new
chat must not inherit it. Do not create or mutate an academic record as a side
effect. Reported coursework remains unverified unless separate evidence verifies
it; an assistant summary cannot supersede the student's own correction.

Return typed comparison rows containing:

- Exact course identity, campus, description and content evidence.
- Raw prerequisites, parsed support, missing alternatives, conditions and actual
  executed preparation-check references for the reported completed set.
- Required core, choose-one option, approved elective, excluded or unresolved
  applicability for each selected degree/track and source edition.
- Content-based fit with the stated goal, with judgment separated from sourced
  facts and with meaningful objections or alternatives.
- Evidence references, source authority, applicability and any missing coverage.

Show a compact, accessible chart with expandable sources and an explanation of
why the options differ. Support two or three program columns initially and a
bounded course shortlist; a larger family can be browsed without merging programs.
Maintain mock/HTTP client parity and current saved-chat behavior. Keep evidence
IDs and internal execution details out of ordinary product copy.

Acceptance: changing a course from completed to planned changes the preparation
result; an unrelated chat receives no profile data. CS 350 can be shown as required
core and CS 377 as an option using actual BA/BS evidence. Unknown term availability
or degree applicability remains unknown. Prerequisite support is never presented
as registration clearance or a verified graduation plan.

## D. Closely related majors and tracks

Add reviewed program-family metadata for discovery and comparison. Confirm exact
active program identities and official titles before seeding the user's math,
applied-math/statistics and physics examples. Department membership alone does not
establish interchangeability; preserve school, campus, degree, track and edition.
Keep historical QTM/DATASCI naming separate from evidence of course equivalence.

Extend comparison output and the existing panel to show shared foundations,
additional required courses, elective/choose-N differences, concentrations,
credit/grade constraints and differences in content emphasis. Published reviewed
rules can support deterministic requirement differences. Raw program mentions
can support a labeled mention comparison, with unresolved semantics disclosed.
Shared mentions never establish double counting, saved credits or an approved
substitution.

Allow a conditional recommendation based on the student's preferences. Explain
which preference favors which option and what information would change the
recommendation. Avoid invented fit scores, universal best majors and employment
or admissions guarantees.

Acceptance: compare two similar programs with a shared foundation and a small
verified difference; preserve that difference in the chart and explanation.
Changing a user's goal can change the recommendation without changing source
facts. Incompatible editions or incomplete rules prevent unsupported rankings or
credit claims while still allowing a useful scoped factual comparison.

## E. Instructions, context and completion

Follow the principles in Anthropic's
[Building effective agents](https://www.anthropic.com/research/building-effective-agents)
and [Effective context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents):
start with simple composable workflows, clear instructions, usable tools and
carefully selected context. These are engineering references, not a requirement
to adopt Anthropic models, SDKs or a new agent framework.

Keep shared safety/source rules concise. Provide task-specific context for
factual lookups, course comparisons and subjective program decisions. Supply a
few representative examples of factual pushback, a legitimate correction from
completed to planned, and a preference change that revises a recommendation.
Avoid growing the system prompt into a list of individual test-case patches.

For each request, track the required deliverables separately from optional
research. For example: two recommendations, BA and BS applicability, course
descriptions and checks against the latest reported completed set. The controller
must reserve work/evidence capacity for these obligations before broadening its
research. Derive completion status from covered obligations and available evidence;
the model's self-assigned complete label is insufficient.

Render check execution status from the actual ledger: not requested, not executed,
executed and supported, or executed but unavailable in the final context. Do not
allow the last category to become the statement that no check ran. Preserve the
current strict evidence checks, bounded repairs and server-owned candidate support.
Source-based assertions embedded inside questions or judgments still need review.

Acceptance: a mixed BA/BS plus course-check request finishes all required parts
or names each actual missing part. An executed-but-omitted check is accurately
described. The advisor resists false prerequisite pressure, accepts legitimate
corrections and gives useful conditional answers without unnecessary refusal.

## F. Continued acquisition and external uploads

Build on the existing upload pipeline, verified provenance manifests, source
review and explicitly reviewed advising-source allowlist. The intended workflow
is ongoing collection and upload of grounded material; arbitrary live web
fetching is not a prerequisite for this work.

Prioritize complete elective/track rules, official syllabi and learning outcomes,
specific graduate admissions requirements and dated career/occupational references.
Inventory existing coverage before acquiring duplicates. Preserve originals,
hashes, extraction locators and source URLs; record publisher, dates, issuing unit,
document role, applicability and review decisions.

An ordinary upload cannot declare itself institutionally verified. Keep unknown
edition/cohort applicability explicit. Preview extraction and table structure,
including qualifiers, columns, core versus track supplements and exceptions.
Trusted human review/import is required before authoritative classification or
publication. New origins and replacements follow existing staged/atomic source
publication and history rules.

Apply authority to the relevant claim: captured Atlas restrictions govern the
captured course; program requirements govern degree applicability; specific
admissions documents govern their admissions population/cycle; career references
support scoped career facts. Outside career material cannot rewrite an Emory
prerequisite. Uploaded content is evidence, never instructions to the system.

Acceptance: upload, preview, review, publish, retrieve and cite an external source;
replace it and verify current retrieval changes while an older citation remains
readable. Reject self-classification and injected instructions. A syllabus can
support content fit without becoming proof of approved elective credit.

## Verification and definition of done

Extend existing behavior tests rather than introducing parallel suites for the
same invariants. Use fake providers for offline regressions and temporary real
Postgres only where behavior depends on SQL. Run contract drift checks when API
types change, and frontend type/build/format plus relevant behavior checks for
UI work. Live model calls remain manual checks using the configured model.

Keep a small reusable set of scenario inputs with the manual runner when needed.
Run output, transcripts, ledgers and diagnostics stay in the gitignored
`docs/audit/tests/` folder; do not commit test results. Local historical audits
are useful investigation evidence but are not required checkout dependencies.

Final acceptance includes:

- Admin edits/removal/restoration, failed publication and a conflicting refresh.
- Course comparisons before and after completed/planned status corrections.
- Similar-major comparisons preserving small requirement differences and editions.
- Long conversations beyond the recent-history view and beyond 50 saved messages,
  including early goals, later corrections and a separate-chat isolation probe.
- Pushback on false facts and on a legitimately incomplete previous response.
- Mixed comparison requests completing their checks and both degree variants.
- Reviewed external uploads, replacements, conflicting sources and citation use.
- Real frontend/saved-stream integration, reload/error recovery and readable charts.

Completion means the working API, UI, background publication, evidence/provenance,
invalidation and regression checks connect end to end. Report unresolved source
coverage explicitly. Do not mark a feature finished because a route exists, a
mock renders, a response is HTTP 200, or a narrow recovery test passes.

## Implementation boundaries

Preserve unrelated working-tree changes. Never let a model create prerequisite
edges, infer missing academic conditions, rewrite similarity nudges, or promote
uploaded claims into verified rules. Use named SQL, provider/embedder seams,
workspace authorization, immutable evidence identity, source revision fences and
the existing job/budget mechanisms. Update architecture and mirrored API types
alongside implementation. This handoff starts no deployment or model change and
does not create a goal; it is ready for the user to invoke later in goal mode.
