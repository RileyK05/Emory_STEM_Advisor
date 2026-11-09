# Artifacts by reprompting

Written 2026-11-09. The user decided P3.2's open question: artifacts are built by
reprompting, a chain of small calls with checks between them, not one call
that must emit the whole artifact. Another model implements this plan; Claude
reviews it after.

## Why the one-shot path fails

`ArtifactHandler._generate` sends one request: every context passage, the full
native spec schema and the brief. The model must return the entire spec as one
JSON string, through `advisor_stage_artifact` (`_run_with_tools`) or a
structured output (`_run_plain`). Measured on minimax-m3.1-flash (P3.2 live
runs, 2026-11-09):

- 4 to 6 of 30 artifact cases publish per run.
- Failed calls end `finish_reason=length` at the 8,192-token cap after about
  30,000 characters of hidden reasoning: the model drafts the whole artifact in
  its reasoning before writing it. Lower reasoning effort, thinking off (the
  provider ignores it) and a 16,384 cap (a 120 s provider timeout) did not fix it.
- A bad draft can be corrected since `2e679a2` (the stage tool hands the
  validation error back), but a call that runs out of tokens has nothing to
  correct.

The artifact is also grounded on the wrong evidence. The chat turn's research
run read and cited specific records, but the artifact job re-runs keyword
retrieval on `brief + "Published answer:" + answer` and works only from those
chunks. That is how plans name courses the program does not list (K5) or put a
course before its prerequisite (K6).

## The pattern

Agent harnesses do not ask for a large output in one shot. They chain small
steps, check each step in code, and send the failure back to the model as the
next prompt:

- **Prompt chaining with gates:** a fixed sequence of calls, each working on
  the last one's output, with a programmatic check between steps.
- **Outline, then expand:** a short skeleton first, then one call per section
  (Skeleton-of-Thought). The skeleton is cheap to validate and fix before the
  expensive part.
- **Evaluator-optimizer:** a generator and an evaluator in a loop. Here the
  evaluator is our own deterministic validation and checks, never a model, so
  runs stay reproducible.
- **Edits, not rewrites:** coding harnesses change a file with targeted edits
  (replace this span, insert after that one), never by re-emitting it whole.
  Revisions work the same way.

References: Anthropic, "Building effective agents" (anthropic.com/research/building-effective-agents);
Ning et al., "Skeleton-of-Thought" (arXiv 2307.15337).

Each call asks for a small output (an outline, or one section), so a model that
reasons first still fits its allowance.

## Design

The orchestration lives in code: a fixed pipeline in a new
`app/generation/artifact_pipeline.py`, called from `ArtifactHandler._generate`.
The model never decides what step comes next.

```
grounding -> outline -> section 1..N -> assemble -> check -> (targeted repair) -> stage
```

Every model call is one tool conversation with a single terminal tool
(`ToolConversationController`, `terminal_tool_names`). A terminal tool that
rejects its input returns `{"error": ...}`, and the controller hands it back to
the model in the same conversation (`_is_refusal`, since `2e679a2`). That is the
repair loop: the model sees exactly what failed and calls again. Each stage
allows `ARTIFACT_REPAIR_ATTEMPTS` corrections before the run fails.

### Stage 0: grounding

- A turn-made artifact grounds on the research run that proposed it: the
  evidence records `ResearchStore.evidence(run_id)` returns, in their stored
  order, as the numbered slots. Pass the research `runId` through the artifact
  run's inputs (`ArtifactGenerationInput.researchRunId`, set in
  `conversations/worker.py` where `inputs` is built today).
- A direct run (no research run) keeps today's `retrieve_artifact_context`.
- Course facts for plans and checklists come from the database, not the model:
  load each candidate course's record and prerequisite tree the way research's
  `read_courses` does, and the program's requirement lines the way
  `read_program` does. These become fixed slots too.

### Stage 1: outline

One call, terminal tool `advisor_stage_outline`. Input is the brief, the kind,
the numbered slots (source name, locator, first 300 characters only), and for a
revision the current outline (see Revisions). Output, per kind:

- **document:** `title`, then up to `ARTIFACT_MAX_SECTIONS` sections, each
  `{id, heading, purpose, block_types, source_slots}`.
- **workbook:** `title`, then sheets, each `{id, name, columns: [{key, header,
  type}], row_purpose, source_slots}`; charts as `{sheet_id, type, title,
  value_column, category_column}`.
