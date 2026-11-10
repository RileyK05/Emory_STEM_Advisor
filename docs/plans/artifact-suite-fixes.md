# Artifacts: fixing what the generated suite found

Written 2026-11-09, after the round in
[artifact-fixes-handoff.md](artifact-fixes-handoff.md) landed (`bd00209`..`12e97ec`).
The offline gates pass on that code. The live suite
([artifact-test-suite.md](artifact-test-suite.md), seed 11, 48 cases, one
repeat, `minimax-m3.1-flash`) passed **8 of 48 (17%, 95% CI 9-30%)**.

The parts the last round changed work. In every plan that published, no
completed course was scheduled and no term needed a code-written fallback.
Code-built term schedules never failed, and run times stayed under the K1
limits. What fails is everything around them: the non-term sections, HTML and
workbook sections, and five research-side handoff decisions.

## Implementation status (2026-11-10)

Done, uncommitted, offline gates pass (ruff, 1592 backend tests):

- 0.1 stage name added to a tool-conversation failure and a warning log.
- 0.2 suite: `tool_conversation` and `revise_blocked` outcomes; the fake-course control uses the A26 wording.
- 1.1-1.5 per-kind section models, block-level citations, one code-built `html` binding, numbers in table cells, null for an unused string.
- 2.1 code hands the student's request to the artifact pipeline when research leaves it out.
- 2.2 noun-phrase requests and "roadmap"; 2.3 summaries (research-48); 2.4 a program that is not in scope gets no artifact; 2.5 plan titles come from the program record.

Not done:

- **2.6** (do not repeat a course code that is not in the Atlas). It contradicts behavior the code already has on purpose: salvage writes "There is no PSYC 219 in the Course Atlas." (`test_research_types.py`), and research's `no_current_course_note` tells the model to name the code. Rejecting answers that name it would fight both. K4 (frozen) still fails any reply that names the fake code, so A26-style cases can fail K4 while behaving as designed. Needs a decision: change the existing wording to avoid the code, or ask for a K4 change.
- Phase 3 (workbook column keys, code-written course rows): only if truncation and column-count refusals remain after the Phase 4 run.
- Phase 4: the live suite has not been run on this code.
## Evidence

### Results by family

| Family | Pass | Where it stopped |
|---|---|---|
| plan_fresh | 2/4 | 1 section, 1 no handoff |
| plan_completed | 0/4 | 4 section |
| plan_oxford | 0/4 | 4 section |
| checklist | 0/4 | 4 section |
| workbook_tracker | 0/4 | 4 section |
| workbook_plan | 1/4 | 2 section, 1 no handoff |
| compare | 0/4 | 4 section |
| html_page | 0/4 | 4 section |
| revise | 0/4 | 3 section, 1 checks; all 4 had an unpublished base |
| summary | 0/4 | 4 section; purpose was always `create` |
| control_factual | 4/4 | |
| control_fake_course | 0/2 | made an artifact; named the fake code |
| control_fake_program | 1/2 | one published a plan for a substitute program |

### Stage refusals, all cases

| Count | Refusal | Cause (below) |
|---|---|---|
| 120 | `html: Input should be a valid string` | F1 |
| 44 | `bind the HTML fragment with element_id "html"` | F2 |
| 41 | `an HTML section uses html, not blocks or rows` | F2 |
| 25 | `{paragraph,heading,table,list}.source_slots: Extra inputs are not permitted` | F3 |
| 16 | `table.rows.N.N: Input should be a valid string` | F4 |
| 11 | `every binding must use this section's own source slots` | F3 |
| 8 | `a row has N values; the sheet has N columns` | F6 |
| 4 | `an HTML section needs one binding for the page fragment` | F2 |
| 3 | `binding names unknown block '…'` | F3 |

Workbooks also had 16 truncated calls (`finish_reason=length` at the 8,192-token
output cap). Their p95 output was 73k tokens per run.

## Root causes

### F1. The provider sends `null` for fields it does not use (biggest single cause)

`StageSection` (`backend/app/generation/native_tools.py`, the class after
`SectionRow`) has one shape for all three kinds: `blocks`, `html`, `rows` and
`bindings`. `html` is `str = ""`. Minimax fills every field it sees, and for a
document section it sends `"html": null`. Strict validation refuses it. The
error doesn't say what to send, so the model repeats the same call on every
correction and the stage runs out (`ToolCallLimitExceeded`, controller.py
around line 131). The same section often refused 3 times in a row. In a live
rerun of G11-plan_fresh-2, one section was refused three times for `html: null`.
Another sent null twice, then a non-empty `html` on a document section.

