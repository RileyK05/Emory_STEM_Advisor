# Catalog context and search planning: implementation handoff

Status: implemented. The scoped catalog directory (`retrieval/directory.py`) and
opt-in query planning (`generation/query_plan.py`, `QUERY_PLANNING_ENABLED`) are
in place; `docs/architecture.md` is the current contract and supersedes this
handoff wherever they differ.

## 1. Outcome

Give the LLM a compact directory of the current courses before it decides what to
search for. Keep the directory at the beginning of the planning prompt so the
provider can reuse its KV/prompt cache. The model uses that directory to propose
course lookups and focused topic searches. Retrieval then supplies the actual
evidence for the final answer.

The initial experiment should support the full catalog, not permanently restrict
career advice to CS, mathematics, and data science. A smaller department directory
is a fallback for connections whose context or operation budget cannot accommodate
the full directory.

Example desired behavior: a question about databases and reliable data pipelines
should cause searches for database systems, programming, and relevant computing
topics. It should surface relevant courses from different departments when their
descriptions support that fit, instead of returning only a degree checklist.

## 2. Evidence behind the change

The natural data-engineering question was replayed against the existing database:

- Keyword search returned 40 candidates, with zero atlas course descriptions.
- Embedding search found DATASCI 350, Data Science Computing, and DATASCI 250,
  Applied Computing, but both disappeared before answer generation.
- The six selected passages included four academic-policy passages, a Data Science
  degree checklist, and a program-page header. No atlas course descriptions reached
  the answering model.
- CS 377, Database Systems, is present and was found by a focused database question.
- Chat currently retrieves first. Its advising tools inspect only the already cited
  courses; they cannot discover courses absent from that initial context.

This feature improves the query-planning stage. It must also avoid sending its new
course candidates through ranking rules that discard them again. It is not a
substitute for reviewing embedding quality or the learned topic groups.

Measured from `data/atlas/emory_college.jsonl` on 2026-10-10:

| Directory | Courses | Characters, codes and titles only |
| --- | ---: | ---: |
| Full atlas | 3,342 | 124,205 |
| CS + MATH + DATASCI | 145 | 5,151 |

The full directory is roughly 31,000 tokens using four characters per token.
That is an estimate, not a LongCat tokenizer measurement. Course codes and
punctuation can raise the actual count; budget approximately 40,000 until measured.
These character counts exclude descriptions, department labels, prompt instructions,
and output-schema overhead. Count the final serialized request before enabling it.

## 3. Catalog directory

Build the production directory from the authorized, current indexed database
snapshot. The local JSONL is useful for a prototype and size measurement, but cannot
represent workspace permissions, source selections, or subsequent database updates.

Start with codes and exact titles, grouped by the fixed department configuration:

```text
Department: Computer Science
CS 350 | Systems Programming
CS 377 | Database Systems

Department: Data Science
DATASCI 250 | Applied Computing
DATASCI 350 | Data Science Computing
```

Requirements:

1. Include only courses in the requested collection whose atlas sources are indexed,
   current, authorized for the workspace, and inside the effective source selection.
   An explicit empty selection stays empty. If the selected sources exclude the
   atlas, the planner must not receive an atlas directory from outside that scope.
2. Sort departments and courses deterministically. Normalize line endings and
   escaping; preserve exact catalog codes and titles. Use a versioned format.
3. Retain a backend mapping from directory entries to course IDs, source IDs,
   locators, and source revisions. Those IDs need not inflate every visible line.
4. Deduplicate only verified identical course identities or verified cross-listed
   aliases. Similar titles are insufficient evidence of equivalence.
5. Keep a content hash and source-revision manifest. Rebuild on atomic catalog
   publication or department/verified alias changes. API and worker must see the
   same version; loading once at startup must not freeze a stale catalog forever.
6. Treat titles and any later excerpts as untrusted source text, not instructions.
   Encode embedded newlines and delimiters so a title cannot impersonate a prompt
   role, department header, or another course.
7. The directory is discovery material. Final claims about topic coverage,
   prerequisites, degree inclusion, credits, and eligibility require retrieved
   source evidence or the existing deterministic tools.

A plain `.txt` file is sufficient. If exported for inspection, place the generated
file under ignored `data/`, with its manifest beside it. Do not ingest it as another
citable source or manually maintain thousands of course entries in `docs/`.

Begin with titles only to measure the benefit at the stated size. A later experiment
may add bounded, catalog-derived description excerpts. Report the increased token
count and avoid cutting a description mid-condition or inventing topic labels.
Do not introduce a model-written catalog summarization job in the first milestone.

## 4. Planning prompt and provider cache

Application memory and provider KV memory are separate:

