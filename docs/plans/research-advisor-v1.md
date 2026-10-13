# Research advisor V1: end-to-end implementation handoff

Status: proposed implementation, requested 2026-10-12. No feature described as
new in this document is implemented by writing this plan.

`docs/architecture.md` remains the single design contract. This is the requested
handoff: implement its decisions in bounded milestones and update the relevant
architecture sections in the same changes. Do not create a second permanent
architecture document. Follow `AGENTS.md`, including its provider seams, named
SQL, migration, deterministic graph, and testing rules.

## 1. Product outcome

Build an advisor that can investigate an academic decision, form a defensible
opinion, and explain its tradeoffs. Its scope includes majors, minors, electives,
course preparation, institutional policies, and career interests. Courses are one
kind of evidence, not the unit of every advising task.

Primary acceptance scenario:

> I want to work in X. Which major should I choose? What would I miss in each
> option, and could electives or a minor fill the gaps?

The advisor should discover plausible paths, inspect what they actually contain,
compare them against the student's priorities, investigate uncertainties, and
recommend a small number of options. It may ask a useful clarifying question or
give a conditional recommendation when preferences are missing. It must also
handle simple questions without turning every lookup into a long investigation.

The important distinction is between **sourced facts** and **advising judgment**.
The catalog establishes requirements and course content. Career references can
describe occupational tasks. Neither automatically establishes the best choice
for a particular student. The model may make that choice as a reasoned,
conditional recommendation; it may not invent the underlying facts.

V1 uses one answering model that can alternate between research and assessment.
Do not introduce a planner/researcher/judge swarm, an autonomous background
research service, or a second mandatory model review for every answer.

## 2. Evidence motivating the replacement

Manual checks in this chat used the existing catalog and configured
LongCat-2.5-Preview connection. These are exploratory observations, not a benchmark
or professor-approved ground truth:

- Six prompts used current settings: advising tools enabled, query planning off.
  Two prompts were repeated with planning enabled only in the diagnostic process.
- A data-engineering career question missed CS 377, Database Systems. A paraphrase
  about databases and pipelines found it. Two nearly identical AI-minor passages
  occupied two of the six context slots.
- For a beginner reporting no college math or CS, the answer suggested MATH 210
  from its title in another program's list. Its actual catalog record requires
  prior calculus, but that record was absent from the context.
- Planning recovered useful course records, including CS 377. On the beginner
  question it still recommended CS 110 while quoting its warning that it is not
  intended for students needing preparation for further CS work. CS 170's actual
  description was available in that same context.
- A natural eligibility question got the main CS 350 prerequisite warning right
  without using a deterministic advising tool. Required-tool detection currently
  recognizes a narrow set of sequence-related phrases.
- The planning-on pair accounted for roughly 148k provider tokens, versus about
  6.6k for the corresponding two baseline prompts. This includes different
  contexts and answers, so it is not an isolated planner-cost benchmark.

Consequences: expanding retrieval alone cannot fix judgment, a valid citation is
not proof of a suitable recommendation, and the complete course directory is too
expensive to make the mandatory prefix of every investigation.

## 3. Firm V1 scope

### Included

- Progressive research across the authorized current institutional corpus.
- Program comparison, course discovery, policy lookup, and conditional advice.
- Curated external career documents ingested ahead of time, with explicit
  provenance. Research works without them and discloses that limitation when it
  matters to the recommendation.
- A bounded working comparison, an immutable evidence register, and a single
  shared operation budget per user turn.
- Deterministic prerequisite checks and exact course-mention overlap analysis.
- Useful clarifications and clearly identified judgment, uncertainty, and
  user-reported preferences.
- Both `/api/query` and persisted conversational turns, using the same service.
- Visible research progress, clickable citations, and a compact inspection view.
- A feature flag, offline behavior tests, manual quality evaluation, and rollback.

### Deferred

- Live arbitrary web browsing, external search-provider integration, and automatic
  crawling during a student's turn. V1 external evidence comes from imported
  snapshots; do not pretend a URL was visited by the model.
- A full degree audit, optimal-credit solver, verified semester timetable,
  transfer-credit adjudication, or enrollment approval.
- A model-written prerequisite/program graph, generated topic nudges, or a
  permanent student-memory/mastery subsystem.
- New artifact editors, automatic document generation, and a redesigned graph UI.
- Resuming an interrupted model conversation across worker crashes. V1 preserves
  audit records, fails interrupted execution safely, and permits an explicit new
  user retry rather than silently re-running billable research.

These boundaries do not prohibit useful planning. The advisor may suggest a
major-plus-minor path or electives, but must distinguish a plausible path from
verified degree-counting or enrollment rules.

## 4. What stays and what is replaced

| Existing component | V1 decision |
| --- | --- |
| Ingest, locators, source origins/current publication, embeddings | Keep; add controlled provenance metadata and program-to-source binding. |
| Keyword/embedding retrieval and fusion | Keep as bounded discovery primitives. Their top-k results are not the final answer's evidence universe. |
| Deterministic atlas parser and sequence planner | Keep as the sole source of prerequisite structure and dependency computation. |
| Parsed program lines/course mentions/choose counts | Keep their limited meaning. They are not a complete degree constraint model. |
| Learned department/topic groups | Keep for exploration. They neither establish eligibility nor rank careers. |
| Full directory plus one-shot `query_plan.py` | Bypass on the research path; retain only for the legacy feature-flag path during rollout. |
| `answer_tools.py` tools restricted to initial chunks | Replace on the research path with scoped discovery and evidence-reading tools. Preserve artifact tool behavior. |
| Cited-bullets-only answer policy | Replace on the research path with typed factual/judgment/conversational blocks. |
| Provider factories, routing, budget controller, tool ledger | Reuse and extend; never instantiate provider SDKs in research code. |
| Conversation jobs, leases, cancellation, source/profile fences | Reuse. Research is a conversation-turn implementation, not a new job system. |
| Claims and explicit claim review | Preserve; extend interpretation for typed blocks. Structural validation remains distinct from semantic review. |