- **html:** `title`, then sections like a document's.
- **plan (a document or workbook whose brief is a term plan):** additionally
  `program` and `selected_courses` (exact codes) plus `completed_courses`
  (only codes the student stated; research already enforces this).

The tool validates in code and refuses with the reason: ids unique and
well-formed, counts within limits, every slot in range, block types legal for
the kind, every selected course a real course in the collection (campus
equivalents allowed), and every selected course named by the program's
requirement lines. A completed course must not be selected.

### Stage 2: sections

One call per outline section, terminal tool `advisor_stage_section`. Each call
gets:

- the outline (all headings and purposes), so the section knows its place;
- the section's own slots in full text, and no others;
- one line per section already written (its heading and first sentence), so
  sections do not repeat each other;
- the block schema for only the block types this section uses.

Output is the section's blocks (document, html) or its rows (workbook), each
with `source_slots`. The tool checks the blocks against the native models, the
bindings against this section's slots, a per-section size cap, and the same
course-existence rule as the outline. Grounded mode requires a binding for
every content element. Authoring mode forbids bindings, as today.

**Workbook rows are data, not cells.** The model returns
`rows: [[value, ...], ...]` keyed by the outline's columns. Code assigns cell
ids, row and column numbers, header styles and column widths. Formulas come
only from the outline's declared column types (a total column), written by code.
The model never writes a formula or a cell address.

**HTML sections are fragments.** The model writes one fragment per section.
Code wraps them in a fixed page template and runs the existing sanitizer once
on the assembled page.

Sections run in order in v1. Parallel sections are R5, only if the time bar is
missed.

### Stage 3: plans are ordered by the planner

A term plan's order is not the model's to choose. After the outline, code runs
`advising.sequence.plan_sequence` on the selected and completed courses with
the campus equivalents. Its stages become the plan's terms, filled in order up
to the outline's per-term load. A blocked course (unparsed or unresolved
prerequisite) goes in a final "check with your advisor" section with the
planner's reason. Section calls then write each term's text around a fixed
course list they may not change: the tool refuses a term section that adds,
drops or moves a course.

This keeps the hard rule: the order comes from the parsed graph, and no model
writes or infers a prerequisite.

### Stage 4: assemble and check

Code assembles the native spec (`DocumentSpec`, `WorkbookSpec`,
`HtmlArtifactSpec`), namespacing element ids by section, merging the bindings,
and runs `validate_candidate` and `validate_generation_size`. Then it runs the
checks the benchmark measures, as product code rather than only benchmark code:

- every course code named exists (K4), and in a plan every course is on the
  program's requirement lines (K5);
- no course sits in or before the term of a required prerequisite (K6);
- every content element resolves to a slot (K7);
- no prompt text, model name, job id, status code or stack trace (K10).

A failing check names the section that holds the problem. That section, and
only that one, goes back to stage 2 with the check's message as the prompt
("PSYC 999 is not a course; use only codes from your slots"). The rest are kept
byte for byte. At most `ARTIFACT_REPAIR_ROUNDS` rounds, then the run fails with
the check's student-safe message.

Put the shared checks in `app/artifacts/checks.py`. The benchmark imports them,
so product and benchmark cannot drift. Do not change the benchmark's cases or
pass criteria.

### Revisions are edits

A revision does not regenerate the artifact. The outline call receives the
current artifact as its outline: section ids, headings and first sentences. Its
terminal tool `advisor_stage_edits` returns operations:
`keep`, `replace(section_id, purpose)`, `insert_after(section_id, section)` and
`delete(section_id)`. Only replaced and inserted sections go through stage 2.
Kept sections, and their evidence, carry over unchanged. Assembly and the checks
run on the whole result as usual.

### Progress

New stages, reported through `context.progress` and labelled in the chat:
`outlining` ("Planning the outline…"), `writing` with `{section, total}`
("Writing section 2 of 5…"), `checking` ("Checking courses and order…") and
`repairing` ("Fixing a section…"). Add them to `PROGRESS_STAGES` in
`conversations/worker.py` and `STAGE_LABELS` in `frontend/src/components/chat/model.ts`.
Check how artifact run progress reaches the UI before adding a new event.

### Budget and settings

