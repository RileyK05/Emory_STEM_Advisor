# Artifacts: what still fails, and the handoff prompt

> Implemented in `bd00209`..`12e97ec`. The next round is
> [artifact-suite-fixes.md](artifact-suite-fixes.md).

Written 2026-11-09 after `571b174`. The reprompting pipeline
([artifact-reprompting.md](artifact-reprompting.md)) publishes correct artifacts
when it finishes, and every case finishes in time. On the live sample (A01-A06)
3 of 6 cases pass. The other three fail for the reasons below. Each reason comes
from the stored tool results (`tool_executions.result_json`) of the failed runs.

## Problem 1: a stage runs out of corrections (A01, A04)

A stage tool rejects the draft, hands the reason back, and the model gets
`ARTIFACT_REPAIR_ATTEMPTS` (2) more tries. In A01 and A04 the model broke a rule
on every try, so the run failed. The refusals, in order:

**A04, outline (Neuroscience and Behavioral Biology BS checklist):**

1. `sections.4.courses: List should have at most 20 items after validation, not 23`
2. `title: Field required` (the correction dropped the title)
3. `this request asks for a term plan and names courses; put those exact course
   codes in selected_courses and name the program`

**A01, sections (Biology BS four-semester plan):**

- `binding names unknown block 'term3-plan.row1'`
- `every content block needs a binding: advisor-biol141, advisor-intro, advisor-note, advisor-questions`
- `section: Extra inputs are not permitted`
- `a term section cannot name other courses: BIOL 142, CHEM 202, MATH 116`
- `a term section cannot name other courses: CHEM 150, CHEM 202Z, MATH 111`

What these have in common: the model is asked to do bookkeeping that code
already knows how to do.

- **Term course lists.** For a plan, `plan_sequence` decides each term's courses
  (`_replace_plan_shape`), yet the model still writes `courses` into outline
  sections (and is refused at 21+), and term sections must write prose that
  names only their own courses. Naming a prerequisite ("after BIOL 142") trips
  the "cannot name other courses" rule. That rule is needed: the benchmark's
  K6 reads every course code in a term's text as scheduled in that term.
- **Bindings.** The model must invent block ids and bind every block to slots.
  It drops bindings, binds rows that do not exist, and adds a stray `section`
  key.
- **Checklist vs plan.** A04 asked for a checklist; the outline rule treated it
  as a term plan and demanded `selected_courses`.

Fix direction (decide in code, not in the prompt):

1. For a term plan, the outline does not carry courses at all. Strip or ignore
   `courses` in outline sections before validation; code fills them from the
   planner. Raise or remove the 20-course cap where code owns the list.
2. Code writes each term's course list itself: a list or table of `CODE — title`
   lines, each bound to that course's record slot (the same evidence rule as the
   fallback section since `571b174`). The model writes only a short note for
   the term, and the note may not contain a course code. That removes the
   "cannot name other courses" refusals and the binding bookkeeping in plan
   sections.
3. A refusal that repeats should show the model its previous arguments and the
   one rule broken, and keep fields it got right. "title: Field required"
   after a correction means the model rewrote the call from memory.
4. A checklist is not a term plan: only plan requests ("plan", "semester",
   "four-year", "schedule") take the planner path.
5. Bindings: when a document section has exactly the slots of one course or one
   program, code may bind every block to those slots. Never bind to a slot that
   does not name the content (see the fallback fix).

## Problem 2: research answers in chat instead of asking for the document (A03)

"I took MATH_OX 111 and CHEM_OX 150 at Oxford. Plan my Chemistry BS from here."
got a full answer and no `decision.artifact`. Since `571b174` research's context
carries `artifact_requested` for this question (`_asks_for_artifact`), and the
prompt rule covers "make". A02 and A04 now hand off; A03 does not.

Fix direction: when `artifact_requested` is set and the finish has no artifact,
treat it like other finish validation. Reject the finish once with the reason
("the student asked for a plan to keep; set decision.artifact with kind and a
brief") so the model corrects it in the same round. Only once: a second finish
without an artifact publishes as is. Keep the prompt within the research
prompt-size tests (`test_context_compaction_preserves_exact_passages_*`,
`test_finish_request_retains_declared_candidate_program_evidence`).

## Prompt for the implementing model

> You are working in `C:\Users\moomi\personal_projects\Emory_STEM_Advisor`.
> Read `AGENTS.md`, then `docs/plans/artifact-reprompting.md`, then
> `docs/plans/artifact-fixes-handoff.md`. The last one lists what still fails in
> the artifact pipeline (`backend/app/generation/artifact_pipeline.py`,
> `native_tools.py`) and in research's artifact handoff
> (`backend/app/generation/research_controller.py`, `research_final.py`), with
> the exact refusals from the failed runs and a fix direction for each.
>
> Fix Problem 1 items 1-5 and Problem 2, in that order, one commit each. For
> each fix, first add a row to the existing offline tests that fails without
> it: `backend/tests/test_artifact_generation.py` and
> `backend/tests/test_native_artifact_tools.py` for the pipeline,
> `backend/tests/test_research_types.py` or `test_research_data.py` for
> research. Use the shared fakes in `backend/tests/fakes.py`
> (`ArtifactStageProvider`, `FakeProvider`); add no new test files and no
> per-file provider classes. Sections run in parallel, so never assert on
> request order.
>
> Rules: never edit `backend/scripts/manual/artifact_benchmark_cases.jsonl`, any
> K-check, or `backend/app/artifacts/checks.py` acceptance. Never let a model
> write, infer or reorder a prerequisite; term order comes from
> `advising.sequence.plan_sequence`. Keep generation greedy and add no sampling
> or reasoning-effort defaults. Keep research's prompt within its size tests.
> Evidence must name what it supports: never bind a block to a slot that does
> not mention its content.
>
> Gates before each commit, from `backend/`: `../.venv/Scripts/python -m ruff check .`
> and the touched test files; before the last commit, the full
> `../.venv/Scripts/python -m pytest` and from `frontend/` `npm run typecheck`
> and `npm test`. Do not run the live benchmark or any script that calls a real
> model; Claude runs the live suite afterwards. Do not push. Finish with a
> short list of what changed and anything you could not fix.