Feature precedence is explicit: `ADVISING_RESEARCH_ENABLED=true` selects the new
path and ignores `QUERY_PLANNING_ENABLED` for that request. Research requires a
tools-capable permitted provider and `TOOLS_ENABLED=true`. An unavailable research
provider returns a clear feature-unavailable error; it must not silently fall
back to a materially different advising workflow. Flag-off retains today's path.

## 5. End-to-end execution

```text
HTTP query or leased conversation turn
  -> authenticate; resolve collection and effective source selection
  -> pin source manifest; select permitted provider; create one operation
  -> create research run/root trace and empty evidence register
  -> give model the question, bounded conversation context, tools and budget
  -> model alternates among:
       discover candidates / read evidence / deterministic checks
       update concise research state / revise candidate comparison
       finish with recommendation, clarification, or insufficient evidence
  -> validate final structure, references and required computations
  -> render readable answer and numbered citations deterministically
  -> fence source/profile/operation/turn state; publish answer + audit atomically
  -> existing conversation event delivery and UI
```

No initial six-chunk retrieval is required. The first call can ask a clarification
or request evidence. A quick factual question may need one lookup and a finish;
a complex comparison can use several rounds. The first plan is revisable.

At the start provide only a small scoped inventory: source categories available,
department/program counts, selected-source restrictions, and research tools.
Do not send every course title, every program requirement, or a transcript of
historical research. Inventory counts describe the selected corpus, not a claim
that all currently offered Emory programs have been acquired.

### Model instructions

The research prompt must explicitly teach the model to:

1. Identify the decision and the preferences that could change it. Treat career
   skills suggested from general knowledge as search hypotheses until researched.
2. Discover plausible alternatives across departments. Do not assume career names
   equal degree names or always force a particular CS/math/data-science trio.
3. Read primary program requirements and course descriptions for important claims.
4. Check the strongest objection to its provisional recommendation: exclusions,
   prerequisites, missing coverage, combination prohibitions, or a better-fitting
   alternative. This is a research behavior, not a mandatory extra model call.
5. Interpret negative evidence: a course's explicit warning matters even when its
   title and topic match. Recognize required courses versus optional possibilities.
6. Ask a targeted question only when the answer would materially change the next
   step; otherwise state an assumption and offer conditional options.
7. Finish when it can explain a defensible comparison and material uncertainties.
   It need not exhaust the catalog, fill every field, or spend the entire budget.

Never request, store, or display private chain-of-thought. Research state contains
task questions, evidence references, concise decision reasons, and uncertainties.
Provider-internal reasoning stays inside the provider's supported behavior; no
reasoning token scraping or unrestricted scratchpad transcript is required.

## 6. Source scope, evidence, and institutional facts

### 6.1 Scope is independent of current search results

At run start build a server-owned `ResearchScope` containing workspace,
collection, effective source selection, and a manifest of accessible indexed
current source IDs and index revisions. Reuse a conversation's stricter frozen
source snapshot when present. `sourceIds=[]` means no sources, not all sources.

The model cannot choose workspace, collection, allowed source IDs, URLs to fetch,
or SQL. Every tool uses the captured scope and named queries. Discovery can find
new entities within it; it cannot enlarge it. Source IDs or course codes supplied
by the model are always resolved again against that scope.

Pin program projections and alias provenance to source versions too. Before every
read verify relevant source revisions still match. Before publication recheck the
manifest and current/authorized state, profile revision, cancellation, and operation
deadline. On mismatch withhold the result and offer a fresh turn; do not combine
old and new course/program data or silently switch editions midway.

Keep database transactions short. Commit reads before provider or embedding I/O.
Do not nest same-pool borrows; retain the existing separate execution/control/tool
pool roles. Do not hold a catalog-wide database lock during research.

### 6.2 Evidence register

Discovery results are leads. Reading a source creates server-assigned evidence
IDs, such as `e1`, scoped to this run. Each `EvidenceRecord` contains:

```text
evidence_id; source_id; source_revision; origin_key; source_kind
source_name; source_url?; edition?; acquired_at?; published_at?
chunk_id; locator_id; locator_label; exact_text; text_digest
entity_refs[]; acquired_step; child_trace_id?; content_completeness
```

An evidence item is one actual locator-scoped chunk. A complete course or
requirement section can reference several items; never concatenate chunks and
invent a synthetic chunk ID. Source metadata has field-specific references where
needed. Acquisition time is not publication time or proof a rule is current.

IDs are stable within a run and never reassigned after context compaction.
Deduplicate identical `(source_id, revision, chunk_id, text_digest)` records.
For retrieval diversity also collapse identical normalized discovery text and
avoid repeatedly returning overlapping chunks from the same locator. Do not merge
merely similar passages or discard an exception because most text overlaps.

All evidence shown to the model is untrusted data. Tool-output envelopes are
server-authored; source text cannot add capabilities or change instructions.
Truncation must be explicit. Never chop a condition off a requirement and mark
the result complete. Page at whole item/chunk boundaries; an overlarge single
item reports that it could not fit and is incomplete.

### 6.3 Program evidence is a prerequisite for the new feature

Current `programs` rows have catalog/page metadata but no scoped source binding.
`program_items` records raw lines, course mentions, and explicit choose counts.
The importer replaces the collection's projection, and
`programs_requiring_course` returns mentions despite its name. These tables
cannot currently serve as authoritative, permission-filtered degree evidence.