One operation per run, as today, sized for the chain. These replace the
`OPERATION_*` settings, which only artifacts use since research has its own:

| Setting | Default | Meaning |
|---|---|---|
| `ARTIFACT_MAX_SECTIONS` | 8 | outline sections (and plan terms) |
| `ARTIFACT_REPAIR_ATTEMPTS` | 2 | corrections a stage tool accepts per call |
| `ARTIFACT_REPAIR_ROUNDS` | 2 | check-driven section repair rounds |
| `ARTIFACT_MAX_OUTPUT_TOKENS` | 8192 | per call (unchanged) |
| `ARTIFACT_OPERATION_MAX_CALLS` | 40 | 1 outline + sections + repairs |
| `ARTIFACT_OPERATION_TOKEN_BUDGET` | measure | set from R0's runs: p95 plus 25% |
| `ARTIFACT_OPERATION_TIMEOUT_SECONDS` | 300 | whole run |

Sampling stays greedy (`temperature=0`); do not add a reasoning-effort default.
Every attempt is recorded with `record_attempt` and accounted in the operation.

## Work items

| ID | Do | Done when |
|---|---|---|
| R0 | Baseline: run `scripts.manual.artifact_benchmark` three times on the current commit. Record per case: K1-K11, calls, tokens and seconds. | The numbers are in the ledger's P3.2 row. |
| R1 | Stage 0 grounding: `researchRunId` through the run inputs; slots from research evidence plus the course and program records. | Offline test: a turn-made artifact's slots are the research run's evidence, and a direct run still retrieves. |
| R2 | The pipeline for documents and HTML: outline, sections, assembly, the two terminal tools with in-conversation repair, progress stages, and the settings. The one-shot path is still there behind nothing: R6 deletes it. | Offline tests with a scripted fake: a draft with an unknown course, a bad slot and an oversize section is refused, corrected and published; a fake that never corrects fails the run cleanly with usage accounted. |
| R3 | Workbooks: rows as data, code-built cells, formulas only from declared column types, charts from the outline. | A workbook built from rows exports, reopens (K8) and has no model-written formula. |
| R4 | Plans: `plan_sequence` orders the terms; term sections may not change their course list; blocked courses get the advisor section. Shared checks in `app/artifacts/checks.py`, used by the pipeline and the benchmark. | Offline: a model that moves a course between terms is refused; a prerequisite cycle or unparsed rule lands in the advisor section. The benchmark imports the shared checks. |
| R5 | Revisions as edits. Parallel sections only if R7 misses the time bar: one connection per worker thread, the provider gate still applied. | A revision that replaces one section leaves every other section and its evidence byte-identical. |
| R6 | Delete the one-shot path: `artifact_messages`, `OUTPUT_SCHEMA`, `_run_plain`, `_run_with_tools`, the `advisor_stage_artifact` tool, the `structured_output` routing requirement, and `TOOLS_ENABLED` and `TOOL_MAX_CALLS` if nothing else reads them. Rename `OPERATION_*` to `ARTIFACT_OPERATION_*` in `config.py`, `.env.example`, Bicep and the deploy scripts. | No caller remains; the gates are green. |
| R7 | P3.2: three benchmark runs on one commit. | The P3.2 bar in `production-launch.md`. On a failure, follow P3.2's steps: fix the product, add an offline row, rerun. |

R1 and R2 can be built together. R3 and R4 need R2. R6 lands after R4.

## Rules for the implementer

- Never edit `artifact_benchmark_cases.jsonl`, a K-check or a pass criterion.
  R4 moves check logic into `app/artifacts/checks.py` without changing what it
  accepts; any behavior difference is a question for the user, not a fix.
- No model writes, infers or reorders a prerequisite. Order comes from
  `plan_sequence` on the parsed graph.
- Keep generation greedy. The only sampling exception in the codebase is
  research's reasoning effort.
- Tests follow AGENTS.md: rows in the existing `tests/test_artifact_generation.py`
  and `tests/test_native_artifact_tools.py` tables, the shared fakes in
  `tests/fakes.py` (add one scripted artifact-stage fake there if needed), no
  new test files for variants, and no tests of manual scripts.
- No transaction is held across a model call; release the borrow before each
  provider call as `_run_with_tools` does now.
- The live benchmark needs a real provider and key. The implementer does R0 and
  R7 only if the user approves the spend; otherwise stop after R6 and report.
