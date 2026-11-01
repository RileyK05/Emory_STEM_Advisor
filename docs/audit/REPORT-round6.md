# Round 6 — the campus equivalence (2026-10-31)

## What was asked

The database should state that `XXX_OX` is equivalent to `XXX`. That is a
statement about a course's identity across two campuses, and the schema had
nowhere to put it: migration 078 deliberately stored no Atlanta/Oxford
equivalence at all.

## What the data said first

The master holds 4,520 current courses, 185 of them Oxford (`_OX`). The Atlas
capture holds the same 185, so nothing was missing from the master on the
Oxford side. The gap was elsewhere: 406 unresolved prerequisite mentions and
21 unresolved program slots named an `_OX` code, and the requirement texts
that name those codes state the equivalence in Emory's own words:

```
BIOL 142 or BIOL_OX 142 or BIOL_OX 142Q or BIOL_OX 142X or BIOL 240
FILM 101 or FILM_OX 101 or equivalent transfer credit
(ECON 220 or ECON_OX 220 or MATH 207 or MATH_OX 207 or MATH 361 or MATH 362)
```

A requirement that offers an Atlanta course and an Oxford course in one
alternative list is Emory saying the two are the two campuses' offering of one
course.

The obvious shortcut — same subject and number means the same course — is
wrong, and the data says so plainly:

| Oxford | Atlanta | |
| --- | --- | --- |
| `PE_OX 101` | `PE 101` | "Weight Training/Conditioning" vs "Personal Health" |
| `PE_OX 126` | `PE 126` | "Beginning Golf" vs "PPF/Cardio Tennis" |
| `PE_OX 151` | `PE 151` | "Dance Improvisation" vs "Beginning Scuba Diving" |
| `SPAN_OX 318W` | `SPAN 318W` | "Advanced Writing in Spanish" vs "Critical Writing in Hispanic Topics" |

94 pairs share a subject and a number; 20 of them are different courses. The
number alone proves nothing, so the title has to agree as well.

## What was built

**Migration 079** adds `course_campus_equivalents`: one row per pair, with the
basis on which it was established. Both bases are the Atlas's own statements.

- `catalog_requirement` (101 pairs) — a requirement offers the Atlanta course
  and the Oxford course in one alternative list. A code whose number carries a
  topic suffix the Atlanta course does not carry is that course's variant, so
  `BIOL_OX 142Q` is `BIOL 142`. A code whose own number is an Atlanta course's
  number exactly is that course: `MATH_OX 112Z` is `MATH 112Z`, not a variant
  of `MATH 112`.
- `course_record` (48 pairs) — two records with the same course number and
  compatible titles are the same course at the two campuses. The campuses
  abbreviate each other's titles ("Concepts And Methods in Biol Anth" and
  "Concepts & Methods in Biological Anthropology"), so one title's content
  words must be contained in the other's and the titles must open with the
  same word. This basis also learns the subject renames the campuses use —
  Oxford writes `ANTH` where Atlanta writes `ANT`, `ASTR` for `PHYS`,
  `DASCI` for `DATASCI` — which the catalog rule then uses to disambiguate a
  clause that offers the same number under two subjects.

The derivation (`app/atlas/equivalence.py`) is pure and reads no model. It
runs on every Atlas import and every reparse, next to the cross-list identity
it deliberately does not share.

**It is not `courses.canonical_code`.** That column is the retrieval
deduplication key. Two campuses sharing it would delete one campus's passage
from the candidate set, relabel the survivor as "BIOL 142 / BIOL_OX 142", blind
the answer's course-identity check to the difference between the campuses, and
leave the research search unable to open the Oxford record at all. A campus
equivalence is a statement about satisfying a requirement, not about being the
same passage. A test asserts the two keep their own anchor.

**What uses it.**

