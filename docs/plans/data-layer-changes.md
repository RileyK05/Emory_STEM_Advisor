# Data layer change log

Every change, addition and removal made during the data layer push, newest last.
Goal set by the user on 2026-11-06: get the data layer to a production-worthy
state and keep all gates. Scaling is handed off.

Nothing here is committed unless an entry says so.

## Already done before this push

- P1.6 (see [production-readiness-progress.md](production-readiness-progress.md)):
  added `advisor_audit_program` and `advisor_suggest_courses`; removed saved
  audits, comparisons and academic records; migration 081 drops their tables.
- Team snapshot: `scripts/team_snapshot.py`, `create_backup(shared_tables=, extras=)`,
  `EvalSummary.missed`.

## Retrieval (this push)

Measured on the 40-question eval: fused recall 0.5875 → 0.90; all expected passages
in the top 6: 22 → 36 of 40; in the top 24: 31 → 37.

- **Added** migration 082: `chunks.search_hidden`. The keyword and embedding seams
  skip a hidden chunk; it stays readable and citable.
- **Added** `repeated_term_cards` and `refresh_term_card_search` in
  `app/atlas/offerings.py`. Each course had up to 13 near-identical term result cards
  that filled the candidate pool. A card now leaves search when the course's master
  record already says the same thing, when a newer term's card does, or when the
  course is retired. Topic titles and section-specific text stay searchable, in the
  newest term. Offering publication and Atlas imports run it.
- **Added** `scripts/refresh_term_cards.py`: run once after migration 082 on any
  database that already holds offerings. Done on the dev database: 11,913 cards hidden.
- **Changed** Emory's 999-numbered placeholder courses (`RES 999GNUR` and the like)
  leave search. They are registration devices.
- **Changed** `app/db/queries/keyword.sql`: a program title matches without its degree
  suffix ("chemistry major" now matches "CHEMISTRY (BA)"), a course's title counts as
  part of its title vector, a question that names a course's full title ranks that
  course with the code-heading band, and subject plus title together rank above
  either alone.
- **Changed** `app/retrieval/seams/keyword.py`: `PROGRAM_NICKNAMES` (math, econ, chem,
  bio, cs, ai, envs, nbb, qss, psych, stats, polisci, wgss), query side only; words
  like "requirements" and "major" no longer win the longest-word bonus; two query
  words in a title now count as naming it, without having to be adjacent; "class",
  "classes" and "emory" are scaffolding words.
- **Changed** `advisor_search` program lookup uses the same nicknames.
- **Added** `scripts/manual/retrieval_census.py`: asks about every program and a
  course sample, to catch overfitting to the eval set.

## Retrieval check beyond the eval set

`scripts/manual/retrieval_census.py` on questions never tuned against: 153 of 155
programs in the top 6 and all 155 in the top 24; 270 of 300 sampled undergraduate
courses in the top 6. The course misses are mostly titles shared by hundreds of
courses ("honors", "directed study"), which need a code to answer.

## Prerequisite parser (versions 22 to 24, no model involved)

Measured on real requisite text: usable by the planner 1,107 → 1,324 of 1,479 courses;
blocked courses in the core STEM subjects 25 → 8; no course that parsed before stopped
parsing.

