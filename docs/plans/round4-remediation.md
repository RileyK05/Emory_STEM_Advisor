# QA round 4 remediation plan

Status: partially implemented 2026-10-25 against the current tree. Packages A,
B, C, D and the backend portion of H are implemented and covered by the offline
suite; E/F data publications, G research completeness, and the remaining Low/Info
items are still open; the sidebar archive control is implemented. See
[the audit status](../audit/README.md#round-4-remediation-2026-10-25). No data
publication, deployment or live regrade was performed.
Prepared October 25, 2026, against `f89f5e6`, the same HEAD tested in
[REPORT-round4.md](../audit/REPORT-round4.md). The report supplies the findings;
the code inspection below refines their causes and proposed fixes.
[Architecture](../architecture.md) remains the implementation contract.

The order is: recover failed conversations and backups, repair validation without
losing grounding, bound upload work, restore policy retrieval and data authority,
finish research coverage, then close conversation lifecycle and platform gaps.
Each package is independently reviewable and has its own acceptance gate.

## Scope and starting evidence

R4 reports 682/976 default-config answers passing, 119 guard withholds, 274
overhedged answers and five published factual errors. These categories overlap;
they are not additive. The suite passed 1,407 backend tests, 98 frontend checks
and six browser/API integration checks with an explicit Chromium path. Passing
these checks did not cover the real worker failure path or answer correctness.

This plan covers all new issues EMO-054–058, remaining partial/unfixed issues,
the reported factual errors, and findings that still need a retest. Keep fixed
issues closed unless a regression reproduces. It complements
[the advising/data handoff](advising-quality-and-data-management.md); R4 supplies
the current remediation priorities. Comparison charts, new permanent student profiles,
model changes and deployment are outside this remediation scope.

Important differences between the report's suggestions and current code:

- `scripts/worker.py` really does omit `on_failure` from `AdvisorExecutor`.
  `app/jobs/worker.py` also has separate execution, cancellation, publication
  failure and lease-recovery paths. Forwarding one hook alone cannot close every
  way a pending request becomes orphaned.
- A reverse graph query already exists: `prereq_downstream` in
  `backend/app/db/queries/prereq.sql`, called by the prerequisite seam.
  `backend/configs/retrieval.toml` enables only `self` and `upstream`. Investigate
  routing, ranking and coverage before adding an index or another graph.
- Markdown extraction already labels section locators. Continuation chunks lose
  useful heading terms in their body/search representation. Preserve that label
  through retrieval rather than inserting invented text into exact source spans.
- The architecture currently requires course identities in the paragraph's
  cited evidence. All-context identity discovery and student mentions are useful
  repair hints, but do not establish a requirement. Do not implement the report's
  unconditional “union of user turns + context, then caveat every miss” literally.
- R4's `r4/live`, gold sets, overrides and grading scripts are paths on the QA
  Linux host, not files supplied in this checkout. That host's running services,
  credentials and database state are not assumptions about this Windows machine.
- `.github/workflows/ci.yml` still marks real frontend/API integration
  `continue-on-error: true`; green CI alone is not a release gate for it.

## Work packages and order

“First” denotes execution priority, not a new severity classification. R4 has no
remaining reported Blocker. Finish the High findings before broad tuning, while
treating incorrect eligibility/policy advice as a priority correctness issue.

| Package | Priority | Findings | Dependency / deliverable |
| --- | --- | --- | --- |
| A | First | 054, 047, 058 | Terminal conversation recovery, bounded DB recovery and usable backups |
| B | High | 041/056, 022 | Course identity recognition and evidence repair |
| C | High | 043, 046, 010 | Echo/vacuous-output rejection and source authority |
| D | High | 044; embedding truncation | Bounded upload indexing through publication |
| E | Next | 007, 008, 009, 057 | Policy/program retrieval and follow-up correctness |
| F | Next | 030, 038, 028, 013, 019, 014 | Held requirements and deterministic corpus repairs |
| G | Next | 034, 035; reverse prerequisites and list errors | Complete bounded research and exact requirement lists; uses E/F |
| H | Next | 055; artifact capacity | Owner cleanup, pagination and recoverable storage limits; uses A |
| I | Follow-through | Remaining Low/Info and unverified findings | Portability, operational clarity and retests |
| J | Final | All packages | Integrated verification and issue-by-issue closure |

Start with a small baseline/reproduction slice, then implement A, B, C and D as
separate changes. E and F precede G's final quality evaluation; H builds on A's
state transitions. I can be split by affected subsystem. Update the relevant
architecture sections and mirrored API contracts within each change, not in a
single cleanup pass after the implementation.

## Before implementation

1. Preserve existing working-tree changes, including the newly added report and
   deletions of older audit documents. Confirm HEAD and migration head again at
   implementation time; do not reserve migration numbers in this plan.
2. Reproduce each defect with the smallest existing fake-provider or saved-data
   case before changing it. Search existing tests for its invariant and extend
   those tests, using shared fakes and helpers. Do not build a second QA suite.
3. Recover R4 inputs/golds if available. Otherwise reconstruct small reusable
   cases from the report with exact local source excerpts and mark the full
   976-item comparison unavailable. Never invent missing grades or source facts.
4. Record the tested configuration, corpus revisions, source applicability and
   model identity when doing manual quality checks. R4's 568-course/234-source
   corpus is a comparison fixture, not the project's desired coverage limit.
5. Use a disposable `_test` database distinct from the application database.
   Baseline checks must not start real model downloads or touch the dev corpus.

## A. Settle failed turns and restore backup availability

Touchpoints: `backend/scripts/worker.py`, `backend/app/jobs/worker.py`,
`backend/app/jobs/store.py`, `backend/app/conversations/{store,worker}.py`,
conversation routes, `backend/app/db/queries/{conversations,backup}.sql`,
`backend/app/ops/backup.py`, and `frontend/src/components/chat/useSavedChat.ts`.

1. Put the composed executor in a reusable production construction path and
   dispatch failure handling by job kind. Exercise that same composition in the
   existing conversation/job tests; do not test a manual script or call the
   cleanup hook directly as the only proof. Preserve artifact execution behavior.
2. Define one idempotent settlement operation for a request whose bound job is
   terminal. Cover execution failure, publication failure, cancellation and an
   expired final lease. Persist a safe terminal result, clear only the matching
   pending pointer, and preserve immutable turns and sequence/replay semantics.
3. Close the gap between terminal job publication and conversation settlement:
   settle transactionally where possible and reconcile after crashes otherwise.
   A failed cleanup or temporary DB outage must be retried by housekeeping.
   Event inspection must eventually return consistent terminal request/job states.
4. Add bounded reconciliation for existing orphaned requests, including
   synchronous requests with no job. Use a persisted operation/request deadline
   and ownership fences, not a blanket age cutoff for healthy running work.
   Expiration must fence late publication before allowing a subsequent turn.
   Preserve unknown started-call usage; never automatically replay an ambiguous
   provider call or add attempts to single-attempt conversation jobs.
5. Log safe failure type, job/request IDs and stage in operator diagnostics.
   Keep provider bodies, prompt content, secrets and arbitrary exception text out
   of public events and persistent job errors; avoid raw exception logging that
   reintroduces the leak R4 found absent.
6. Reconcile stale requests in a separate write transaction before entering the
   maintenance backup's read-only snapshot. Keep the live-work backup gate.
   Report bounded blocker counts and operator-visible IDs/kinds, rather than
   bypassing pending work or requiring every owner to click abandon.
7. Reproduce the idle-pool reconnect delay separately. Tune bounded acquisition
   and reconnect behavior only after measuring it; give every transient DB 503
   a consistent Retry-After. Avoid nested same-pool borrows.

Acceptance: through the real composed worker, provider errors, malformed/empty
responses, deadline expiry and publication failures leave a terminal failed turn;
the next fresh turn works, replay starts no extra call, and reload/reconnect
removes the pending bubble while preserving the draft. Worker loss and DB outage
converge after recovery without owner action. Abandon versus late success has one
winner. Backups succeed after stale-work reconciliation, still refuse live work,
and restore retained usage, histories and citations correctly.

Extend `test_conversations.py`, `test_jobs.py`, `test_persistence_backup.py` and
the existing saved-conversation case in `frontend/tests/live-api-integration.test.mjs`.

## B. Repair the course-code guard without weakening requirement evidence

Touchpoints: `backend/app/generation/answer.py`, prerequisite code recognition,
reviewed alias data, `backend/app/api/query_service.py`, and `test_generation.py`.

1. Share deterministic recognition across validation and repair: spaced/unspaced
   codes, subject carry in lists, suffix shorthand and explicitly printed ranges.
   Preserve suffix and campus distinctions (`444`/`444W`, `CS`/`CS_OX`). Support
   QTM/DATASCI mappings only at the exact scope supported by reviewed evidence;
   historical renaming is not automatic academic equivalence.
2. Recognize student reports such as “I took”, “I've taken/done”, “I have” and
   “Done:”. Parse report and rule clauses separately: a later question containing
   “satisfy” must not invalidate an earlier attributed transcript statement.
   Completed, planned and denied completion stay distinct. Only exact user turns
   grant report attribution; assistant summaries cannot grant it.
3. Recognize absence/refutation wording including “does not include/contain” and
   “no course numbered”. Apply that exemption per clause so a negative opening
   cannot authorize a later unsupported positive requirement.
4. Use all authorized, current supplied context to discover identity matches and
   suggest the relevant passage to the existing bounded citation repair. Do not
   load the whole corpus into an identity allowlist. A student mentioning
   `CS 999` may be quoted or corrected, but cannot authorize “CS 999 is required”.
5. Distinguish an attribution/identity-format mismatch from a genuinely
   unsupported institutional claim. Permit attributed reports and accurate
   nonsupport statements; preserve useful supported portions when possible.
   A warning must never be the only protection on a fabricated positive claim.
   Keep paragraph citations and requirement evidence checks; update the
   architecture and existing behavior tests explicitly if their policy changes.
6. Pass the same subject registry/history to tool-path repair and normal answer
   validation. The existing citation repair defaults off: enable one narrowly
   scoped repair for identity mismatches resolvable in the supplied evidence,
   after its regression gates pass. Preserve the single-repair call/token/time
   budget and measured usage accounting; exhaustion must not grant a fallback
   citation. Give the UI a safe, actionable limitation when repair fails.

Acceptance: reported transcripts, false-premise corrections, CHEM suffix lists,
`Math 411–412`, Oxford identities and reviewed QTM/DATASCI variants stop causing
bare refusals. An identity present in another supplied block reaches the correct
citation through repair. Invented positive `CS 999`/`MATH 999` claims, false
suffix substitutions and claims supported only by user assertions still fail.
Run variants through default, tools, research and saved-stream entrypoints.

## C. Reject instruction echoes and misleading evidence use

Touchpoints: `backend/app/generation/{answer,claims,claim_review,research_final}.py`,
verified source metadata, response redaction and existing generation/API tests.

1. Normalize citation insertions, punctuation, Unicode and underscore-separated
   words before prompt-overlap checks. Add bounded approximate sequence matching
   and bounded decoding of plausible encoded blocks for the reported base64 case.
   Calibrate on legitimate short answers and source quotations. A canary may
   supplement the check; it cannot prove that arbitrary prompt leakage is solved.
2. Reject citation-only and stock content-free replies. Add structural checks
   for meaningful answer content plus manual semantic grading. Do not treat
   shared words, a minimum length or a valid `[1]` as proof of factual support.
3. Apply verified source authority to the claim being made. An injected upload's
   “C- counts” must not become institutional advice merely by adding a hedge.
   Use the applicable official rule, identify the conflict when relevant, or
   explain that authoritative evidence is unavailable. Keep uploads untrusted.
4. Do not attach irrelevant chunks to generic refusals or absence claims.
   Cite a source when describing what it actually says or conflicts with, not
   as decoration on “I cannot answer”. Preserve the zero-candidate refusal rule.
5. Remove non-cited retrieval IDs from student trace payloads while retaining
   IDs required for actual citations and owner-authorized saved evidence.
   Keep full operator traces behind existing capabilities (EMO-053 residue).

Acceptance: the four R4 echo transformations, literal echo and vacuous cited
answers are withheld across sync/stream paths; legitimate concise answers still
publish. Query/document injection remains resisted, the C- memo does not supply
policy, and ordinary students see only the evidence intended for their response.

## D. Bound upload indexing, including embedding

Touchpoints: `backend/app/ingest/pipeline.py`, `backend/app/embeddings/base.py`,
`backend/app/config.py`, corpus upload routes, existing job handlers and UI upload
status. Preserve trusted-curator versus ordinary-user ingestion distinctions.

1. Enforce one configured deadline across extraction, tokenization, embedding
   batches and publication. Check before/after each batch and before activating
   results. Lower the user chunk cap from 2,048 to a measured safe bound; 300 is
   an initial benchmark candidate, not an unexplained permanent constant.
2. Embed in bounded batches so a whole 1,075-chunk document is not submitted in
   one uninterruptible allocation. Enforce per-workspace/global indexing
   admission limits; moving the same unbounded work to a worker is insufficient.
   Do not release an execution slot while its underlying batch still runs.
3. Move ordinary user indexing into the existing durable job system with typed
   progress/failure and a recoverable UI. Update the upload response, mock/HTTP
   clients and integration flow together. Use source-revision and ownership
   fences, duplicate-byte idempotency and last-good-index preservation.
4. Be explicit about cooperative deadlines. Between-batch checks cannot preempt
   a hung native embedding call. Bound batch size and concurrency; if measured
   cancellation latency is unacceptable, isolate embedding work in a terminable
   process before claiming a hard execution-time guarantee.
5. Measure the exact embedder input with its special tokens against the effective
   embedding ceiling. Split or reject oversized chunks before embedding; do not
   silently truncate. Coordinate heading enrichment from E with this limit.
   Keep pinned dependencies and the trusted Granite assembly unchanged.

Acceptance: the under-cap R4 upload shape cannot pin an API request for 145 s;
deadline/overload failures are terminal and safe, parallel uploads remain bounded,
and a failed reindex retains old citations. Oversized tokenizer inputs cannot
silently produce truncated vectors. Use fake clocks/embedders in existing ingest
tests and a manual CPU/RSS measurement for actual capacity; no models in pytest.

## E. Retrieve policy rules alongside programs and retain follow-up intent

Touchpoints: `backend/app/retrieval/{fusion,retrieve}.py`, keyword seam and
`backend/app/db/queries/keyword.sql`, extraction/chunk metadata, conversation
query rewriting, retrieval config/evals and their existing tests.

1. Carry explicit policy obligations separately from named programs. For mixed
   queries cap reserved program slots at `max(0, cited_set_size - 2)` as an initial
   rule. Also ensure relevant policy candidates survive keyword ranking and
   candidate limits: free slots alone can still be filled by more program chunks.
   Preserve exact-program retrieval for program-only questions and handle small
   citation budgets without negative or duplicate allocations.
2. Propagate real section headings to continuation search/embedding inputs and
   context labels while preserving exact source text, locators and offsets.
   Add bounded adjacent-section reads where a rule spans chunks. Avoid a heading
   match promoting every continuation equally over the actual rule paragraph.
3. Handle CS BA → “And the minor?”, multiple-program back-references, explicit
   topic changes, credit/GPA corrections and later summaries. Keep the latest
   user correction and selected program identities; do not merge BA/BS/minor
   evidence or carry unrelated policy scope into a new topic or conversation.
4. Normalize full-width/zero-width code forms in a comparison/search view;
   preserve exact user/source bytes. Use bounded typo handling for program names
   without silently choosing another degree/campus when the match is ambiguous.
5. Reindex affected sources with versioned derived inputs if needed. Stage and
   publish replacements atomically through current-source fences; never rewrite
   historical chunks or change a chunker's meaning under an unchanged version.

Acceptance: direct and student-situation phrasings retrieve the 90-hour minor
rule, S/U allowance/deadline and repeat policy, including the applicable exceptions
and school/cohort. An 88 → 91 → 85 correction changes the policy conclusion.
CS-minor details beat the minors index, CS BA evidence persists in long follow-ups,
and program-only complete lists retain their existing recall. Extend fusion,
keyword/retrieval, extraction and conversation behavior tests rather than adding
tests that assert slot constants or SQL strings.

## F. Surface held rules and repair the corpus deterministically

Touchpoints: `backend/app/advising/requirement_holds.py`, advising reads/tools,
prerequisite retrieval, `backend/app/programs/{catalog_pdf,requirements}.py`,
verified import/reconciliation and the existing Admin review workflow.

1. Keep held requirements excluded from eligibility reasoning, but return an
   explicit status: “this requirement is under review; confirm before relying on
   it”. Distinguish held, unstated, unparsed, conflicting and unavailable rules
   across default/tools/research/stream responses. Show the status in the UI;
   do not expose private curator notes as student evidence.
2. Investigate `CS 171 ← MUS 221` against exact saved Atlas evidence and master
   hash. Use a scoped hold while unresolved. A curator may publish a verified
   parser/manual correction; a model cannot infer a replacement edge. Preserve
   the captured statement and conflict history even if it appears implausible.
3. Fix CHEMBA/CHEMBS front-matter leakage into Classics and the Anthropology &
   Human Biology `degree=85`/`kind=other` parse. Rebuild from saved originals and
   verify affected neighboring boundaries, program identities, bindings and
   reviewed releases before staged publication. Do not patch generated Markdown
   alone or reinterpret an hours field as a degree.
4. Address observed PDF kerning/ambiguous requisite parser forms with exact
   fixtures in `test_extract.py`, `test_catalog_scrape.py`, `test_programs.py`
   and `test_requisites.py`. Expand coverage only where the text supports it;
   unresolved forms remain explicit review gaps.
5. Keep source disagreements visible: recommended statistics versus required
   pre-health courses, QSS naming history, DS-minor exclusion-list differences,
   and implausible Math BS hours/course counts. Use claim-specific authority and
   applicability; flag source anomalies rather than inventing corrected numbers.

Acceptance: a hold never becomes “no prerequisite stated”; release updates future
answers without erasing historical evidence. The anomaly cannot be presented as
an approved unqualified prerequisite. Parser repairs preserve program boundaries
and exact-source binding. No LLM edits the prerequisite graph or similarity nudges,
and no catalog import replaces an Atlas-primary master.

## G. Finish research reads, reverse prerequisites and exact lists

Touchpoints: `backend/app/generation/research_{controller,tools,final}.py`,
`backend/app/advising/research_repository.py`, operation budgets, prerequisite seam,
sequence/preparation evaluators and reviewed program evidence.

1. Track mandatory requested facts/lists independently of the model's completion
   label. Follow continuation cursors until required coverage is complete or a
   concrete limit prevents it. Prioritize the relevant section over indiscriminate
   full-document reading; reserve finalization tokens/calls/time up front.
   “8 of 33” cannot count as a complete elective or capstone answer.
2. Propagate remaining operation time through provider, tool and document-read
   work. Check before and after expensive operations and fence late publication.
   Finish with an accurate partial result while time remains, or return the
   existing typed deadline outcome. Preserve measured and unknown usage.
   Prove budget behavior with FakeClock, then measure real-provider cancellation
   overshoot; do not merely raise the 240 s limit to hide 265 s behavior.
3. Route reverse-prerequisite intent to the existing downstream query and
   prioritize those results through fusion. Check query performance before any
   new index. Return courses that reference the completed course separately from
   courses whose full AND/OR/grade/corequisite conditions are newly satisfied.
   “Newly open” needs a before/after deterministic check of the supplied history,
   within actual campus/source/term coverage; it is not registration clearance.
4. Build requested lists from exact program sections, preserving core/elective/
   choose-N boundaries, exclusions and editions. Distinguish alternatives from
   extra requirements. Target ENVS's four tracks, the NBB Biology elective tier,
   Econ+CS list truncation, Math BA/BS padding, Physics alternatives and the
   “only one of BIOL 205/CHEM 301” rule. Real but irrelevant codes are still errors.

Acceptance: RS_P12, S/U deadline and 4+1 six-hour cases either complete required
reads and answer correctly or state the exact remaining gap. Full lists contain
only requested requirements. Reverse answers cover the supported downstream set
without converting one satisfied operand into eligibility. Deadline work does
not start after exhaustion, and late results cannot publish. Extend existing
research-data, operation-budget, prerequisite and planner/evaluator tests.

## H. Make conversation and artifact capacity recoverable

Touchpoints: conversation store/queries/routes, `backend/app/api/schemas.py`,
`frontend/src/api/{types,http,mock}.ts`, chat sidebar and artifact lifecycle.

1. Add stable owner-scoped cursor pagination instead of the latest-200-only
   conversation list. Define deterministic tie ordering and return a continuation
   cursor. Update all callers together; do not silently change an array contract
   to a page envelope without updating mock/HTTP clients and architecture.
2. Prefer reversible archive/restore for conversations, with the 500 limit
   explicitly applying to active conversations. Archive must release capacity;
   restore must recheck it atomically. Show archived items through pagination.
   Define a total retention bound/cleanup policy as well so archive does not
   create unlimited storage. Keep permanent deletion in the existing privacy
   workflow unless reference-safe owner deletion is separately implemented.
3. Reject archive/cleanup during active work or use A's explicit fenced abandon
   before transition. Preserve histories, exports and artifact dependencies;
   keep cross-user operations indistinguishable from missing resources.
4. Give cap errors a stable code and actionable UI text. A permanent storage
   limit must not suggest that waiting for Retry-After will fix it. Retain
   race-safe creation/restoration at the cap.
5. Inventory analogous 500-artifact/500-revision caps. Reuse existing lifecycle
   operations if they actually free capacity; otherwise provide reference-aware
   retirement/cleanup. Do not delete revisions used by citations or dependent
   artifacts merely to make room. Document retention exceptions explicitly.

Acceptance: a student at the cap can find the oldest conversation, archive one,
create another and inspect the archive. Restoration/concurrent creation cannot
exceed the limit. Reload, keyboard focus, mobile sidebar and error recovery work;
archiving another owner's ID fails. Exercise the actual HTTP/browser flow.

## I. Remaining findings and explicit retest queue

These stay tracked even when they do not justify delaying the High fixes.

| Finding | Planned disposition / completion check |
| --- | --- |
| EMO-011, 026 | Retest career guidance and wording with grounded cases, including K28. Preserve conditional advice; do not infer employment guarantees. Change shared instructions only for reproduced behavior. |
| EMO-012 | Filter catalog subjects before fetching details where the public sitemap/cache supplies identity. Preserve complete snapshots, resume behavior and WAF stop rules; never use hidden/authenticated APIs or bypass CAPTCHA. |
| EMO-020, 021 | Inspect Compose dev-auth exposure and retest with Docker. Keep development behavior explicit; add/use a non-root runtime without reload for deployment, with writable data/cache ownership and unchanged local development workflow. Production readiness requires actual container checks. |
| EMO-023 | Return structured quota reset instants and render local time, with timezone explicit and an absolute time fallback. Test daily resets and minute limits; retain correct Retry-After. |
| EMO-024 | Fix typing in changed seams first, including connection/principal annotations. Establish a pinned type-check command and explicit staged scope in CI; reduce the 457-error backlog by package. No blanket ignores or claim of a green whole-project type check. |
| EMO-025 | Retest planning trace presentation in Admin/Debug; correct stale labels and trace cosmetics only where reproduced. |
| EMO-032 | Centralize browser executable discovery shared by both Node suites. Honor the environment override, installed Playwright Chromium and platform paths; run on Windows/Linux without a Windows-only default. Remove integration `continue-on-error` once the offline job is reliable. |
| EMO-052 | Retest post-privacy-deletion identity behavior. Document whether permanent 410 is intentional; if reuse is supported, explicitly fence old data/grants/jobs and prove the new session cannot recover them. Do not silently undo deletion tombstones. |
| Test-DB guard residue | Resolve the effective app DSN with the same config/libpq rules before destructive test setup, including `.env`. Preserve `_test` enforcement and fail before connecting on ambiguity; test with sentinels and password auth without PGPASSWORD. |
| Hold release wording | Make repeated release an explicit already-released/no-op outcome. Keep capture preservation and audit history. |
| Windows filenames | Reject CONIN$/CONOUT$ and equivalent device-name variants through the existing cross-platform filename validator; preserve normal filenames. |
| Import preview bounds | Bound academic-record preview rows/work and return actionable validation errors; retain ordinary legitimate imports. |
| Active-job quota semantics | Document that `activeJobsLimit` currently covers artifact runs; distinguish it from conversation admission and new upload-indexing limits. If broadening enforcement, make it race-safe by kind and update effective-quota contracts/UI. |
| Quota charged before execution (Q2) | Define admission/attempt charging and rollback when no provider work starts. Preserve actual/unknown started usage and replay idempotency; extend existing quota behavior tests. |
| Public OpenAPI/docs | Review intentional exposure under OIDC. Document it if public; if restricted, apply the existing server auth/capability boundary and verify developer tooling still works. This is an Info decision, not evidence of an auth bypass. |
| Code split across newline | Constrain query-side subject/number matching to intentional code boundaries while preserving explicit PDF-spacing normalization for source comparison; unrelated `MATH\n101` prose must not become a course lookup. |
| GUI coverage not performed in R4 | Verify Admin source review, rule drafts, grants and connection profiles; keyboard/mobile/accessibility of touched screens. Record untested surfaces explicitly rather than inheriting API-only “pass” status. |

EMO-014 and 028 are covered in F; EMO-022 in B/E; EMO-010/046/053 in C;
EMO-041 is superseded by 056. Fixed R4 issues (including 001–006, 015, 017–018,
027, 029, 031, 033, 036–037, 039–040, 042, 045 and 048–051) remain regression
checks, not new implementation projects. Use the report's individual status rows
when a range includes a historical grouping.

## J. Verification, rollout and closure

For every package, extend the existing behavior test before broad checks. Pure
recognition/ranking/parsing logic uses unit tests; transaction, publication and
ownership behavior uses real temporary Postgres. Reuse `tests/fakes.py`, helpers
and FakeClock. Tests using fresh connections also request `db`. No new tests for
manual/smoke scripts, no network/model calls in pytest, and no assertions about
SQL strings, constants or mock plumbing.

Run the relevant tests per change, then the repository checks once the integrated
changes are ready. From `backend/`, use the Python 3.12 venv:

```powershell
../.venv/Scripts/python -m ruff check .
../.venv/Scripts/python -m pytest
../.venv/Scripts/python -m scripts.schema_baseline
```

Use a fresh unique `--basetemp ../data/test-runs/pytest-<run-id>` if Windows temp
permissions require it. A skipped DB test is a failed verification. New migrations
must upgrade existing installations and work after the immutable baseline; never
edit the baseline or an applied migration. Run contract drift when schemas change.
From `frontend/`:

```powershell
npm run typecheck
npm run build
npm run format:check
npm test
npm run test:integration
```

Browser checks prove rendering, focus, isolation, reconnect and drafts surviving
errors; unit tests cover pure list/formatting/client behavior. The integration job
must be required before calling its failures release-blocking in CI.

Improve the existing manual smoke runner with explicit expected facts/codes,
forbidden claims and refusal/limitation expectations, nonzero failure exit status,
and paraphrase groups. Cover live retrieval as well as injected context. Preserve
call/token budgets and greedy generation; verify R4's “temperature > 0” assertion
against actual provider settings rather than introducing sampling. Three
meaning-preserving variants are more informative than identical repeated calls.
Model-dependent smoke runs remain manual, never CI unit tests.

| Release gate | Required evidence |
| --- | --- |
| No operational wedges | Composed-worker faults, cancellation, DB restart, crash reconciliation, replay, late-result fencing and backup/restore pass end to end. |
| No guard tradeoff hidden by pass rate | All targeted legitimate identity/report/refutation cases publish useful grounded answers; invented positive claims and unsupported substitutions remain rejected. |
| Policy correctness | Direct/indirect S/U, repeat, minor eligibility, credit/GPA corrections and mixed-program cases agree with the exact applicable evidence. |
| Source and graph integrity | Holds are visible, verified repairs retain history, Oxford/suffixes remain distinct and deterministic prerequisite checks pass. |
| Completion and precision | Required research pages/lists are complete or specifically partial; all five published R4 errors plus both guard-off errors are explicitly retested. |
| Capacity and GUI | Pagination/cleanup, failed-stream recovery, upload failure/progress, hold messaging, cap errors and student trace redaction pass through the real UI/API. |
| Regression quality | Regrade available R4 sets using the same corpus/configuration, frozen golds and hand-reviewed FP adjudication. Report pass/partial/fail, false claims, guard withholds, overhedging, paraphrase flips and list recall/precision separately; no aggregate uplift can excuse a new targeted false claim. |

Manual quality checks use the configured model; do not swap providers, disable
the guard globally or change authoritative data merely to improve scores. If R4
raw artifacts are unavailable, complete deterministic and reconstructed cases and
leave the full comparison explicitly pending. Do not claim the entire round fixed
from HTTP 200s or a smoke runner's “accepted” label.

Publish source/index changes only after their package gates pass. Preview affected
source counts, bindings, parser statuses and retirement/hold effects; retain the
last-good publication for rollback. Schema, worker and API changes need a
compatible rollout order, particularly asynchronous uploads and pagination.
Stopping embedded Postgres must be clean; never kill the server process tree as
an outage test against the development database.

Close each EMO in `docs/audit/README.md` only with the behavior changed, relevant
check evidence and any remaining data/deployment limits. Update older plan status
links after implementation so they do not retain contradictory completion claims.
Print routine diagnostics in the terminal; do not add saved test-output/status
reports unless requested. This is a planning handoff; implementation, data
publication, deployment and model changes have not been performed.