Add a deterministic binding from each program projection to its exact indexed
catalog source revision and content hash. Extend scraper/import records to carry
the existing exact-SHA ingest manifest identity (`origin_key`, SHA, catalog ID).
Resolve that identity through current source metadata during import; never bind
by a fuzzy filename/title match or a model guess.

For each requirement item retain ordered supporting chunk IDs plus exact spans
where they can be deterministically aligned. Alignment uses the same normalization
as ingestion. If alignment fails, retain the raw item as a discovery lead and
return `evidence_unresolved`; the model can read the actual program section, but
the projection cannot masquerade as a cited requirement. Header fields need
supporting source chunks too. A program tool must never expose unscoped raw text
from a legacy unbound projection.

A minimal forward migration may add source/version/hash fields to `programs` and
a `program_item_evidence` join table. Update import/publication together: stage
the new projection, validate its binding, and publish the collection replacement
atomically. Include a digest of its parsed content in the research manifest so a
re-import against unchanged source text cannot alter a running comparison.
Legacy rows stay unbound until explicitly verified/re-imported; do not guess a
backfill. This applies to fresh installs and upgrades.

Expose these honest semantics:

- `mentioned_course_codes` means mentioned, including alternatives and exclusions.
- `choose_count` is an explicit count on a raw line, not a whole-degree solution.
- `requirements_complete` means all sections of this source have been read, not
  that the entire degree has been formalized or that the student can graduate.
- Topic coverage from a required course differs from coverage available only via
  an elective. The answer must say which it is and cite the raw requirement.
- Overlap in mentions does not imply both programs require a course, that credits
  double-count, or that a major/minor combination is permitted.
- Do not infer QTM/DATASCI renaming or equivalence from similar descriptions.
  Use verified identity mappings only. Missing/unresolved codes stay visible.

### 6.4 External career evidence

V1 supports manually curated public occupational references as ordinary indexed
documents, with controlled metadata: publisher, URL, occupation identifier if
provided, geography, edition/publication date if known, acquisition date,
content hash, source category, and reuse/attribution information supplied by the
curator. Do not add a career taxonomy or scraping service as a hidden prerequisite.

Add a source-provenance table keyed by source ID, or extend the existing metadata
mechanism if it already has these semantics. Ingestion must validate sidecars and
write metadata atomically with the indexed version. Trusted classifications are
assigned by configured importers/curators; a user's document cannot declare
itself an official Emory source. Ordinary uploads remain `user_supplied`.

Use categories `institutional_catalog`, `institutional_department`,
`institutional_policy`, `career_reference`, `user_supplied`, and `unclassified`.
An explicit verified import identifies category; a model or hostname heuristic
does not. Existing sources without verified category remain usable with their
provenance uncertainty exposed; do not blanket-promote the corpus.

Extend the existing exact-SHA `--origin-manifest` import schema with optional
validated provenance fields, keeping old manifests valid. Keep acquisition and
publisher metadata in sidecars, outside canonical document identity. The normal
upload endpoint cannot set trusted source categories. Document the import command
and expected sidecar fields in the existing corpus runbook; use a fixture to prove
that a career reference is discoverable, citable and source-scoped end to end.

Career sources support occupational tasks/skills within their scope and date.
They cannot establish Emory requirements, placement rates, salary guarantees, or
which major is best. Missing career evidence does not bar conditional academic
comparison: label the career-to-skill mapping as an assumption and explain its
limits. Career pay/outlook statistics require source dates and geography.

No live URL fetch tool in V1. A source link is attribution, not a network action.
External documents respect the same collection and source selection as other
evidence. Do not silently add them when the user selected only institutional files.

## 7. Research tools

Implement typed `ToolSpec` tools backed by server-owned scope. Reuse the registry
and execution ledger. Introduce a `CORPUS_RESEARCH` capability for collection-level
discovery rather than weakening `SOURCE_READ` resource checks globally. Read tools
retain resource validation and also verify the DB scope/revision on every call.

The handler owns a run-local issued-handle map. Discovery issues opaque handles
bound to entity/source versions. Course reads may accept exact codes and resolve
them in scope; arbitrary IDs never bypass authorization. Handle issuance does not
grant access outside the pinned manifest. Pagination cursors bind to tool, query,
scope fingerprint, and ordering and cannot be reused across runs.

Keep the ledger scope fingerprint stable for the run: hash the pinned source and
program manifests, workspace, collection, provider revision, and tool/prompt
versions. Do not include the changing active-evidence set. Build each invocation's
`ToolScope.resource_ids` from the currently issued read handles under that fixed
authority. The generic controller's initial frozen chunk scope cannot simply be
reused for progressive discovery. State-tool fingerprints additionally include
their expected revision through validated arguments.

All results contain `status` (`ok|not_found|incomplete|conflict`), bounded items,
`next_cursor` if present, and completeness/limitations. An unauthorized identifier
returns the same safe not-found behavior as a nonexistent one.