- **Added** condition forms: counted courses ("Two PHIL courses", "One 300 level SPAN
  course"), another person's approval, placement, exam scores, student groups
  ("SPECSTUBUS students"), and transfer placeholders ("QTM_OX ELEC"). The planner
  reports these as unresolved conditions rather than blocking the course as unreadable.
- **Added** repairs for registrar typing with one reading: `ECON _OX 100`, `CHEM_X`,
  `PSYC_OC`, `ENVS?POLS`, `112Z0]`, `studentsor`, `transfer/test`, `as prereq`,
  `This course mandates`, a lost opening parenthesis.
- **Added** grouping: "FREN 310 or FREN_OX 310 and FREN 314 or FREN_OX 314" reads as
  two courses, each at either campus. A capital AND between lowercase ors joins groups.
- **Changed** a description after the requirement sentence is dropped, unless it names
  a course or says who may enroll. A restriction is never dropped.
- **Changed, kept by your decision on 2026-11-06:** "A as co-requisite or B as
  prerequisite" used to fail closed. It now reads as one concurrent alternative. Either
  course satisfies it, and a course taken earlier meets a concurrent requirement. The
  cost is that B alone taken in the same term would pass. It unblocks `BIOL 141L`, among
  others.
- **Changed** "Cross-listed with X" is no longer read as naming a requirement. This
  removed the last prerequisite cycle in the graph (`EPI 542` and `GH 562`).
- **Added** `Russian` and `Tibetan` to `SUBJECT_ALIASES`. These are subject names.
- **Changed by your decision on 2026-11-06:** the gold entries for `BIOL 141L` and
  `PHYS 212` now expect `parsed`, with trees written from the raw text rather than copied
  from the parser. They stay `reviewed = false`. The audit shows 0 mismatches.

## Program mentions

Program slots with a resolved course: unresolved 516 → 396. Course numbers a line
states that no mention captured: 261 → 112.

- **Added** `home_subject` and `bare_course_mentions` in `app/programs/importer.py`: a
  bare number on a program line ("209 Perception and Action" in Psychology) becomes a
  course only when the program's leading subject really has that course. Round numbers
  are treated as levels. A program with no leading subject gets nothing.
- **Added** `resolve_program_mentions_by_suffix_variant`: "PHYS 499" resolves to
  `PHYS 499R` when that is the only current course of that number.
- **Changed** `research_programs_naming_courses` matches through the resolved course, so
  "which majors need X" sees those lines.

## Checked and left alone

- Mathematics (BS) "85 hours, 45-51 courses" is what the catalog PDF prints, not a
  parsing error. French Studies and Dance look odd for the same reason. These are the
  catalog's own numbers, and the requirement lines are correct.

## "Which majors need X"

- **Added** `program_lines` on `advisor_read_courses`, only when the question is about a
  major, minor, program or degree: each program's actual requirement line for the
  course, capped at 30 lines of 160 characters. Before this the model read up to a
  dozen programs to find out what the database already knew.
- **Changed** the matching prompt rule. Prompt version `research-33`, tools version 16.

## New sources (Emory only, staged in `data/staging/advising-gaps-2026-11-06/`)

Seven documents the corpus had no answer for, ingested through
`scripts.ingest --origin-manifest` as `institutional_publication`:

| File | Answers | Source |
| --- | --- | --- |
| `emory-cost-of-attendance-2026-27.md` | Cost of a year | Office of Financial Aid worksheet |
| `emory-tuition-fees-2026-27.md` | Tuition and fees | 2026-27 tuition and fee schedule, pages 1-3 |
| `emory-advantage-plus.md` | Tuition-free attendance | Office of Financial Aid |
| `emory-course-registration-guide.md` | Registration, add/drop/swap | Office for Undergraduate Education |
| `emory-honors-in-course.md` | Latin honors and GPA cutoffs | Office for Undergraduate Education |
| `emory-sire-undergraduate-research.md` | Starting undergraduate research | Undergraduate Research Programs |
| `emory-waitlists.md` | How OPUS waitlists work | University Registrar, a staff guide last updated 2023 |

Left out on purpose: the Dean's List. The only Emory text I found is an older page that
no longer serves it, and a hole beats poor data.

## Bug found and fixed

- **Fixed** `app/ingest/extract.py`: a Markdown heading with no title ("###") produced a
  locator with an empty label, and the citation contract then failed the whole search
  for any question that reached that chunk. Such a line no longer names a section.

## "Which majors need X", second pass

The model kept reading programs even with the lines in hand, because it could only cite
what it had opened, and one answer claimed a single major "definitely" required
`MATH 221`. On a question that asks which programs need a course:

- **Added** each line's source passage opens with the course read, so a line can be
  cited directly, and the course's term listings stay out of that read.
- **Added** `lines_answer_question`: once the lines are in hand the next step is the
  final answer, so the model cannot wander. The compact line list stays in the prompt.
- **Changed** program lookups for a course include its other-campus code. The model
  often reads `MATH_OX 221`, which no program names.
- **Result**, live: `MATH 221` from 160-190 s to 50 s, `CHEM 202` from 115-225 s to
  41 s, `CS 170` from 145 s to 32 s, with the real line quoted for each program.

## Where the data layer stands (2026-11-06)

| Measure | Before | Now |
| --- | --- | --- |
| Eval fused recall, 40 questions | 0.5875 | 0.90 |
| All expected passages in the top 6 | 22 of 40 | 36 of 40 |
| Programs found, never-tuned census of 155 | not measured | 153 in the top 6, 155 in the top 24 |
| Courses found, census sample of 300 | not measured | 270 in the top 6 |
| Courses usable by the planner | 1,107 of 1,479 | 1,324 of 1,479 |
| Blocked courses in core STEM subjects | 25 | 8, 7 of them Oxford wordings |
| Program course numbers no mention captured | 261 | 112 |
| Prerequisite cycles | 1 | 0 |
| Integrity problems in `data_audit` | 0 | 0 |

These numbers measure finding the right text. Answers are graded separately below. The
eval set has not been reviewed by you yet.

**Answers, graded by `scripts.manual.advising_clarity_probe`** (mechanical ground truth,
no human judgement, live model): 145 of 147 checks and 18 of 19 probes on the final run.
The check counts vary a little between runs because many checks come from the answer
itself. The one miss is `phantom-cs495`, a course that does not exist. That answer was
correct: it says the code does not exist and is not in the requirements. The probe counts
the real neighbouring course `CS 495A` against it. I tightened the wording so answers open
with "There is no", and kept the real neighbour because it helps students. The probe is
unchanged.

**Final full run on the final code: 154 of 155 checks, 18 of 19 probes.** With your
approval on 2026-11-06, the fictional-code check no longer treats a longer real code such as
`CS 495A` as the fictional `CS 495`, and `phantom-cs495` now passes. The one failure in the
final run is `policy-su-cap`, and that answer is also correct: it says "20 of your total
graduation credit hours", where the probe's pattern wants "20 credit hours" side by side. It
passed on four of the last six full runs. I did not rerun until it came up clean, and I left that check alone because
you approved changing only the fictional-code check.

## Needs you

1. **Upload the snapshot** somewhere private. It is too large for git, and the repo is
   public.
2. **Publish reviewed requirement rules.** None exist, so `advisor_audit_program` only
   matches lines. The answers are useful but it is not a degree audit. I chose not to
   pre-fill rule trees from course mentions: the architecture says mentions never decide
   a clause's meaning, and pre-filled trees would invite approval without reading.
3. **Ask Emory about graduation credits.** The catalog gives 127 and the Office for
   Undergraduate Education page gives 126. Answers now disclose both. Remove the entry
   from `configs/source_conflicts.toml` once Emory settles it.
4. **Math BS "85 hours, 45-51 courses"** is the catalog's own printed figure. It looks
   wrong, but only Emory can correct it.
5. **Review the gold set and eval cases.** None are marked reviewed, and only a person
   may mark them.

## Still weak in the data

- `course-orgo`: "organic chemistry" does not appear in `CHEM 202`'s record, so search
  misses it. A prompt line covers it for now.
- `major-qss` and `career-cs-pay` stay partial in the eval. The right documents appear,
  but not the exact passages the eval names.
- 25 ambiguous and 166 unparsed requisites remain, mostly graduate, business and
  honors-thesis wording. They block honestly rather than guess.
- 238 program slots point at a course no term shows, and 396 name a code with no
  course record. These are catalog lines older than the Atlas, shown as such.
- A course whose title hundreds of courses share ("honors", "directed study") can only
  be found by its code.
- The Dean's List has no current Emory source in the corpus.
- `emory-waitlists.md` is a staff guide last updated in 2023.

## Handed off (not data layer)

- **Search speed under load.** `embed_search` scans every embedding with no vector index,
  and the keyword query recomputes a text vector per row. It takes about 0.7 s per
  question on this machine with one user. Hiding repeated cards removed about a quarter
  of the rows, but this belongs to P5.
- **Live answers wander.** Questions outside the fixed paths still take 30-200 s and up
  to 14 provider calls. That is model behaviour and P2 work, not data.
- **Answer slips the data cannot prevent.** Live answers still blurred billed charges with
  cost of attendance once, misread the ARCHE rule once (the policy text is right), and
  told a premed student about `PHYS 121` when `PHYS 141` simply is not offered that term.
  These are model reading errors for Phase 2.
- **Provider speed.** During the last conversation run the provider fell from 222 to 67
  tokens a second with prompts the same size, tripling turn times. Worth watching.

## Operating notes

- A database restored from a snapshot made before this push needs
  `python -m scripts.refresh_term_cards --collection NAME`, because migration 082 adds
  the column with every card visible.
- `data/staging/advising-gaps-2026-11-06/` holds the new sources and their manifest. It
  is gitignored, so a rebuild from an empty `data/` needs those files or the snapshot.
- `RESEARCH_TOOLS_VERSION` is 24 and the prompt version is `research-43`.

## Team snapshot

Rebuilt after all of the above: `data/team/advisor-20261106.zip`, 704 MB, 414,992 rows and
15,656 files verified by a full restore. It already holds the hidden cards and new
sources, so a teammate restoring it does not need `scripts.refresh_term_cards`. It is not
uploaded yet. The 2026-11-05 snapshot is
superseded and can be deleted.

That snapshot is at schema 082. It is now out of date: rebuild it after this work is
committed, or a teammate gets a database without migrations 083 to 086.

## Guards added after grading real answers

- **Added** `_refuse_met_claims`: once an audit matched lines only, a final answer saying
  a course satisfies or fulfils a requirement is rejected, unless the sentence negates it.
- **Added** `unknown_question_codes`: a code in the question that is no current course
  reaches the model as `no_current_course`, with the nearest real codes.
- **Added** `answer_in_hand`, generalizing the which-majors path: "what does X unlock"
  now goes straight to the final answer once the graph has it, and `must_name` rejects
  an answer that leaves out a current course the graph lists. `reverse-cs253` went from
  11 of 14 in 88 s to 15 of 15 in about 25 s, on the three runs since `must_name` was added.

## Source conflicts

- **Added** `configs/source_conflicts.toml`, hand-written: a question pattern, a note for
  the model and the terms a final answer must contain. A matching answer that leaves one
  out is rejected. The first entry makes graduation-credit answers disclose both 127 and
  126, which live answers now do. Your decision on 2026-11-06.

## Test expansion and cuts (2026-11-06)

You asked for the live tests rerun and expanded, and for anything that is not student
advising, an admin panel or debug mode to be cut. Nothing here is committed yet.

### New live test

**Added** `scripts/manual/advising_sweep.py`. The clarity probe asks 19 fixed questions,
which get tuned against. The sweep draws random courses and programs from a seed, words
the questions like a student, and takes the right answers from the database. Change the
seed and it is a new test. It does not change the clarity probe.

Families: prerequisites, no prerequisites, title to code, code to title, program
requirements, which majors use a course, what a course unlocks, term offerings, made-up
courses, retired courses, "I took X, can I take Y" both ways, "what's left for my major",
Oxford twins, and 18 policy facts. Each policy fact names the sentence the corpus must
hold, so a fact Emory changes is reported as stale, not failed.

Live results, `minimax-m3.1-flash`. Each row is the whole battery on one seed:

| Run | Prompt | Cases fully passed | Notes |
| --- | --- | --- | --- |
| Sweep 3 | research-41 | 109 of 115 | |
| Sweep 4 | research-41 | 110 of 117 | |
| Sweep 5 | research-42 | 118 of 122 | `two-degrees` added, 7 of 7 |
| Sweep 6 | research-42 | 120 of 124 | |
| Sweep 7 | research-42, tools 22 | 119 of 122 | |
| Sweep 8 | research-42, tools 22 | 122 of 124 | |
| Sweep 9, new seed | research-42, tools 22 | 105 of 123 | 16 were the provider's 429, so 105 of 107 answered |
| Sweep 10, the four families these fixes touched | research-43, tools 23 | 36 of 37 | `phantom` 6 of 6, `two-degrees` 7 of 7 |
| Sweep 11, new seed | research-43, tools 23 | 122 of 124 | no 503s |
| Sweep 12, new seed, `prereq`, `eligible`, `offered`, `unlock` | research-43, tools 24 | 42 of 42 | final code |

Clarity probe, 19 fixed questions: 146 of 146 checks on research-41, then 155 of 156 on
research-42. On research-43 it was 16 of 19 probes on the first run and 17 of 19 on the
second, and the probes that missed changed between runs. The four affected by this round's
fixes then passed 34 of 34 checks on the final code, so that was model variance, not a
regression. `phantom-biol245` and `prereq-datasci350` missed on the first run only.
`policy-su-cap`, `unknown-course` and the research-42 miss were correct answers in wording
the frozen checker does not accept, such as "No. No Emory degree guarantees medical school
admission" or "I couldn't locate a course called".

The full battery was not rerun after the last fix, the `open` pattern below. That fix
changes which questions count as reverse, so sweep 12 covered the families that depend on
it, and the clarity recheck covered `reverse-cs253` and `boundary-seats`.

The first round of failures changed nothing in the product:

- **Standby.** The computer entered standby three times during live runs. In-flight calls
  hung until the 360 s deadline and came back as 422. Those were not advising failures.
  The keep-awake hold stops idle sleep only, so a closed lid still interrupts a run.
- **My new checks were too strict** in four places: "There is no CS 115" was not read as
  a denial, a blocked course answered with "Almost", a program check ignored codes the
  program names but that are no longer current, and "Where did you see ANT 139?" was read
  as asserting the code. Those were fixed in the new script. The frozen probes were not
  touched.
- **Model variance.** "What share of graduates get Latin honors" was answered as the Latin
  department once, and the semester withdrawal rule was missed once. Both passed on the
  rerun, and search returns the right documents for both.
- **Provider.** One turn failed after four provider errors in a row.

Later rounds found real gaps, each fixed once in the code that owns it:

- **Added** `other_degrees` on a program read. Computer Science, Biology, Chemistry,
  Mathematics, Physics, Physics and Astronomy and Psychology each exist as a BA and a BS,
  and an answer used to give one as if it were the only major. A new sweep family,
  `two-degrees`, asks about all seven. **Added** `must_name_degrees`: when the student
  named no degree, an answer that leaves the other one out is rejected. The prompt rule
  alone missed about one in twenty.
- **Added** `terms_checked` on a course read, so "is this offered in spring" can say no
  for a checked term instead of hedging.
- **Added** `research_courses_titled_in`: a course title in the student's words is a lead
  whatever the passages found, longest title first. "Differential Equations" sits inside
  "Partial Differential Equations", and the shorter title used to rank first.
- **Added** a program name boost: a program named in the question outranks every partial
  match, longest name first, so "religion and anthropology" is not "anthropology".
- **Changed** the made-up course path. `finish` now rejects an answer that leaves out a
  code the student asked about and that does not exist. A `fact` block needs evidence, and
  nothing can evidence an absence, so the model used up its corrections on it, and
  `salvage` then dropped that sentence and published the neighbouring courses as the
  answer (`PSYC 219`, sweep 9). The note now says to use an uncited `limitation` block,
  and `salvage` writes "There is no X in the Course Atlas." itself, since the master
  already settles it. This did not make them faster: the median stayed near 50 seconds.
  Most of that time now goes on the neighbouring courses the model reads.
- **Fixed** the reverse-question pattern in `retrieval/seams/prereq.py`. `opens?\s+(?:up)?`
  matched "open" before any word, so "how many seats are open in CS 170" and "is CS 170
  open this spring" counted as "what does this unlock", and `must_name` then forced a list
  of follow-on courses into the answer. It now needs "open up" or "what does X open". No
  check could see this as a failure; it showed only as a long answer and a rejected finish
  in the run's steps.

Changes to the new sweep, none to the frozen probe:

- `stays-on-the-program` went from 0.7 to 0.5 and now counts codes from a sibling degree.
  A good answer may add examples or point to the related program.
- "Specific" and "nothing lists" now read as no prerequisite.
- A sentence with "if" in it is no longer read as a promise.

Test budgets moved, the product limit did not: the prompt grew, so five byte budgets in
`test_research_types.py` went up by 500 and one scenario in `test_research_data.py` is at
56,000. Production stays at `research_max_prompt_bytes` 52,000.

Known misses I left alone, each seen once or twice across the sweeps:

- **Model invented a completed course.** Asked "I've taken ENVS 232, can I take ENVS 320",
  the first preparation check said blocked. The model reran it with ENVS 120 added and
  opened with "Yes", though it stated the assumption. A guard would also refuse "I took
  intro chem" mapped to CHEM 150.
- **A dead end on `policy-4plus1`.** 14 calls went on searching, `finish` was rejected and
  nothing survived `salvage`, so the student got an error. Safe but unfriendly, and it is
  Phase 6 "dead ends".
- **`ECON 102`** invented inside a question block. Those blocks skip the identity check on
  purpose, so a student's repeated typo is not rejected.
- **Stated limits of the checks.** `ANT 377W` is marked as no prerequisite in the graph,
  but a class note carries one, so the answer was better than the check. `ARTHIST 398`
  drops the catalog's R suffix. English audits cannot hit `names-expected` because the
  program is written in categories.
- **Model variance.** "Media Studies minor" was once said not to exist though search ranks
  it first, and `declare-by` was answered for American Studies once and declined once.
  `DATASCI 463` for Spring 2027 hedged and said that term "wasn't part of what I checked",
  though `terms_checked` lists it.
- **Codes the master does not hold.** A Media Studies answer named `FILM 401W` and
  `FILM 408`. The identity check accepted them, so the run's own evidence holds them, but
  the sweep found them in no cited passage. Same family as `ARTHIST 398`.

**Provider limit, new.** About 4,700 model calls in three hours ended in a hard 429 on
every call, starting 02:35 UTC on 2026-11-07, which is what the 16 failures in sweep 9
were. `retry_transient` gave up in about 3.5 seconds and the student got an honest
"rate-limited" 503. A short delay cannot outlast a usage window, so I left it. This does
matter for launch: the plan's capacity needs checking against real traffic, and a limit
hit in class hours fails every student at once.

### Cut

Deleted in four migrations, `083` to `086`. Dev database migrated. Before dropping, every
table was empty except `feature_flags`, which held its 7 shipped defaults.

| Cut | Why | Plan item |
| --- | --- | --- |
| Bulk artifact drafts: `routes/batches.py`, `jobs/provider_batches.py`, `jobs/native_artifact_batches.py`, `llm/batch.py`, `llm/batches.py` | No caller | P1.10 |
| Yes/no decisions: `routes/decisions.py`, `generation/decisions.py`, `llm/decision*.py`, the decision providers and role | No caller | P1.11 |
| Claim reviews: `routes/claim_reviews.py`, `generation/claim_review.py` | No caller | P1.11 |
| Planned artifact runs: `generation/planned_artifacts.py`, `jobs/artifact_plans.py`, 8 `artifact_run_*` tables, their triggers | Reachable only from `POST /api/runs`, which nothing called | New |
| Feature flags: `flags/`, `routes/flags.py`, cohorts | No panel since P1.1. Each flag is now its `config.py` setting | P1.8 |
| Catalog refresh: `admin/catalog_refresh.py`, its routes | No panel since P1.1 | P1.9, first half |
| Routes with no caller: `/api/recommendations`, `/api/retired-courses`, `/api/subgraph`, artifact create, import, save-revision and `preview.pdf`, `POST /api/runs`, `/api/runs/limits`, conversation memory and summaries, `export-summary`, `/api/quotas/*`, `/api/admin/quotas`, privacy export list and read, `/feedback/eval-candidates`, admin `/users/{id}`, `/sources/{id}/impact`, `/rules/diff`, `/audit/export` | No caller | P1.11, part new |
| Tool entries for Python, JavaScript and artifact creation in `tools/builtins.py` | No handler is ever injected | New |
| Frontend: unused app state, mock leftovers, the decision providers in the connection form | Nothing read them | New |
| Manual one-shots: `build_advisor_ledger`, `restore_course_authority`, `review_atlas_cleanup`, `unpaired_labs` | Finished one-time jobs | New |
| Roles `advisor` and `auditor`, `EXPORT_AUDIT`, `VERIFY_ACADEMIC_RECORD`. Migration 086 maps both to `student` (0 users on dev) | Only student and admin exist now | P1.7 |
| Artifact leftovers: `artifacts/revisions.py`, `import_docx`, `import_xlsx`, `import_artifact`, `parse_markdown`, `render_artifact_pdf`, `OfficePreviewRunner`, `/api/artifacts/{id}/original` and its button | Artifacts are only made by chat now | P1.11 |
| Dead code with no caller found by a sweep: schema models, memory and export helpers, about 60 named queries | No caller | P1.15, part new |
| 18 backend test files merged into their area file, 111 to 93 files, 1,655 tests collected before and after | Easier to find one test | P1.13 |

**Changed, please check:** `auth.capabilities.execution_settings` replaces flag evaluation.
It keeps the workspace access check and the rule that a debug setting cannot exceed the
process setting.

**Fixed along the way:**

- The admin Courses panel could not load: it still called `/api/admin/collections`, which
  P1.5 removed. A stale browser mock hid it.
- `schema_catalog` compared raw column numbers, which a restored backup renumbers after a
  `DROP COLUMN`, so consolidated restore would have failed. It now compares column order.
  This is the check itself, so please look at it.

**Restored after cutting:** 11 named queries I had dropped are still used by tests, and
the query check caught them: `delete_source`, `diagnostic_current_chunks`,
`diagnostic_recent_research_runs`, `get_course_by_code`, `list_atlas_sources`,
`list_unpaired_labs`, `get_program_binding`, `list_program_items_without_evidence`,
`list_privacy_deletions`, `set_privacy_export_expiry`, `seed_quota_policy`.

**Lost with the cuts:** the run-plan publish fence (`stale_plan`) existed only for planned
runs, so a plain artifact run has no check that its sources did not change between
drafting and publishing. Same for the kill switches: stopping a feature now means changing
a setting and restarting.

### Left in place, and why

- **Debug backend** (`debug/`, `routes/debug.py`, `conversations/experiments.py`, the
  `DEBUG_*` lines in `infra/modules/apps.bicep`): your message can be read as keeping it
  or cutting it, the plan's P1.9 deletes it, and nothing reaches it since P1.2. Your call.
- **Legacy answer path:** unreachable with research on, but `infra/modules/apps.bicep`
  sets research and tools to false, so a deploy from today's infra would run it. It goes
  in Phase 2, after that setting changes.
- **Sandbox**, **`hf-local`**, **frontend demo mode**: each needs a decision.
- **Stored originals:** the write path, columns, backup and privacy handling stay, because
  old backups restore through them. Nothing reads them in the app now.
- **Sandbox PDF operations** (`render_*_pdf`) stay with the sandbox decision.
- **Small leftovers:** `upsert_policy` (tests only; quotas have no production write path
  now), the memory epoch columns, the `account_id` column, the `batch` billing mode on
  price schedules, `POST /pricing/schedules`, the old values in `connection_profiles`
  check constraints, `scripts/reconcile_source_origins.py`, and `scripts/scrape_catalog.py`
  (kept for catalog policy pages, not courses).

Size against the plan's targets:

| Measure | Start of the plan | Now | Target |
| --- | --- | --- | --- |
| `backend/app` Python lines | 52,305 | 40,860 | 42,000 |
| Backend test files | 125 | 93 | 95 |
| `frontend/src/admin` + `debug` lines | 4,700 | 3,132 | 3,300 |
| `frontend/src/api/types.ts` lines | 2,093 | 1,155 | 1,400 |
| Roles | 4 | 2 | 2 |

## Gates at the end of this push

Backend: 1,658 passed, 2 skipped (the expected Windows-only skips), ruff clean, on the
final code. Frontend: typecheck, format and `check:callers` clean, 19 unit and 32 browser
tests passed, and the real-API integration suite passed 5 of 5. Frontend source did not
change after those runs; integration ran before the last backend fix.

`npm run test:integration` is not part of the usual gates, and it had gone stale since
`6d45450`: it still drove UI that P1.1 and the artifact cuts removed. It now seeds
artifacts through a test-only route and drives reindex and gap resolution through the real
API. Run it after any API cut.
