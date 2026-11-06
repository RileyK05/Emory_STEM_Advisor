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
- `RESEARCH_TOOLS_VERSION` is 20 and the prompt version is `research-40`.

## Team snapshot

Rebuilt after all of the above: `data/team/advisor-20261106.zip`, 704 MB, 414,992 rows and
15,656 files verified by a full restore. It already holds the hidden cards and new
sources, so a teammate restoring it does not need `scripts.refresh_term_cards`. It is not
uploaded yet. The 2026-11-05 snapshot is
superseded and can be deleted.

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

## Gates at the end of this push

Backend: 1822 passed, 2 skipped (the expected Windows-only skips), ruff clean. Frontend:
typecheck and format clean, 19 unit and 32 browser tests passed.