- The application may memoize the rendered directory to avoid rebuilding it.
- Remote requests must still carry the required directory context, or use an
  explicitly supported provider cache-reference API.
- A stable prefix makes provider cache reuse possible. It does not guarantee a hit,
  permanent retention, or a reduction in the model's occupied context window.

Construct the planning request in this order:

1. Fixed planning instructions and a fixed output schema.
2. The deterministically rendered catalog directory, clearly delimited as data.
3. The changing user question and minimal conversation context needed to understand it.

Put request IDs, timestamps, student details, and variable history after the stable
directory. Changing those values must not alter the reusable prefix. Avoid changing
the schema or tool definitions between otherwise identical planning requests.

The planning instructions should say: use the directory to propose searches and
candidate course codes; verify the results against retrieved records; return a
bounded search plan rather than an answer to the student. Directory text and student
messages cannot change authorization or prerequisite rules.

For the current LongCat route, send the stable prefix through the existing
openai-compatible provider seam. Its published pricing distinguishes cached and
uncached input, but does not establish that every repeated request will hit a cache.
Verify actual response usage fields and supported controls before adding any
provider-specific fields. Never assume OpenAI or Anthropic cache parameters work
merely because an endpoint accepts a compatible message format.

Keep provider controls inside the adapter/factory boundary. If another connection
supports explicit caching, map a provider-neutral cache policy to that adapter's
documented controls. Do not import concrete providers into retrieval or advising.

Local directory cache keys must account for collection, workspace authorization,
source selection, source revisions, department configuration, alias mapping, and
format version. Provider cache handles, if introduced, must additionally be tied to
the provider connection and revision, endpoint, and model. Never reuse a handle
across different credentials or an incompatible scope.

Do not add a billable startup warm-up call by default. A normal planning request
can populate the cache. Optional warm-up belongs to the manual experiment and must
be accounted for.

Official references:

