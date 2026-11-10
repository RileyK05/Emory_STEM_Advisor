# Artifact test suite

Written 2026-11-09. The frozen benchmark (`scripts.manual.artifact_benchmark`,
30 hand-written artifact cases) is P3.2's bar and stays unchanged. Thirty fixed
prompts are too few to trust on their own: a fix can be tuned to them, one
lucky run can pass a case that fails half the time, and a failed case says that
it failed but not where. `scripts.manual.artifact_suite` is the wider net.

## What it does

1. **Generates cases from the development database** (`generate --seed N`).
   Every program, course and Oxford pairing in a prompt is real. A new seed
   draws a new sample, so passing one sample is not the same as passing the
   suite. Programs default to STEM departments; `--all-programs` adds the rest.
2. **Runs them through the unchanged benchmark runner**, repeated
   (`run --repeat R`). Generated cases use the benchmark's case format and its
   K-checks, so a pass means the same thing in both.
3. **Diagnoses every case from the database**: the research turn's outcome,
   whether it asked for an artifact, the artifact run's stages, every stage
   refusal the model got, calls, tokens, truncated calls, seconds, and where
   the case stopped.
4. **Reports** (`report --dir`): pass rate per family with a 95% Wilson
   interval, failed K-checks, where cases stopped, flaky and never-passing
   cases, p50/p95 time and cost per kind, quality measures the K-checks do not
   gate, and the most common stage refusals.

## Families

| Family | Example | Must |
|---|---|---|
| `plan_fresh` | "Plan out my Psychology BS semester by semester." | publish a document plan (K4-K8) |
| `plan_completed` | "I've done CS 170 and MATH 111. Make a plan for the rest of the Mathematics and Computer Science BS." | leave the completed courses out |
| `plan_oxford` | "I took BIOL_OX 120 and BIOL_OX 141 at Oxford. Plan my Biology BA from here." | honour the campus pairing |
| `checklist` | "Give me a requirements checklist for the Physics and Astronomy BS." | publish a document checklist |
| `workbook_tracker` | "Make a spreadsheet that tracks my Chemistry BS credits, with totals." | publish a workbook; totals are formulas when asked |
| `workbook_plan` | "A semester-by-semester spreadsheet for the Psychology BS with a credit column." | publish a workbook plan |
| `compare` | "Compare the Biophysics BS and the Engineering Sciences BS in a document I can keep." | publish a comparison |
| `html_page` | "Make a one-page web page roadmap of the Applied Mathematics minor." | publish an inert page |
| `revise` | an earlier case's request, then "Add a section for electives." | make a new revision, parent unchanged |
| `summary` | two questions, then "Summarize this conversation into a document I can keep." | publish a summary |
| `control_factual` | "What does MATH 111 require?" | answer, make no artifact |
| `control_fake_course` | "Make a plan for the Mathematics and Computer Science BS that includes CS 914." | never present CS 914 as a course |
| `control_fake_program` | "Make me a plan for the Underwater Basket Weaving BS." | make no artifact |

"Completed" courses are first-year courses: below 300 with no parsed
prerequisite. Fake course codes are checked against the course master.

## Where a case stops

`report` sorts every case run into one outcome:

| Outcome | Meaning | Usually fixed in |
|---|---|---|
| `published` | the run published; the K-checks grade it | the pipeline's checks, if K4-K10 fail |
| `no_handoff` | research answered in chat and asked for no artifact | research prompt / finish validation |
| `outline_exhausted` | the outline stage ran out of corrections | `native_tools.py` outline rules |
| `section_exhausted` | a section ran out of corrections | section rules, code-built content |
| `checks_failed` | assembly checks still failed after repair rounds | repair prompts, check attribution |
| `deadline` | the run hit `ARTIFACT_OPERATION_TIMEOUT_SECONDS` | parallelism, fewer calls |
| `provider` | the model connection failed | provider settings |
| `turn_failed` | the research turn itself failed | research |
| `control_ok` / `control_made_artifact` | a control case made no artifact / made one | research |

The refusal table names the exact rule the model keeps breaking. That is the
first thing to read after a run.

## Quality measures (reported, not gated)

- **Program coverage**: the share of a program's listed courses a plan or
  checklist names. K5 checks that named courses are on the program; coverage
  catches a checklist that lists three of twenty requirements.
- **Heaviest term**: the most courses scheduled in one term.
- **Completed courses scheduled**: plans that schedule a course the student
  said they took.
- **Fallback sections**: artifacts where code wrote a term because the model
  could not.

These stay out of the pass/fail bar until the user decides thresholds.

## How to use it

```bash
cd backend
python -m scripts.manual.artifact_suite generate --seed 11 --per-family 4 --out ../data/manual-checks/suite11.jsonl
python -m scripts.manual.artifact_suite run --cases ../data/manual-checks/suite11.jsonl --repeat 3 --out-dir ../data/manual-checks/suite11
python -m scripts.manual.artifact_suite report --dir ../data/manual-checks/suite11
```

- **After a pipeline fix**: rerun the same seed, so before and after are the
  same cases. Compare the outcome table, not only the pass count.
- **Before calling P3.2 done**: also run a new seed. Passing one seed while
  failing a fresh one means the fix is tuned to the sample.
- `--per-family 4` gives 48 cases. A case takes one to four minutes, so one
  repeat is about two hours and three repeats about six. Each case runs in its
  own benchmark process (a revise case with the case it revises), so a stopped
  run loses only the case in progress: `run --resume` with the same arguments
  first closes the jobs the stopped run left open (waiting out a lease of up to
  10 minutes), then continues with the next unfinished case. Run it alone: the
  benchmark's queue check (K1) and its worker loop assume no other jobs are
  running.
- Generated case files and run output go under `data/manual-checks/`
  (gitignored), never in the repo.

## Offline coverage it relies on

The live suite finds failures; each fix still lands with an offline row that
fails first (AGENTS.md testing policy): `tests/test_artifact_generation.py` and
`tests/test_native_artifact_tools.py` with `ArtifactStageProvider`, and the
research handoff in `tests/test_research_types.py`. Sections run in parallel, so
offline tests never assert on request order.