| Where | What changed |
| --- | --- |
| `prereq_mentions` | A mention that names an Oxford course the master has no row for resolves to the Atlanta course it stands for, keeping its own text code. |
| `program_course_mentions` | The same for a program slot. |
| The prerequisite graph | `prereq_upstream` and `prereq_downstream` traverse either campus's code to the same course. |
| The sequence planner | A completed campus equivalent satisfies the requirement: a student who took `MATH_OX 111` at Oxford has taken what a `MATH 111` requirement asks for. A selected campus equivalent satisfies a requirement that names the other campus's course, and the plan reports the student's own course. The equivalence changes what is *satisfied*, never what is *planned*. |
| The research identity check | The collection's known courses now include the Oxford codes the catalog publishes, so an answer that names `BIOL_OX 142` is naming a real course, not one that does not exist. A course nothing publishes is still rejected. |

## What it changed in the corpus

| | Before | After |
| --- | --- | --- |
| Unresolved prerequisite mentions | 716 | **361** |
| …of which name an `_OX` code | 406 | **51** |
| Unresolved program slots | 556 | **548** |
| …of which name an `_OX` code | 21 | **13** |

355 mentions now resolve, including the subject renames (`ANTH_OX 205` →
`ANT 205`, `QTM_OX 100` → `DATASCI 100`) and the topic variants (`BIOL_OX
142Q` → `BIOL 142`).

The 51 that remain name an Oxford course whose Atlanta counterpart does not
exist in the master: `MATH_OX 117` (MATH 117 is not a current course),
`FREN_OX 310`, `ACT_OX 210`, `PE_OX 102`–`PE_OX 160`, `ENGCW_OX 270`. These are
not equivalence failures; they are requirements that name a course Emory's
current catalog does not list, and they are now visibly so rather than
silently unresolved.

## The tools path, measured for the first time

The clarity probe had always measured the legacy path *without* the advisor's
scoped tools, because the development `.env` turns research on and "legacy"
would otherwise measure research. The tools are the product's real
configuration. Measured against the live model on the development corpus:

| Configuration | Checks | Probes fully passed |
| --- | --- | --- |
| Legacy, no tools (the baseline the probe had been measuring) | 126/126 | 19/19 |
| Legacy, tools on, before the fixes below | 116/122 | 15/19 |
| Legacy, tools on, after the fixes below (two runs) | 134/135 and 121/126 | 18/19 and 16/19 |

Three probes ended in an HTTP 422 in the middle state, and the model output is
stochastic, so a single run is not a score. The failures were not random:
every one of them had a cause in the code. What the fixes removed is the hard
failure: **no probe in either later run ended in a 422**, and the ones that
still fail fail on content — a phrase the model did not use, a course the model
named without citing it, a statement the model should not have made.

### A spent tool budget killed the request

`ToolConversationController` raised `ToolCallLimitExceeded` when the model
asked for more tool calls than the operation allowed, and the query service
turned that into a 422 — after the retrieval had already found the passages
and four tool calls had already run. The model was told nothing and the student
got an error.

The fence still holds: none of the calls run. But a fence is not a failure.
The call is now answered with a refusal that says the budget is spent, and the
model is asked to answer from the numbered context and the tool results it
already has. A required deterministic tool that did not run is still required,
so an operation that needed one fails rather than publishing an answer without
it.

### The durable ledger's spent budget killed the request the same way

The ledger fences the tool budget durably, per operation. A routing fallback
re-runs the generation on the *same* operation, so the second attempt finds the
budget already spent: the controller's in-memory count says zero, the ledger's
durable count says four, and the claim fails with an opaque `ToolLedgerError` —
a 422 again. The ledger now reports that case as its own error, and the
controller answers it the same way: the fence holds and the operation still
answers.

### The tools path needs a bigger provider budget than a plain answer

A tools conversation spends provider calls on tool turns, and a reply that ran
out of output allowance needs one more call to be asked again with a larger
allowance. With four calls the tool turns left nothing for that retry, so a
long answer was cut off and withheld. One operation call is now reserved for
the retry, and the measurement is in the table above: the tools path went from
116/122 to 134/135 on the same probes.

This is a configuration fact, now written down in `.env.example` and
`docs/architecture.md`: the tools path needs `OPERATION_MAX_CALLS` of five, and
the shipped default of two fits the plain path, where the tools are off. The
development `.env` was raised from four to six, and the measured run above is
with that budget.

### The echo guard withheld correct answers

The prompt-leak guard compared eight-word stretches and fired on a single
shared stretch. A careful answer often quotes the advisor's own caveat — "A
fact absent from the supplied passages is unknown here" — and that is the
instructions, but an answer that says it is answering the question, not
leaking the prompt. Three such answers were withheld, deterministically.