| Tool | Inputs and bounded output | Meaning |
| --- | --- | --- |
| `advisor_search` | `query` (1–300 chars), `kind` (`program|course|policy|career_reference|any`), optional department, cursor. Up to 12 entity/document leads with exact titles, bounded snippets, source kind, opaque handles and discovery provenance. | Hybrid keyword/embedding search grouped by entity/locator, not six final chunks. Search program requirements as well as titles so a career phrase need not match a degree name. Results are not citable until read. |
| `advisor_list_programs` | Optional kind/department, cursor; up to 30 exact program identities/titles and scoped source status. | Deterministic browse path when search vocabulary fails; no whole-course directory prompt. |
| `advisor_read_program` | Program handle, optional section handle/cursor. Overview plus section index, then up to 8 complete requirement items per page. | Returns raw requirements, parser observations, metadata, evidence IDs, unresolved links, and what remains unread. Does not assert degree satisfiability. |
| `advisor_read_courses` | Up to 4 exact codes or issued course handles per call. | Complete description, raw requisites, explicit restrictions and parsed requisite status with evidence IDs; unknown requirements stay unknown. |
| `advisor_read_document` | Document/section handle and cursor. Up to 8k source-text bytes per page at complete chunk boundaries. | Policies, department pages, career references, and permitted uploads. An overview gives a section index so the agent can navigate instead of guessing locators. |
| `advisor_compare_mentions` | Two to four already-read program handles. | Exact shared/unique mentioned codes with raw item/evidence references and exclusions/alternatives left visible. Return `mention_overlap_only`; count shared mentions, never credits saved. |
| `advisor_check_preparation` | Selected exact course codes and student-asserted completed codes, each bounded to 30. | Reads scoped current requisite records and invokes the existing deterministic planner; returns stages, alternatives, blockers, assumptions and evidence. It may acquire prerequisite evidence within scope under the same limits, but never infer missing edges. |
| `advisor_update_research` | Full replacement of bounded `ResearchState` with expected state revision. | Keeps the working comparison, open questions and active evidence set. Validation prevents fabricated evidence IDs or silent promotion of hypotheses. |
| `advisor_finish` | Typed final decision and blocks defined below. Sole terminal call in its provider response. | Proposes the final answer; it does not publish it. Backend validation and publication fences still run. |

Use `advisor_search` for discovery across every supported entity kind; do not
implement an NLP intent regex dispatch to separate course/major/career pipelines.
The model chooses tools from the question and conversation. Exact lookups are cheap
tools in the same workflow. Existing graph-exploration tools may be reused when
scoped correctly, but a new graph algorithm is not part of this V1.

Search ranking is a hypothesis. It may combine current keyword/embedding ranks,
deduplicate canonical course identities, and cap repeated source/locator entries.
Do not represent an arbitrary rank as semantic confidence. Preserve actual seam
scores in child traces. A search that returns no leads can be reformulated or
followed by browsing; it is not automatically a final refusal.

Deterministic preparation checks must collect the source evidence needed to
support their result. Bound expansion by visited nodes and cycles; return
`incomplete` with unexplored dependencies if the bound is reached. “No parsed
prerequisites” is not “eligible.” Preserve existing conservative handling of
conditions, missing raw fields, and parse failures.

## 8. Working state and context management

Define strict internal Pydantic models in a new `app/advising/research_types.py`.
Avoid a generic untyped agent-memory dictionary. Suggested shape:

```text
ResearchState
  revision: integer (server assigned)
  decision_question: string
  student_context: [{statement, basis: user_report|assumption, turn_id?}]
  criteria: [{id, description, basis: user_report|career_evidence|assumption,
              evidence_ids[]}]
  candidates: [{id, program_handles[], course_codes[], description,
                status: investigating|viable|conditional|not_preferred,
                strengths[], gaps[], objections[], evidence_ids[]}]
  open_questions: [{question, importance: material|optional,
                    next_action: research|ask_student|disclose}]
  active_evidence_ids: [server-issued evidence IDs]
```

Use at most 6 candidates, 8 criteria, 8 open questions and 8k serialized bytes for
state. Candidate combinations are hypotheses, not persisted degree pathways.
Reasons are short summaries; do not ask for verbose internal reasoning. Student
assertions cannot be converted into institutionally verified facts by updating
state. Preserve the originating user turn when available. User corrections in
later turns supersede earlier assertions.

The durable evidence register can exceed the model's active context. After a
completed tool round, rebuild the next model request from fixed instructions,
the original question, bounded relevant history, validated state, active exact
evidence, and a compact list of completed actions/remaining limits. Do not append
the full ever-growing transcript indefinitely. Preserve valid assistant/tool
message pairing if native tool messages are retained; never leave orphan tool
results. The source of truth is application state, not the provider's memory.

Only backend-selected exact evidence text enters the evidence block. State
summaries are labeled model assessments and never promoted into citable passages.
Preserve raw restrictions/conditions alongside supportive text. Deterministic
compaction may remove discovery snippets and old repeated results first. It may
not shorten a requirement into a stronger claim. If requested evidence cannot
fit, report which items were left out and let the agent reduce its active set.

V1 limits the register to 100 evidence items and 256k exact-text bytes. Large
program comparisons page deliberately and can finish as partial. These are
starting limits, not evidence that 100 passages is sufficient for every question.

## 9. Final decision and judgment contract