`repair_arguments` (`backend/app/tools/registry.py`) already undoes this quirk
for lists: null, `{}` or `""` for a required list becomes `[]`. Its comment
names minimax. Strings get no such repair.

The bug isn't new. It appears at a few refusals an hour in the runs before
`bd00209`. It now dominates because plans have more non-term sections and the
suite covers more kinds.

### F2. HTML sections are asked for a binding the prompt never mentions

`_validate_html_section` requires exactly one binding whose `element_id` is the
literal `"html"`. The section prompt in `_write_section` says only "an html
section returns `html` only". The model guesses ids like `page` or `roadmap`,
sends several bindings, or sends `blocks` alongside `html`. html_page scored
0/4, and the revise cases built on HTML failed too.

### F3. Models put citations on the block; the schema wants them in a separate list

Workbook rows carry their own `source_slots` (`SectionRow`). Document blocks
don't: their citations go in a parallel `bindings` list keyed by block id. The
model keeps writing `source_slots` on the block, as rows do (25 refusals). It
also binds ids that don't exist and misses blocks (14). The information is
correct; it's just in a place the schema doesn't accept.

### F4. Table cells must be strings; the model sends numbers

`TableBlock.rows` is a list of string lists. The model writes credit hours as
`4`, not `"4"` (16 refusals). This is the same kind of re-encoding as F1. A
number shown in a document table means the same thing as its string.

### F5. Five runs failed with no recorded cause