- [LongCat Chat Completions](https://longcat.chat/platform/docs/api/chat)
- [LongCat cached-input pricing](https://longcat.chat/platform/docs/pricing/longcat-2.5)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  for a provider with explicit cache controls; not proof of LongCat compatibility.

## 5. Run the planner before retrieval

The intended query path is:

```text
Resolve collection, permissions, source fences, and eligible model connection
  -> establish one operation budget and request identity
  -> load the scoped catalog directory
  -> LLM proposes a bounded search plan
  -> validate the plan against the scoped directory
  -> retrieve course records and focused topic evidence
  -> select evidence at course level
  -> generate and validate the grounded answer
```

Use the existing provider seam and evaluated routing. A low-cost planning profile
can serve this task when configured and authorized for its data. Until a separate
profile exists, use an eligible configured connection; do not embed a new API key,
model ID, or endpoint in planning code.

A proposed provider-neutral plan:

```json
{
  "intent": "course_exploration",
  "topic_queries": [
    "database systems query languages storage reliability",
    "Python cloud computing reproducible computing workflows"
  ],
  "candidate_course_codes": ["CS 377", "DATASCI 350", "DATASCI 250"]
}
```

This is an illustrative result, not a hardcoded data-engineering answer. The model
must discover candidates for other goals too.

Use typed validation with bounded field lengths and lists. Initial experimental
limits: at most eight distinct topic queries and twenty candidate course codes,
configurable and versioned. Treat all strings as search data. No raw SQL, model
supplied source IDs, arbitrary URLs, or model-supplied authorization filters.

Resolve every proposed code against the authorized snapshot; reject unknown or
out-of-scope codes without expanding scope. Retain the original student question
for answer generation and trace interpretation. Do not replace it with a synthetic
question that erases the user's constraints.

Initially use planning for interest/career exploration. Straight course facts,
degree requirements, and sequence requests retain their existing paths. Ambiguous
questions can fall back to the original retrieval rather than guessing an intent.
When planning is disabled, unsupported, invalid, or fails, record the reason and
use the existing scoped retrieval if the shared budget permits. Cancellation and
authorization failures must propagate; they are not fallback opportunities.

For saved conversations, resolve phrases such as "that career" from bounded
conversation context. Do not treat a previous answer or its old citation numbers
as current institutional evidence.

## 6. Make the proposed searches effective

Fetch the scoped current descriptions for validated candidate codes directly.
Run the focused topic searches separately, deduplicate the resulting course
identities, and combine the evidence. Keep exact-code candidates available for
evaluation even if their titles were missing from the top forty semantic matches.
Finding a code in the directory is not enough to recommend it: its description
must still support the student's interest.

For exploration, select evidence using course identity and relevant skill coverage,
rather than treating the entire atlas file as one diversity bucket. Preserve
evidence provenance and citation identity. Distinguish course descriptions from
degree checklists and general academic policies; those documents remain useful for
their appropriate question types.

Inspect the current min-max normalization, relevance floor, and embedding-only
quota. Do not equate the strongest weak keyword match with established relevance.
Ensure a relevant course cannot disappear solely because unrelated degree pages
set a different score range. Make changes specific and testable; do not globally
remove relevance checks or increase every context budget without measurement.

Bound the intermediate candidate pool separately from the final shortlist. The
answer can name at most five courses while the system considers substantially more.
Fetch the descriptions and any explicitly requested requirements for the chosen
courses before generating their explanations.

The learned department/topic graph may supply additional candidate groups in a
follow-on stage: goal -> relevant departments -> topic groups -> course records.
The first milestone can prove directory-guided search without rebuilding the graph.
Do not create course-to-course similarity edges, force every department into every
answer, or generate hand-written nudges with an LLM.

## 7. Budgets, routing, traces, and worker lifecycle

These are required integration changes, not optional cleanup:

- `query_service.py` currently selects the model and creates `GenerationOperation`
  after retrieval. Planning needs an authorized connection and persisted budget
  before its first model call. Refactor the shared API/worker path accordingly.
- Reuse one logical operation for planning, answer generation, tool calls, repair,
  and retries. Establish a stable operation ID before planning, then associate the
  retrieval trace with it. Operations already have their own persistent identity;
  do not fabricate a completed retrieval trace merely to satisfy an audit foreign
  key. Ensure planning-only failures retain their accounted operation calls.
- `estimate_prompt_reservation()` currently reserves one unit per serialized byte.
  The 124 KB directory therefore consumes approximately 124,000 reservation units,
  not 31,000. Make reservation units explicit. Either configure a sufficient bounded
  budget for this experiment or add a verified model tokenizer with conservative
  overhead and the existing fallback. Dividing by four is not a reliable hard limit.
- Verify the chosen model's actual input/context limit separately from its output
  limit. Reserve space for instructions, schema, question, history, and output.
  For a constrained local model, use the configured smaller-directory strategy or
  skip planning with a recorded reason. Never silently truncate half the catalog.
- Account planning calls, including failed calls, through the existing operation
  controller. Cached tokens still count toward context capacity and must retain
  correct provider usage accounting. A cache miss cannot bypass the token budget.
- Keep the full directory on the planning request only in the first milestone.
  Final answer and subsequent tool requests use selected evidence. Repeating the
  full catalog on every tool call would consume the aggregate operation budget.
- Preserve cancellation, timeout, source-revision fences, and publication fences.
  Recheck the snapshot before publishing so a changed catalog cannot silently
  invalidate a plan. Release read transactions before provider or embedding I/O.
- Keep API and worker construction together in `app/runtime.py`. A worker restart
  or process-local cache miss must still produce the same scoped directory.
- Keep directory data out of the saved conversation transcript and historical
  context budget. Persist its identity/manifest where needed for replay; avoid
  duplicating the entire catalog into each saved turn's public metadata.

Trace the original question, validated plan, planning model, directory version/hash,
source fences, fallback reason, candidate counts, and selected evidence. Record
measured input/output usage and provider-reported cache-read/cache-write usage when
available. Missing cache telemetry means unknown, not zero and not a confirmed hit.
Keep cost estimates separate from context-token accounting.

If new public trace fields are needed, update Python schemas, frontend API types,
and `architecture.md` together. Start with existing debug surfaces rather than a
new UI. Use named SQL queries; add a new numbered migration only if persistence
changes require one. Do not edit an applied migration or the baseline.

## 8. Department and legacy-code consistency

The imported atlas contains DATASCI course codes, while the stored Data Science
degree document and some raw prerequisite descriptions still mention QTM codes.
Represent current departments and verified historical aliases explicitly.

Do not rename every QTM reference by string substitution. Verify equivalences from
authoritative data before adding aliases; preserve original source text and
provenance. Unresolved aliases remain unresolved. The LLM cannot supply missing
prerequisites or edit the deterministic graph to make a recommendation work.

This reconciliation can be a separate change, but the directory must expose actual
current atlas codes from its first version.

## 9. Suggested code touchpoints

Inspect the current implementations before editing; other workers have changed this
repository and the worktree contains substantial uncommitted work.

| Area | Existing files / likely change |
| --- | --- |
| Directory snapshot | New small discovery module under `app/retrieval/`; scoped named queries under `app/db/queries/` |
| Shared startup/cache service | `app/runtime.py` and existing API/worker wiring |
| Planning request and validation | Small separate module using `app/llm/base.py`; existing provider factory/runtime |
| Query lifecycle | `app/api/query_service.py`, shared by HTTP and workers |
| Candidate retrieval | `app/retrieval/retrieve.py`, seams, and scoped course queries |
| Course evidence selection | `app/retrieval/fusion.py` or a narrowly scoped exploration selector |
| Evidence and answer validation | `app/generation/answer.py`, `answer_tools.py`, and existing advising snapshots |
| Token/call accounting | `app/generation/operation.py`, `app/operations/budget.py` |
| Provider cache usage/controls | `app/llm/base.py`, `adapters.py`, and factory; preserve payload parity where relevant |
| Configuration | All environment settings in `app/config.py`; versioned retrieval tunables in `configs/retrieval.toml` |
| Public trace changes, if necessary | API schemas, serializers, `frontend/src/api/types.ts`, contract tests |

Expose enable/disable, directory mode, planning limits, input budget, and fallback
policy as validated configuration. Start opt-in until the experiment demonstrates
better answers. Keep small-model context limits and private routing behavior intact.

## 10. Verification

Extend the relevant existing tests rather than duplicating test suites. Use shared
fakes, mock HTTP transports, and parametrized cases. No live APIs or model downloads
in pytest; real database tests must run, not skip.

Behavior checks should cover:

- Current, indexed, authorized, selected sources only; explicit empty scope;
  unauthorized and historical courses cannot enter the planning directory or lookup.
- Deterministic rendering, escaping, snapshot invalidation, concurrent replacement,
  and consistent API/worker directory versions.
- Valid/invalid plans, unknown codes, bounded searches, correct lookup and evidence,
  cancellation, planner failure fallback, and no cross-workspace cache reuse.
- Planning and generation share budgets; cache misses and oversized directories are
  handled; unknown usage remains conservatively reserved.
- Relevant CS and DATASCI candidates survive selection when supported; policies
  cannot crowd them out merely through generic keyword matches. Exact-code lookup
  does not force an unsupported recommendation.
- Final answers use current citations; directory-only claims cannot masquerade as
  description evidence. Prerequisite graph contents remain unchanged by planning.
- Saved conversation turns do not acquire a full-directory history payload.
- Optional cache controls preserve sync/stream/batch semantics for providers where
  those controls are supported. Unknown usage fields remain compatible.

Then run a live frontend experiment using the authorized configured LongCat route:

1. Reuse the original data-engineering prompt and several natural paraphrases.
2. Add at least ten distinct career/interest goals, including goals outside CS and
   data science. Include deliberately unsupported interests and ambiguous questions.
3. Compare feature off/on with the same catalog and configuration. For each failure,
   inspect directory -> plan -> candidates -> selected descriptions -> answer.
4. Include a saved follow-up and a deterministic prerequisite/sequence question.
5. Repeat otherwise identical planning requests to measure cold and warm latency
   and cache usage. Do not infer a hit from speed alone. Check one changed-question
   case and one changed-catalog case.

Measure supported-course recall, useful coverage of the requested skills, unsupported
claims, citation support, fallback rate, planning latency, input tokens, cache reads,
and total operation cost. Review natural answers, not just successful HTTP responses.

For the data-engineering regression, inspect CS 377, DATASCI 250, and DATASCI 350 as
known relevant candidates. They are diagnostic examples, not a mandatory answer to
every paraphrase. Determine additional expectations from actual catalog descriptions;
do not hardcode a course list into production planning.

Run the affected backend tests and lint. Run frontend typecheck/build and relevant
integration checks if public contracts or frontend behavior changed. Summarize
verification in the final response; do not generate additional status reports.

## 11. Delivery sequence and completion criteria

1. Build and measure the scoped full directory. Verify actual tokenization/context
   capacity and the serialized-byte budget before sending it to a model.
2. Add the opt-in planner with stable prefix, shared accounting, routing, and bounded
   validation. Preserve the feature-off behavior and scoped failure fallback.
3. Connect validated course lookups and topic searches to course-level evidence
   selection. Confirm the known DATASCI candidates reach the answer context.
4. Preserve and expose cache telemetry where the provider supplies it; verify cold
   and warm behavior with the configured endpoint.
5. Run the offline checks and live career-question comparison. Tune from observed
   failures and update `architecture.md` to describe the implemented behavior.

Done means the full-directory mode works within an explicitly measured budget,
natural career questions retrieve and explain supported courses beyond generic
degree checklists, deterministic answers retain their constraints, catalog changes
invalidate the directory correctly, and repeated-prefix cache behavior is measured
or clearly reported as unverified. Passing infrastructure tests alone is insufficient.

The implementing model should deliver the code, configuration guidance, and a short
account of actual answer improvements and remaining failures. No extra reports,
database replacement, graph redesign, or unrelated frontend changes are requested.