The measure is now how much of the prompt the answer reproduces: eight
eight-word stretches, counted rather than shared, so a short quotation does not
look like an echo and a long echo does not look like a quotation. All four
round-4 leak cases still fail — the obfuscated prompt, the prompt with
citations, the prompt with underscores, and the base64-encoded prompt.

### The probe read the corpus with a different reader than the product

The probe's `course-code-exists` check compared the answer's codes with the
cited passages as literal text. A passage that writes "171Z" after "CS 171"
names CS 171Z, and the answer that repeated "CS 171Z" was flagged as a course
that exists nowhere — including in the passage that named it. The probe now
reads both sides with the shared reader the product's identity gate uses. The
master is right that no course "CS 171Z" exists: 171Z is a registration
section of CS 171, and the Atlas lists no such course.

### Two campus codes of one course are one choice

The planner's own comparison of the requirement's alternatives read "MATH 111
or MATH_OX 111" as two alternatives, so a student who selected either one was
blocked with `multiple_alternatives_selected`. The two codes are the same
course at two campuses, and the planner now collapses the alternatives that name
one course.

## Verification

- 26 new tests in `tests/test_campus_equivalence.py`: the catalog basis, the
  record basis, a course the master lacks, the topic variant, the variant that
  is its own course, the number-alone trap, the subject rename, the both-
  directions lookup, the rows the import writes, the mention that resolves, the
  equivalence that is not the retrieval key, the planner honouring it in both
  directions, the plan staying untouched, the research path knowing a published
  course, and the graph reaching either campus's code.
- `tests/test_tool_controller.py`: a model that asks for more tools than the
  budget is told and can still answer; a spent durable budget is refused the
  same way; a required tool that did not run still fails the operation.
- `tests/test_output_truncation.py`: the tools path reserves an operation call
  for the answer's own retry.
- `tests/test_generation.py`: an answer that quotes one of the advisor's own
  caveats is not an instruction echo.
- `tests/test_research_types.py`'s preparation-check fake now answers a query;
  the check reads the collection's campus equivalences.
- `tests/test_provider_retry.py`'s clock patched the global `time.sleep`, so a
  thread still running in the session landed its sleeps in the retry's record.
  It patches the controller's `time` module instead, with only `sleep`
  replaced.
- The full backend suite, ruff over `app/ tests/ scripts/`, and
  `scripts.manual.data_audit --collection default` (0 integrity problems, 0
  gold mismatches) are green.
- The legacy path's live probe against the configured provider on the
  development corpus: **126/126 checks, 19/19 probes fully passed**. The
  legacy path reads the equivalence through the `sequence` tool, so this
  confirms the new table does not disturb the answering path.

## A performance defect the change introduced

The first form of the traversal put the counterpart in the join condition as a
correlated subquery:

```sql
AND (target.code = edge.target_code
     OR target.code IN (SELECT e.atlanta_code FROM course_campus_equivalents e WHERE ...))
```

It is correct, and it is eighty times slower than the plain join: the `OR`
stops the planner using the index on `courses.code`. Measured over 300 courses
on the development corpus:

| Form | Upstream | Downstream |
| --- | --- | --- |
| Plain join, no equivalence | 0.32 ms per course | 0.51 ms |
| Correlated subquery in the join | 557 ms per course | — |
| Lateral over the codes | **0.49 ms** | **0.58 ms** |

The lateral yields the edge's own code and its counterpart before the join, so
the index on `courses.code` is still used and no course is lost: the
traversal gains 19 upstream chunks and 22 downstream chunks against the plain
join, and loses none.

## Still open

- The research path's failure response (HTTP 422 versus a graceful refusal) is
  unchanged and still awaiting a decision.
- The 48 eval cases remain `pending_human_review`, and the gold set's 51
  entries remain unreviewed by a person.
- A program slot that names an Oxford course only the *program* requirement
  text names (`DANC_OX 300R`, `GEOL_OX 220N`) is still unresolved: the
  derivation reads the Atlas's course requirement texts, and the program
  requirement texts are a separate corpus with a separate importer.