Five runs, including suite job `c091e034`, ended in `tool_failed` ("Native tool
execution could not be completed safely.") with no refusal stored. In
`c091e034` the outline was accepted. Then all 4 section calls returned tool
calls and none reached the ledger, so the controller raised a
`ToolConversationError` before any tool ran. Possible causes, all raised before
the ledger:
- a reply with more than one tool call ("terminal artifact tool must be the
  only requested tool");
- arguments that aren't an object (`_validate_call`);
- a truncated tool call after the allowed continuations.

`ArtifactHandler.run` (`app/generation/artifacts.py`, the
`except (ToolConversationError, ToolLedgerError)` branch) turns the error into
`tool_failed` and logs nothing. The worker logs only the class name
(`app/jobs/worker.py`, `finish_failed`). We can't fix what we can't see.

### F6. Workbook rows: wrong column count, and output too long

Most of the 8 column-count refusals are the model leaving out an empty trailing
cell. The 16 truncated calls are tracker sheets where the model writes every
course of a program as a row (up to `_MAX_WORKBOOK_ROWS` = 200), with an
8,192-token cap. For a program tracker, code already knows those rows: they're
the program's requirement records, the same records the term plan uses.

### R1. Research still answers in chat when it should hand off (2 cases)

- **"Build me a four-year plan for the Economics and Mathematics BA."**
  `_asks_for_artifact` matches it, yet no artifact was made. The one-time
  reminder in `12e97ec` fires only when `corrections_used < 2`, the remaining
  time is above `_SALVAGE_RESERVE_SECONDS`, and other budgets allow it. This
  turn ran 116 s and 9 calls, so a correction or time budget was already spent
  and the reminder never fired.
- **"A semester-by-semester spreadsheet for the Economics and Human Health BA
  with a credit column."** The regex in `_ARTIFACT_ASK`
  (`research_controller.py`) needs a verb ("make", "build", …) or "plan my". A
  request that is only a noun phrase doesn't match.

### R2. Summaries never get the summary purpose (4 cases)

`ResearchArtifact.purpose` allows `create | revise | summary`
(`app/advising/research_types.py`). `_queue_turn_artifact`
(`app/conversations/worker.py`) builds a summary deterministically from saved
turns (`build_advising_summary_spec`). But research's prompt rule names only
"make, keep or export" and "revise the attached artifact"; it never names
summaries. Every "Summarize this conversation…" became `create`, which then
failed in sections (F1). The frozen benchmark has the same request in A20 and
A21.

### R3. A plan for a program that doesn't exist was published (1 case)

"Make me a plan for the Underwater Basket Weaving BS." published *"Integrated
Visual Arts Co-Major (BA, 33-36 Hours) — Weaving/Basketry Focus: Term Plan"*.
Two problems:
- research replaced the requested program with a different real one and made
  the artifact;
- the outline's model-written title made up a "focus" that no record supports.

### R4. Fake-course controls (2 cases; partly the suite's fault)

The generated case "Make a plan for the Mathematics and Computer Science BS
that includes CS 914" names a real program. That's a different case from
frozen A26, "Make a plan that includes BIOL 999", which names none. With a real
program, a plan that leaves CS 914 out is a reasonable result. The frozen K4
also fails any turn text that names the fake code, even "CS 914 isn't a
course". So research has to say the named course isn't in the Course Atlas
without repeating the code.

### S1-S3. Bugs in the suite itself (Claude's script)

- **S1.** `_classify` reports any failure during writing as
  `section_exhausted`. F5's failures need their own outcome.
- **S2.** A revise case whose base case didn't publish can't attach a
  reference (the benchmark attaches one only from `artifact_registry`). It
  measures the base, not revision. All 4 revise cases this run were in that
  state.
- **S3.** `control_fake_course` should mirror A26: no program in the prompt.

## Fixes

Each fix lands with a test that fails first, as a row in an existing table
(AGENTS.md testing policy). Use the shared fakes (`ArtifactStageProvider`,
`FakeProvider`, `FakeClock`), add no new test files, and never assert on
request order, because sections run in parallel. The commit order below is the
order of impact.

### Phase 0: make failures visible (do first; small)

**0.1 Log why a tool conversation stopped.** In `ArtifactHandler.run`'s
`except (ToolConversationError, ToolLedgerError)` branch, log at warning level
the stage key and `str(exc)`. That string is internal controller text such as
"terminal artifact tool must be the only requested tool", never model output or
slot text. The student still sees only the `tool_failed` message.
`_run_stage` knows the stage key; add it to the exception as a note
(`exc.add_note(f"stage {tool_id}:{stage_key}")`) rather than adding a new
exception type.
- *Test:* none. This is logging, and the policy forbids asserting on it.

**0.2 Suite outcomes (Claude, `scripts/manual/artifact_suite.py`).**
- S1: `tool_failed` with no refusals becomes `tool_conversation`. The report
  shows the matching `runN.log` warning line from 0.1.
- S2: a revise case whose base didn't publish becomes `revise_blocked` and
  doesn't count toward the revise pass rate.
- S3: `control_fake_course` uses the A26 phrasing.

### Phase 1: one section schema per kind (fixes F1-F4; most of the failures)

**1.1 Section models per kind.** Replace the single `StageSection` input with
three models, all offered under the same tool name `advisor_stage_section`.
`_run_stage` already takes the input model, and `terminal_repair_model` follows
it:

```python
class StageDocumentSection(ToolInputModel):
    blocks: list[dict[str, Any]]        # each block may carry source_slots
    bindings: list[ElementBinding] = [] # still accepted; merged with block-level slots

class StageHtmlSection(ToolInputModel):
    html: str = Field(min_length=1, max_length=64_000)
    source_slots: list[int] = []        # code builds the one "html" binding

class StageWorkbookSection(ToolInputModel):
    rows: list[SectionRow]
```

- `_write_section` picks the model from `self.kind`. Each one converts into the
  existing internal draft (`StageSection` stays as the internal type, so
  `_assemble` and repair don't change).
- A document section can no longer send `html`, and an HTML section can't send
  `blocks`. The 120 + 41 refusals go away because those fields aren't offered.
  The validators' "uses blocks, not html or rows" branches become unreachable;
  delete them.
- The plan-note path (`StagePlanNote`) is unchanged.

**1.2 Block-level citations (F3).** Before `_BLOCK_ADAPTER` validates a
document block, take `source_slots` off the block and turn it into that block's
binding.
- If `bindings` also names the block, the slots are merged.
- The existing check that every slot belongs to this section still applies:
  `_check_binding_slots` and the "must use this section's own source slots"
  refusal stay.
- `_infer_document_bindings` still fills blocks that have neither.
- The model now cites in the place it already tries to. No citation is made
  up: every slot still comes from the model or from the single-entity text
  match.
- Section prompt: "Cite on each block: `source_slots` lists the numbered slots
  it uses."

**1.3 HTML binding built by code (F2).** `StageHtmlSection.source_slots`
becomes the single binding `{"element_id": "html", "source_slots": …}`.
- In grounded mode, an empty `source_slots` is refused with "list the slots
  this page uses in source_slots", the one rule the model can follow.
- The three binding-id refusals go away.

**1.4 A null for an unused string becomes empty (F1, as a backstop).** In
`repair_arguments`, model branch: when a field's annotation is plain `str`
(not nullable), its default is `""`, and the value is `None`, send `""`.
- This extends the existing empty-list repair and its comment to strings. It
  invents nothing: null and `""` both mean "not used".
- A required string (no default) keeps its null and still fails.
- *Test:* add `(str field with default "", None) -> ""` and `(required str,
  None) -> None` rows near
  `test_an_empty_array_sent_as_another_empty_value_is_repaired` and
  `test_null_stays_null_for_a_field_that_allows_it` in
  `tests/test_tool_controller.py`.

**1.5 Numbers in table cells (F4).** In the document-section conversion
before block validation, write `int` and `float` cells of a `table` block as
their exact decimal text (`4 -> "4"`, `3.5 -> "3.5"`).
- `bool`, objects and lists still fail.
- This applies to `table` cells only, not paragraphs.

**Tests for 1.1-1.5** (`tests/test_native_artifact_tools.py`, and
`tests/test_artifact_generation.py` with `ArtifactStageProvider`):

| Row | Expected |
|---|---|
| a document section with `html: null` | accepted (1.1/1.4) |
| an HTML section with `source_slots` and no bindings | accepted; one `html` binding |
| an HTML section with no `source_slots` in grounded mode | refused with the 1.3 message |
| a document block carrying `source_slots` | accepted; binding equals those slots |
| a block citing a slot outside the section | still refused |
| a table cell `4` | rendered `"4"` |
| a table cell `true` | still refused |

Existing tests that script `StageSection(blocks=…, bindings=…)` payloads keep
working: `bindings` is still accepted. Tests that script `html` must move to
the HTML shape.

### Phase 2: research handoff (R1-R4)

**2.1 The handoff can't be lost (R1).** When `artifact_requested` is set, the
accepted decision has no artifact, and the reminder can't fire because a budget
is spent, code fills in the artifact instead of answering in chat:
- `kind` comes from the request: "spreadsheet", "sheet", "workbook", "excel"
  or "column" → workbook; "web page", "webpage", "html" or "interactive" →
  html; otherwise document;
- `title` is the student's request, trimmed to 200 characters;
- `brief` is the student's request.

The artifact run gets the brief plus the published answer, as it does today, so
nothing is invented. The reminder stays first while budgets allow, because the
model writes a better brief. The fallback must not apply when the decision is
`declined` or `insufficient_evidence`, or when R3's check refused the program.
**Needs your approval:** it reverses "a second finish without an artifact
publishes as is" from the last handoff.
- *Test:* rows in `tests/test_research_types.py` for the kind-inference table,
  and one research-loop row (`FakeProvider`) where corrections are spent and
  the turn still hands off.

**2.2 Noun-phrase requests (R1).** Extend `_ARTIFACT_ASK` with requests that
open on the thing itself: `^(?:an?|my|one)\s+[^.?!]{0,60}\b(?:plan|checklist|
spreadsheet|workbook|sheet|page|handout|roadmap)s?\b`. Also add "roadmap" and
"semester-by-semester" to the noun list.
- *Test:* add the suite prompt above and "A one-page roadmap of the Applied
  Mathematics minor" to the existing `_asks_for_artifact` table, plus negative
  rows: "A question about the plan", "My plan is to take CS 170".

**2.3 Summaries (R2).** Add `_SUMMARY_ASK`:
`\bsummar(?:y|ize|ise)\b[^.?!]{0,40}\b(?:chat|conversation|this|our)\b`.
When it matches and research hands off a `document` artifact with purpose
`create` and no `revises`, code sets the purpose to `summary`. The summary is
then built from saved turns by `build_advising_summary_spec`, with no stage
calls at all. This is deterministic, needs no correction round, and avoids F1
entirely for summaries.
- Prompt: add "or summarize this conversation (purpose summary)" to the
  existing artifact rule. Keep `research-NN` within the prompt-size tests
  (`test_context_compaction_preserves_exact_passages_*`,
  `test_finish_request_retains_declared_candidate_program_evidence`); bump the
  prompt version.
- *Test:* `_SUMMARY_ASK` rows in the same table, and one row checking that a
  `create` document becomes `summary` on a summary request and stays `create`
  otherwise.

**2.4 A missing program makes no artifact (R3).** In research finish
validation (`research_final.py`, next to the `revise` check): if the decision
carries an artifact and the question names a degree program, that name must
match a program the turn listed or read. A named degree program is a phrase
ending in BS, BA, BBA, minor, major or co-major. The match uses the same
normalized title comparison as `advisor_list_programs`. If nothing matches,
reject once with "the program the student named is not in the catalog; say so,
name the closest programs, and make no artifact". After that one rejection, a
second finish that still carries an artifact gets the artifact removed, and the
answer publishes.
- *Test:* rows in `tests/test_research_data.py` for a matched name, a fake
  name, and a question that names no program.

**2.5 Plan and checklist titles come from the program record (R3).** When the
outline resolves a program (the plan path), code writes the title as
`"{program title}: {Term plan | Requirements checklist}"` and ignores the
model's title. A made-up "focus" can't reach the page.
- *Test:* one row in `tests/test_artifact_generation.py`.

**2.6 Don't repeat a code that isn't a course (R4).** In finish validation,
look up every course code in the answer text in the course master (including
retired fallbacks). A code with no record is rejected once with "the named
course is not in the Course Atlas; say so without repeating its code". This
uses the lookup behind `retired_course_problems`.
- *Test:* one row next to the retired-course rows.
- **Decision for you:** K4 (frozen) treats even "CS 914 doesn't exist" as
  naming it. If you'd rather allow a reply that says a named code doesn't
  exist, that's a K4 change and needs your written approval. 2.6 is what the
  current bar requires.

### Phase 3: workbooks (F6; measure after Phases 1-2 first)

**3.1 Column guidance.** The section payload already lists `columns`. Add
"each row has exactly N values in this order: key1, key2, …; use "" for an
empty cell" to the workbook section prompt. Also add the column keys to the
refusal text.
- *Test:* the existing column-count refusal row checks that the keys appear.

**3.2 Course-list sheets written by code.** When a workbook sheet tracks a
program's courses (the outline resolved a program, and the sheet has a course
or code column), code writes one row per program requirement record, using the
plan path's `plan_records` (code to record slot) as evidence: code, title and credits
from the record, and status left empty. The model writes only notes rows, as
`StagePlanNote` does for terms. This removes the 200-row model writes behind
the 16 truncated calls. Do this only if truncation still shows up in the Phase 4
run.
- *Test:* one `ArtifactStageProvider` row that a tracker sheet's rows are the
  program's courses with their records as citations.

### Phase 4: verification (Claude)

1. Offline gates: `ruff`, full `pytest`, frontend `typecheck`, `format:check`
   and `npm test`.
2. Live suite, **same seed 11**, one repeat. Compare the outcome table, not
   only the pass count.
3. Live suite, **new seed** (for example 23), one repeat. A fix that passes 11
   but fails 23 is tuned to the sample.
4. The frozen benchmark (`artifact_benchmark`, 30 + 40 cases), since that is
   P3.2's bar.

**What counts as fixed.** These targets are for this round, not P3.2:
- no `html` field refusals;
- section-stage outcomes at or below 5 of 48;
- no `tool_conversation` outcome without a logged cause;
- every control family passing;
- summary at least 3/4;
- html_page at least 2/4;
- no published artifact for a program the catalog doesn't have.

## Rules for the implementer

- Never edit `scripts/manual/artifact_benchmark_cases.jsonl`, any K-check, or
  `app/artifacts/checks.py` acceptance.
- No model writes, infers or reorders a prerequisite; term order comes from
  `advising.sequence.plan_sequence`.
- Generation stays greedy.
- Evidence must name what it supports: never bind a block to a slot that
  doesn't mention its content. 1.2 moves the model's own citations; it doesn't
  add any.
- Code-filled fields (2.1, 2.3, 2.5, 3.2) come from the student's words or from
  records, never from model guesses.
- One commit per numbered fix, in phase order. Don't push. Don't run the live
  benchmark or the suite; Claude runs Phase 4.

## Open decisions

1. **2.1:** code fills in the handoff when research drops it. Recommended: yes.
2. **2.6 / K4:** keep K4 as is, so research must not repeat a fake code.
   Recommended: keep.
3. **Meeting-time filter in `checks.py`** (from an earlier round, not
   approved): keep or revert.

## Prompt for the implementing model

> You are working in `C:\Users\moomi\personal_projects\Emory_STEM_Advisor`.
> Read `AGENTS.md`, then `docs/plans/artifact-reprompting.md`, then
> `docs/plans/artifact-suite-fixes.md`. The last one lists what the live
> artifact suite found, with the exact refusals, the root cause in code, and a
> fix design for each.
>
> Implement Phase 0 item 0.1, then Phase 1 (1.1-1.5), then Phase 2 (2.2, 2.3,
> 2.5, and 2.1, 2.4, 2.6 only if the plan's Open decisions mark them approved),
> one commit per numbered item. Skip 0.2 (Claude's script) and Phase 3 unless
> told otherwise. For each fix, first add the listed row to the existing test
> table so it fails, then make it pass. Use the shared fakes in
> `backend/tests/fakes.py`; add no new test files and no per-file providers.
> Sections run in parallel, so never assert on request order.
>
> Follow the plan's "Rules for the implementer" exactly. Gates before each
> commit, from `backend/`: `../.venv/Scripts/python -m ruff check .` and the
> touched test files. Before the last commit: the full
> `../.venv/Scripts/python -m pytest`, and from `frontend/` `npm run typecheck`
> and `npm test`. Don't push. Finish with a short list of what changed and
> anything you could not do.