`advisor_finish` accepts the following internal structure (snake_case internally;
map to the API's camelCase conventions at serialization):

```text
FinalDecision
  outcome: recommendation|information|clarification|insufficient_evidence
  completeness: complete|partial
  candidate_ids: existing research candidate IDs, possibly empty
  blocks: [{kind: fact|judgment|assumption|limitation|question,
            text: bounded Markdown paragraph or bullet,
            evidence_ids: [], candidate_ids: []}]
  preparation_check_ids: executed check IDs, possibly empty
  preparation_claims: [{check_id, selected_codes[], completed_codes[],
                        conclusion: blocked|dependency_order|unresolved}]
  unresolved_questions: [short strings]
```

Limit to 20 blocks and 16k text bytes. Do not mandate a comparison table or a
particular number of recommendations. One clear recommendation with an
alternative can be better than a list of six indistinguishable options.

### Facts and opinions

- A `fact` block needs supporting exact evidence IDs for its institutional or
  external claims. Titles, program inclusion, exclusions, requirements, credits,
  and occupational statistics are factual claims.
- A `judgment` block may express a preference or recommendation. It identifies
  the candidate and cites the facts supporting its rationale. Use language such
  as “I would lean toward…” or “This looks better suited to…”. Do not state a
  numerical fit score, employment probability or universal best major without
  a defensible separately specified methodology; V1 has none.
- An `assumption` block may rest on the student's report or an explicitly stated
  working interpretation. It needs no decorative catalog citation.
- A `limitation` can describe the scope of the investigation without citing an
  unrelated passage. A factual prohibition embedded in it still needs evidence.
- A `question` can stand alone without evidence. Ask at most two concise questions
  in one turn; prefer one decision-changing question.
- Text cannot escape factual validation simply by labeling itself judgment or
  limitation. Prompts, semantic evaluation, and explicit review must check this;
  a deterministic schema validator cannot prove entailment.

Before publication validate all references against evidence actually supplied
to a provider call in this run. A stale history citation or discovery snippet
cannot become a valid evidence reference. At finish, ensure the selected exact
evidence and current state were in the final request. If the model cites a
previously read but inactive passage, return a recoverable error and require
reactivating it before a new finish call; do not silently attach unseen evidence.

Recommended program candidates need scoped program evidence; course-specific
recommendations need the actual course record or an explicit missing-record
limitation. Claims of readiness or ordering need an executed deterministic check
covering those codes and the same asserted completed-course set. Merely naming
a future course does not claim eligibility and need not run a sequence check.
Use the typed `preparation_claims` list in `FinalDecision` for every asserted
readiness/order conclusion, linking conclusion to check ID and covered codes;
validate it against the executed result, including blockers. This is an audit
contract, not a claim that prose classification can catch every model omission.

The model must not convert unknown double-counting rules into a claimed credit
saving. It can recommend exploring a combination, explain its topical coverage,
and identify the policy that must still be checked. Present contradictions
between catalog and department sources explicitly; catalog-of-record rules take
precedence for degree requirements when editions/applicability match. Unknown
student catalog year is a condition on the advice, not an excuse to choose an
arbitrary edition silently.

### Rendering and review

The backend assigns display citation numbers in first-use order and renders
Markdown plus citation markers. Model block text must not contain raw numbered
citations; reject those rather than accepting two reference mechanisms. Build
`trace.contextBlock` in that exact order. Store evidence IDs, rendered markers,
source snapshots, typed blocks, and prompt version together.

For a final proposal with malformed references or missing required checks, allow
one bounded correction within the same budget. Never invent a supporting citation
or silently replace a recommendation. If correction fails, return a controlled
failure and keep the rejected proposal only in private audit data.

Replace the blanket “every paragraph must have a citation” validator only on the
new path. Preserve citation syntax checks for rendered factual references. Update
explicit claim-review handling to review factual assertions and supporting facts
of judgments, while keeping subjective suitability distinct from entailment.
Do not mark any block “verified” because it passed JSON or citation validation.

Clarification and honest insufficient-evidence responses may contain zero
citations and may be produced without institutional evidence. This intentionally
changes the architecture's old “never call the model without chunks” rule for
research/clarification, while retaining the prohibition on unsupported factual
answers. Update that rule explicitly in `architecture.md`.

## 10. Controller, budgets, and failures

Implement a research controller beside the generic tool controller. Reuse its
provider-call/accounting helpers and ledger semantics; extract common helpers
where necessary instead of copying them. Keep artifact terminal-tool behavior
unchanged. A research round is one model call and its sequential tool executions.
Tool requests within a round execute in returned order in V1, avoiding concurrent
state updates and ambiguous evidence numbering.

The controller, not the model, enforces:

- Call, tool, prompt, output, elapsed-time and evidence limits.
- Native tool schema validation and scope checks.
- Only `advisor_finish` ends a successful research turn. Free text outside a tool
  result is not a publishable answer; one correction request is bounded/accounted.
- A terminal tool must be the sole tool in its response. An invalid terminal
  proposal is not a committed answer.
- Repeated identical reads/searches use the run cache where safe. Same tool call
  ID with changed arguments is rejected. Repeated no-progress rounds eventually
  force a finish opportunity rather than infinite paraphrased searching.
- At most one structural final correction. No hidden retries with new budgets.

### Starting configuration

Add validated environment settings in `app/config.py` and algorithm/context
limits in a versioned `[research]` section of `configs/retrieval.toml`. Unknown
keys remain errors. Explicit starting defaults:

| Setting | Default |
| --- | ---: |
| `ADVISING_RESEARCH_ENABLED` | false during rollout |
| `RESEARCH_MAX_CALLS` | 12, including initial, finish, and correction calls |
| `RESEARCH_MAX_TOOL_CALLS` | 24, including state updates; successful cached replays do not consume an execution slot |
| `RESEARCH_OPERATION_TIMEOUT_SECONDS` | 240 |
| `RESEARCH_OPERATION_TOKEN_BUDGET` | 640000 reservation/accounting units |
| `RESEARCH_MAX_PROMPT_BYTES` | 48000, including messages and tool schemas |
| `RESEARCH_MAX_OUTPUT_TOKENS` | 4096, bounded by the selected connection |
| `[research].max_candidates` | 6 |
| `[research].max_evidence_items` | 100 |
| `[research].max_evidence_bytes` | 256000 |
| `[research].max_state_bytes` | 8000 |
| `[research].no_progress_rounds` | 2 |

Use one `GenerationOperation` initialized with research-specific limits; these
replace the legacy answer limits for that operation, not stack on top of them.
Never create an operation for each search, comparison or correction. Existing
accounting uses serialized-byte prompt estimates until provider usage arrives;
the budget above is not a promised token bill or money ceiling. Record measured,
estimated, cached and unknown usage separately. Do not change greedy generation
defaults or enable provider-specific reasoning without explicit configuration.

Leave room for finalization: before a nonterminal provider call require at least
two call slots and a conservative final-request/output allowance afterward. Keep
an optional correction slot while possible. Budget calculations must use the
same serialization/accounting helper as actual reservations; a separate rough
word-count estimate is insufficient. Stop research early when there is not enough
time for a final call, using that call to report partial results. Finalization
stays inside the original deadline and token budget. If the deadline has already
expired, publish no newly generated answer; return a controlled timeout.

When research is exhausted, provide only the finish tool and a structured list
of unresolved questions. Failure to finish is a controlled failure. A partial
answer can still be useful, but must not claim the investigation was complete.

### Failure semantics

| Event | Required behavior |
| --- | --- |
| Empty search | Permit reformulation, browsing, clarification, or scoped insufficient-evidence result. |
| Unknown/out-of-scope entity | Safe structured not-found result, no scope expansion. |
| Invalid tool arguments | Replayable correction result; consumes bounded attempt/call resources. |
| Incomplete program/parser data | Preserve raw evidence and uncertainty; no invented degree logic. |
| Contradictory sources | Expose conflict and applicability/edition; do not silently merge. |
| Repeated searches add no evidence or decisions | After configured limit, offer finish with unresolved questions. |
| Provider timeout/unknown completion | Account conservatively; no automatic new operation or hidden replay. |
| Cancellation, source/profile change, stale turn | Withhold publication and preserve the existing state fence. |
| Tool result exceeds limit | Page or return incomplete; never silently clip a restriction. |
| Crash after provider submission | Retain unknown attempt accounting; mark run interrupted and require explicit retry. |

## 11. Persistence and audit

Use the next available forward migration numbers (037 onward at the time of
writing). Inspect the repository at implementation time; do not assume 037 is
still free. Never edit the baseline or applied/legacy migrations.

Reuse `operations`, `model_calls`, `tool_executions`, `responses`, and citation
snapshots. Add these bounded research-specific records:

```text
research_runs
  run_id/operation_id; root_trace_id; workspace_key; collection_id
  conversation_request_id? (unique when present)
  scope_manifest JSONB; scope_digest; prompt/config versions
  state_revision; state JSONB; status; stop_reason; created_at; updated_at

research_steps
  run_id; ordinal; tool_execution_id?; child_trace_id?
  action_kind; safe short summary; issued_evidence_ids[]; created_at

research_evidence
  run_id; evidence_id; immutable evidence snapshot JSONB; digest

research_decisions
  run_id; response_id; validated final structure JSONB; render_version
```

Use foreign keys to stable run/response identities. Evidence snapshots must
survive reindex/removal of mutable chunks; do not require a live chunk FK for old
audit data. Research state is private to the workspace and turn, not global
student memory. Bound stored JSON by the same model/schema limits as tools.

Integrate existing trace semantics explicitly:

- Add a trace kind (`retrieval` default for old rows, `research` for new root rows).
  Each actual `retrieve()` invocation still writes exactly one child trace.
- The root trace shares the operation identity and receives model-call links and
  the final response. `research_steps` links child retrieval traces to the root.
- Exact record reads get acquisition records, not invented similarity scores.
- Root seam/contribution arrays may be empty; they do not pretend to describe a
  single retrieval pass. The new research trace extension exposes child steps.
- Final `context_snapshot` is the numbered exact context used by the rendered
  response. The full gathered register is stored separately in `research_evidence`.
  The final provider input must contain all final-cited text; audit also retains
  which uncited active evidence influenced that call.

Refactor evidence persistence to accept a validated response context independent
of `Retrieved`; do not manufacture fake `CitedChunk` ranks to satisfy the old
serializer. The one-source/locator/trace chain still applies to every citation.

Ledger completion and evidence-register/state updates must be atomic or have an
idempotent replay path keyed by `(run_id, tool_execution_id)`. A cached tool result
must restore the same evidence IDs and state revision effects. Saving a ledger
result before saving its issued evidence without recovery is a correctness bug.

Check operation/turn/profile/source fences in the final transaction that stores
response, citations and research decision. Conversation publication still performs
its own existing lease/request fence. A stored internal response must not cause a
cancelled or stale conversation turn to appear in chat.

Look up `research_runs` by the saved conversation request before creating an
operation. A completed run can reuse its stored final result after the usual
publication fences. A run with an unknown submitted provider attempt is marked
interrupted without a second submission. A duplicate active request must not
start a second run; use the existing lease plus the unique request binding.
Synchronous `/api/query` creates a fresh identity per incoming request in V1 and
does not promise replay after a client disconnect. Document that distinction;
never implement retry by recursively calling `query_response` with a new budget.

Do not persist credentials, raw SDK exception bodies, or provider private reasoning.
Research progress summaries describe actions, not hidden deliberation. Existing
workspace deletion/retention and backup/restore must cover the new tables.

## 12. API, conversation, and UI contract

Keep current query and conversation endpoints. Backend and frontend types change
together, with explicit optional fields for old responses:

```text
AnswerResponse / QueryResponse's answer variant:
  advising?: AdvisingResult

AdvisingResult:
  runId: string
  outcome: recommendation|information|clarification|insufficient_evidence
  completeness: complete|partial
  blocks: AdvisingBlock[]
  unresolvedQuestions: string[]

AdvisingBlock:
  kind: fact|judgment|assumption|limitation|question
  text: string                         # rendered text, with current markers
  citationChunkIds: string[]

RetrievalTrace:
  research?: ResearchTraceSummary

ResearchTraceSummary:
  runId: string
  status: completed|partial|clarification|insufficient_evidence
  stopReason: string
  steps: ResearchStepSummary[]

ResearchStepSummary:
  ordinal: integer
  action: string
  summary: string
  traceId?: string
```

`answer` remains the rendered Markdown string and `citations` remains the existing
list, so existing chat/source rendering has a compatibility path. A research
clarification or insufficient-evidence message uses `type=answer` with the
explicit advising outcome; old retrieval refusals retain their existing shape.
Update any UI/eval logic that assumes `type=answer` implies substantive advice.

Add optional source metadata to `ContextItem` and `Citation`: `sourceKind`,
`sourceUrl`, `sourceEdition`, `sourcePublishedAt`, and `sourceAcquiredAt`, with
matching Python/TypeScript optionality. No fabricated dates. Include only safe
http(s) attribution links; source URLs are not executable navigation instructions.

Research `trace.prompt` contains the final research instructions and serialized
active evidence/question state, documented as such; it is not a claim that the
whole run consisted of one system/user pair. `modelCall` reports aggregate measured
usage and overall latency consistently; unknown constituent usage makes the
aggregate unknown/estimated rather than silently counting missing values as zero.
Detailed per-call accounting remains durable. Do not claim cached tokens were free.

Reuse existing progress stages: `planning`, `retrieving`, `using_tools`,
`generating`, `validating`, `validated`. Optionally extend stage events with a
server-authored `activity` string of at most 160 characters, such as “Comparing
program requirements”; keep it optional for older events. Update the worker's
stage filter and all TS unions if adding it. Never stream the research scratch
state or intermediate model tool text as provisional advice.

V1 can deliver the validated final answer in one event; token-streaming JSON from
the finish tool is unnecessary. Preserve cancellation and reconnect behavior.
The UI shows a small research status, a partial-results notice when needed, normal
citations, and a collapsible “Research steps” view. Do not build a second chat or
expose provider implementation details in the main student flow.

For follow-ups, retain the final options, relevant user assertions and unresolved
questions in the existing bounded conversation context. Previously cited evidence
is historical context until re-read under the next turn's scope. Corrections such
as “I meant policy work, not software engineering” must change the investigation;
do not blindly append old course codes to every new retrieval query. A new turn
gets a new operation; retries of the same pending request do not.

Update `claims`/explicit review adapters so uncited questions and assumptions are
not automatically treated as failed factual paragraphs. Retain unreviewed status
for generated factual/judgment blocks. Older saved answers remain readable and
retain their original prompt/version interpretation.

## 13. Implementation map

New files below are proposed responsibilities, not permission to duplicate
existing helpers. Keep pure models/comparisons separate from DB access.

| Area | Files / changes |
| --- | --- |
| Research types and comparisons | `app/advising/research_types.py`, `comparison.py`: strict state, decision and evidence models; pure mention comparison and structural validation. |
| Scoped reads | `app/advising/research_repository.py` plus named `db/queries/research.sql`: inventory, leads, exact records, source/version checks. Reuse existing atlas/program query helpers where valid. |
| Research persistence | `app/advising/research_store.py`: run/state/evidence/step transactions and idempotent acquisition. |
| Tools | `app/generation/research_tools.py`: typed tool definitions and bounded read-only handlers; terminal/state tools write only private research records. |
| Research loop | `app/generation/research.py`, `research_prompt.py`: context assembly, provider calls, tool rounds, stop/finalize behavior. Reuse extracted controller helpers. |
| Final rendering | `app/generation/research_answer.py`: typed-block validation, citation assignment, Markdown, prepared response context. |
| Program/source binding | `app/programs/importer.py`, scraper/import entrypoints, ingest provenance, forward migration and named queries. No LLM parsing. |
| Runtime integration | `app/api/query_service.py`, `runtime.py`, `config.py`, `retrieval/config.py`, `configs/retrieval.toml`; preserve one shared query entrypoint. |
| Evidence and contracts | `generation/evidence.py`, `claims.py`, explicit claim review, `api/serialize.py`, `api/schemas.py`, `frontend/src/api/types.ts`; add matched named TS interfaces. |
| Saved chat | `conversations/worker.py`, relevant store/context helpers, `frontend/src/components/chat/`, source/trace UI and API mocks/fixtures. |
| Manual evaluation | Extend `scripts/smoke/live_advising.py` to run judgment scenarios and emit terminal diagnostics; keep provider calls out of pytest. |

Do not put a new monolithic agent framework inside `query_service.py`. That file
selects the workflow and passes existing runtime dependencies and guards.

## 14. Quality evaluation

The professor's assessment is the desired product-quality judgment. Until such
review happens, label rubric expectations as draft engineering judgments. An LLM
grader can assist but cannot certify its own advice or become the sole gate.

Create a small, checked-in scenario set with user context, query/paraphrases,
expected research needs, acceptable answer properties, disqualifying mistakes,
and relevant fixture evidence. Do not store a single mandatory “best major” string.
Maintain a separate held-out set; do not tune every prompt against the same cases.

Include at least these families (multiple domains, not only computing):

1. Career-to-major choice with two plausible programs and different strengths.
2. Same career, different priorities: flexibility/domain interest versus technical
   depth, producing a reasoned change in advice or a justified unchanged choice.
3. Major plus minor/electives: distinguish topic coverage from permitted counting.
4. Shared course mentions containing exclusions and choose-one alternatives.
5. Beginner preparation: avoid MATH 210 as an immediate start without calculus;
   consider explicit warnings such as CS 110's intended audience.
6. Ambiguous career label: ask or compare interpretations instead of picking one
   silently. Student preference supplied in a follow-up must affect the answer.
7. Missing career reference: conditional academic comparison without invented job
   outcomes. A career source cannot override an institutional rule.
8. Conflicting catalog/department rules, unknown catalog year, unresolved course
   aliases, missing descriptions, and variable special-topic content.
9. Simple factual lookup and empty/selected-source scope.
10. Natural eligibility phrasing, unresolved prerequisite alternatives and missing
    evidence, preserving deterministic checks.
11. Budget exhaustion producing an honest partial comparison.
12. Source-text instructions attempting to redirect research or fabricate evidence.

Review each final answer for factual support, relevance, student suitability,
tradeoff quality, uncertainty, useful decisiveness, and conversational clarity.
Separately inspect whether necessary evidence was available to the model. This
distinguishes retrieval failure from poor use of adequate evidence.

Compare: legacy baseline; new research with the same model and corpus; and, only
if needed and configured, another model on the same prepared evidence. Record
model/config/corpus versions, provider calls, tool calls, measured/unknown token
usage, cached usage, latency, stop reason and human rubric judgments. Report in
the terminal and PR text; no saved run-output reports unless the user requests
them. Curated test fixtures and scenario inputs are source files, not run reports.

Acceptance requires zero critical errors on the reviewed set: fabricated degree
rules, unsupported credit savings, inferred prerequisite edges, out-of-scope
evidence, or unqualified readiness despite known blockers. Require clear quality
improvement on program comparison and beginner suitability, no material loss on
simple lookups, and explain any latency/cost tradeoff. Do not call a small sample
a guarantee of accuracy or declare rollout passed with unreviewed judgments.

## 15. Offline tests and operational checks

Search for each invariant before adding tests. Extend existing behavior tests
where they own it; add a new research test module only for the new controller/state
behavior. Use shared fakes, fake clock, parameterized cases, and mock transports.
Do not mirror schema constants, SQL strings, tool inventories, or mock call counts
unless the count itself is the bounded-execution behavior under test.

Required behavior coverage:

- A useful candidate absent from initial search can be discovered and cited later.
- Program rows with missing/stale source binding cannot disclose requirements as
  scoped evidence; exact binding works; selected-source and workspace isolation.
- Same-code mention overlap remains distinct from required overlap/credit savings.
- Evidence IDs survive paging, compaction and tool-result replay without swapping
  citations. Final citations resolve to exact texts seen in the final request.
- Clarification/assumption blocks can be uncited; unsupported factual references,
  raw marker injection, and incomplete terminal output are rejected.
- Deterministic preparation conclusions must match actual executed results and
  completed-course assumptions; language alone cannot invent a successful check.
- Repeated searches, finalization reserve, invalid tools and correction attempts
  cannot escape the original operation budget. Unknown usage stays accounted.
- Cancellation, stale source/profile, lost lease, and worker retry cannot publish
  stale advice or repeat an unknown billable execution automatically.
- HTTP and saved conversation use the same behavior. Browser tests cover research
  progress/cancel, citations, final partial notice and follow-up context, while
  pure mapping/rendering rules are tested once outside the browser.
- Forward migrations upgrade existing databases and install cleanly; backup and
  restore retain the research evidence and provenance relationships.

Run relevant tests during each milestone; before rollout run backend pytest
(including actual DB tests, none skipped), ruff, frontend typecheck/build/test,
offline real-API integration, and formatting checks. Run schema parity/migration
checks appropriate to the added migrations. Use a unique permitted pytest temp
directory on Windows as documented in `AGENTS.md`. No real model/network calls
inside pytest.

## 16. Implementation order and completion gates

Implement in this order. Each milestone must be reviewable and tested before the
next. Do not stop after adding tools while leaving chat on the old six-chunk path.

### A. Contract and provenance

Update architecture sections for research mode, judgment, zero-evidence
clarification, program semantics, citations and trace kinds. Add forward
migrations, strict types, controlled source provenance, and deterministic program
bindings. Verify scoped program reads on fixtures and existing imported sources.

Gate: program facts have exact source/revision evidence, or explicit unresolved
status; old conversations and legacy queries still work with the flag off.

### B. Research reads and evidence acquisition

Implement discovery/browse, full record/section reads, evidence register, paging,
mention comparisons and preparation checks. Add idempotent ledger integration
and short-transaction scope checks. Curated career-reference import works through
the same ingest/locator pipeline; include an offline fixture, not invented real
career statistics.

Gate: scripted fake-model tool rounds can find and cite evidence that was not in
the initial search; scope, revisions and exact citation identities survive replay.

### C. Research loop and judgment output

Implement working state, iterative context assembly, adaptive tool choice,
finalization reserve, typed finish, one correction, citation rendering and durable
publication. Wire into `query_response` behind the flag and use the existing
permitted provider route. All advice types use this one workflow.

Gate: an end-to-end fake-provider major/minor comparison, clarification, simple
lookup and budget-limited partial answer pass through the real API/database path.

### D. Saved conversations and UI

Integrate worker guards, progress, final publication, evidence panels, optional
contract fields and mocks. Support follow-up preference changes and historical
evidence revalidation. Display partial outcomes honestly.

Gate: offline browser integration proves first turn, clarification reply,
preference correction, cancellation/reconnect, and citation navigation.

### E. Real quality evaluation and rollout

Run the manual scenario set against the actual configured provider and current
catalog; compare against the baseline using the same scope. Review bad decisions
with their exact evidence before changing prompts, retrieval or model selection.
Seek professor review of the judgment rubric and sample comparisons; until then
keep the rollout explicitly experimental and the feature default off.

Professor review is a later product evaluation, not a dependency that blocks
implementing or delivering the flag-off feature. Do not contact anyone or leave
milestones A-D unfinished while waiting for review. Report its pending status
alongside the actual manual evaluation results.

Gate: reviewed improvements and critical-error checks pass; document measured
tradeoffs. Enable deliberately in deployment configuration. Keep flag-off rollback
through one evaluation cycle, then retire obsolete one-shot planning/prompt paths
in a separate change if research becomes the supported workflow. Never drop old
response snapshots or prerequisite data as part of that retirement.

## 17. Definition of done

V1 is complete when a student can ask a career-to-major question, have the advisor
investigate multiple credible academic paths within its scope, receive a reasoned
conditional recommendation with accurate evidence, and refine it conversationally.
The system must identify the difference between missing evidence and preference,
fetch new evidence when needed, explain meaningful tradeoffs, and stop honestly
when it cannot establish an important rule. Typed JSON, more tools, a larger
context window, or passing citation syntax alone do not meet this outcome.
