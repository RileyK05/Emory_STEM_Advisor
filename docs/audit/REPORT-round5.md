# Round 5 — identity reading, research gates, and one chunking measure

**Date:** 2026-10-30
**Scope:** another round of experiments against the live corpus and the live
model. Each finding below was measured before it was fixed, and the fix has a
regression test or a live measurement behind it. Nothing here is a claim that
the remaining work is done.

**Ground truth used:** the Atlas capture (`data/atlas/atlas-primary-spring-2027.jsonl`,
byte-identical to the indexed source), the stored `courses.requisites_raw`, the
program requirement text, and the recorded research runs.

**What is green after the round:**

| check | result |
|---|---|
| backend suite | 1704 passed, 2 skipped, 5 xfailed |
| ruff over `app/`, `tests/`, `scripts/` | clean |
| `scripts.manual.data_audit --collection default` | 0 integrity problems; gold set 0 mismatches (51 entries, 0 reviewed) |
| frontend `npm run typecheck` | clean (no frontend code changed) |
| legacy answering path, live probe | **108/108 checks, 19/19 probes** |
| research advising path, live probe | 1 of 4 re-run probes pass in full; the other three fail on the model's own malformed output, not on the identity check — see §4 |

---

## 1. The identity gate withheld correct answers

### The measurement

Six legitimate prerequisite questions, asked of the live legacy path:

| course | before | after |
|---|---|---|
| MATH_OX 212 | **refused** | answered, names MATH_OX 112 |
| PSYC_OX 210 | answered, names `Psyc_ox 110` | answered, names PSYC_OX 110 |
| PHYS_OX 152 | **refused** | answered, names PHYS_OX 151 |
| ECON_OX 112 | answered, names `Econ_OX 101` | answered, names ECON_OX 101 |
| BIOL 142 | answered, omits BIOL 151 | answered, names BIOL 151 |
| PHYS 462 | **refused** | answered, names PHYS 461 |

Three refusals and three answers carrying a malformed course code, on
questions whose answers are in the corpus.

### The cause

The gate asks whether the cited passage contains the code the answer names.
Both sides were read by the retrieval seam's query reader. That reader
resolves a subject only when it is written in capitals or is a known alias,
so the corpus's own spellings came out unread:

```
"Math_OX 112 or Math_OX 112Z or Math 112 as prerequisites"
"This course requires Psyc_ox 110, Psyc_ox 111 or equivalent transfer credit."
"This requisite mandates Phys_OX 151 as prerequisite."
"Econ_OX 101, Econ 101 as prerequisite"
"... BIOL 151or BIOL_OX141 ..."
"Math 112_OX as corequisite"
"... (BIOL 142 and BIOl 142L) ..."
```

A passage the reader cannot read makes a correct answer look unsupported, and
the answer is withheld. The requirement parser already resolves every one of
those spellings — it is the authority for the prerequisite graph — but the
gate did not use it.

### The fix

One shared reader, `canonical_codes_in` (`app/atlas/requisites.py`). It
resolves identities with the requirement parser's own subject resolution but
reads prose, not a requirement: no labels, no exclusions, no trees, and it
never states that a course is required. Two rules differ from the requirement
parser, because the question differs: a passage that lists codes carries the
subject across its separator (`CHEM 202Z + 202ZL`, `MATH 411-412`), and a
number printed in a level phrase (`GER 300 level courses`) still names that
course.

The safety property is a strict superset. Measured over all 1,257 stored
requirement texts:

* every code the requirement parser finds, the reader finds — **1,257/1,257, 0 misses**;
* every code the previous gate reader found, the reader finds — **0 misses, 16 gains**.

The 16 gains are codes the corpus really names and the old reader could not
read (`MATH_OX 112`, `MATH_OX 112Z`, `BIOL 151`, `ECON_OX 101`, `MATH_OX 221`,
`PHYS 461`, `PHYS_OX 151`, `PSYC_OX 110`, `PSYC_OX 111`, `ENVS 247`). The 71
codes the reader sees beyond the parser are codes inside enrollment-exclusion
sentences — named in the text, so an identity check must see them.

The reader was also A/B'd against the previous one over every answer the
probe has recorded (172 answers with citations). The previous reader rejects
9 of them, the new reader 7, and the three verdicts that differ all move
*toward* accepting:

| recorded answer | previous reader | new reader |
|---|---|---|
| a plan answer listing `DATASCI 496R/497R/499R` | rejects all three | accepts |
| `CS 326 … (CS 170 or CS_OX 170) …` | rejects `CS 171Z`, `CS_OX 326` | rejects `CS_OX 326` only |
| `CS 326 is described in the Emory Course Atlas as …` | rejects `CS 171Z` | accepts |

So the new reader does not make the gate stricter. A refusal seen once during
the legacy probe was the model's own stochastic output, not the reader: the
same probe question passed on three of four re-runs.

Regression: `backend/tests/test_passage_identity_reading.py`.

### The legacy path's own score

The legacy path's 19-probe score is **108/108 checks, 19/19 probes** (round 4:
107/107, 19/19). One probe needed attention first, and it turned out to be the
**instrument**, not the product. The check asks whether the answer states that
a course is not a core requirement. The model answered, on one run:

> "The supplied Computer Science BS requirements do not identify the course
> referenced in the question by code, so they do not establish it as a
> required core course."

That states the fact. The check's pattern looked for "not" within **three**
words of "listed / required / named / part of", so "not establish it as a
required" — four words — fell outside it, and its "does not" list had no
"establish". The window is now four words and the list matches the product's
own vocabulary of honest absence statements. Widening it does not let an
answer that asserts the course through: "CS 495 is a required core course",
"Yes, CS 495 is required" and "The Computer Science BS requires CS 495" are
all still refused by the pattern.

The instrument change is recorded here because it changes what the score
means, so a round-4 number and a round-5 number for that probe are not
strictly comparable.

One legacy refusal was also observed during the round, on the same probe. It
is the model's own stochastic output, not the reader: the same question
passed on three of four re-runs, and the reader A/B below shows the new
reader rejects *fewer* recorded answers than the old one.

---

## 2. The research path's identity check had blind spots, then the wrong level

### The blind spots

The check ran only for `fact` blocks whose text contained inclusion language
("required", "listed", "program", …). Two things escaped it:

* a **limitation** block naming courses the evidence does not contain, and
* a **fact** block that lists courses without that wording.

The round-4 audit recorded the first: an answer offered `CS_OX 325`,
`CS_OX 326`, `CS_OX 350`, `CS_OX 355` and `CS_OX 428` as examples of Oxford
counterparts it had not read. Emory's catalog has exactly five `CS_OX`
courses (170, 171, 224, 253, 297RE), so the answer told a student five
courses that do not exist.

The two former xfails in `backend/tests/test_course_identity_guard.py` are now
real tests, and a third test was added: an honest "the cited passages do not
name CS 325" limitation is still allowed.

### The wrong level

Widening the check to every fact and limitation block, and comparing each
block against only its own citations, rejected legitimate answers. In the
first measurement three of the first eight research probes returned 422 — the
run could not finish at all:

```
prereq-cs326       422   the answer named the right codes and cited narrower passages
reverse-cs253      422   same
phantom-math231    422   an honest absence claim, phrased in a way the escape did not cover
```

A 422 is a worse outcome for a student than an imperfect answer, so the check
was restructured into the two questions it was always mixing:

* **Identity** — a question about the course and the run: a course the answer
  names must be one that exists. The course master is the authority on
  existence and the run's evidence is the authority on what the run
  established. A block may cite a narrow passage while naming a course the
  run read elsewhere.
* **Claim support** — a question about the passage a block cites: a fact
  block that asserts a course is required, listed or part of a program must
  be backed by that passage.

Existence and coverage turned out to be the discriminator the first attempt
missed. A limitation that says "MATH_OX 221 was not read and is not treated
as interchangeable with MATH 221" is honest — MATH_OX 221 is a course Emory
offers. A limitation that says "the Oxford counterparts (for example
CS_OX 325) were not read" is not: Emory's catalog has no CS_OX 325, so the
answer tells a student a course exists. No phrasing rule separates the two;
the course master does.

Replaying every recorded decision through the new check: the two legitimate
answers that were rejected are now accepted, and the round-4 phantom
identities are still rejected.

---

## 3. The escape hatch did not cover the phrasings models produce

A clause that reports that the evidence does not establish a course may name
that course; that is the point of such a clause. The escape was a phrase
list, and the phrasings the model actually produces were not on it:

```
neither "CS 495" nor "Senior Seminar" appears anywhere in the stated requirements
MATH 211, MATH 212, and MATH 250 are not part of the CS BS math requirement
No course-level description for CS 495 was read, so its characterization here is only the name you supplied
```

The escape was widened to cover absence phrasings — "not part of", "not among",
"does not appear", "neither … appears", "is absent", "no … was read" — and the
widening was checked against the rule it must not break: *"were not read"* is
**not** an escape, so an answer may not offer courses the run never heard of.
That is the round-4 finding, and it is now a separate test.

`phantom-cs495` went from 5/7 to **6/6**.

---

## 4. The research path's policy read was pinned to one section
`pending_policy_read` held a single handle, and the controller rewrote the
read tool's schema so that the model could read exactly that one section
before its next search. The handle was the section the **keyword seam ranked
first**, not the section the retrieval score favoured.

The two rules that round 4 could not find live in different sections of the
same document:

| rule | chunk | section |
|---|---|---|
| 6-credit 4+1 double count | 19 | DUAL AND EXTENDED DEGREE PROGRAMS |
| 43rd-class-day S/U deadline | 48 | ACADEMIC STANDING (section starts at 41) |

`pending_policy_read` became `pending_policy_reads`: every section the search
matched. The controller's read-tool enum now lists all of them, and the round
hint says "read one of them now".

The remaining reach is a separate question: a section handle points at the
section's **first** chunk, and a read returns one 8,000-byte page, so the
ACADEMIC STANDING handle reaches chunk 41 and the rule sits at chunk 48. The
read reports its next cursor, so the rule is reachable — but it takes a second
page. That is a pagination question, not a routing one, and it is still open.

### What the research path still gets wrong

Four research probes were re-run after the fixes above. One now passes in
full, and the other three fail — but **no longer because of the identity
check**:

| probe | before | after | why it still fails |
|---|---|---|---|
| phantom-cs495 | 5/7 | **6/6** | — |
| phantom-math231 | 422, 3/4 | **13/13** | — |
| phantom-astr101 | 422, 4/5 | 422, 4/5 | the model sent `decision.artifact` in a shape the schema rejects, twice, and the run spent its budget correcting it. The answer's content passed the identity check. |
| prereq-cs326 | 422 | 422 | the model wrote a raw `[1]` citation marker in a block, then asserted a course its cited passage did not name |
| reverse-cs253 | 422 | 422 | the model listed a course in `recommended_course_codes` without having read its record, then sent a malformed `unresolved_questions` |

So the identity guard and the section discovery — the two things round 4 left
open on this path — are addressed, and what remains is the research path's
general robustness: the model produces a decision the schema rejects, and the
bounded correction does not always land within the operation budget. The
research path stays **off by default** for that reason.

---

## 5. The gold set could have drifted with the database unnoticed

`scripts.manual.data_audit` compared each gold entry with the **database**.
That is a self-consistency check: had the gold file and the database drifted
together, it would have passed.

The audit now also compares each entry's text with the **Atlas capture**, the
primary source. All 51 entries match the capture exactly (0 differ).

The entries remain `reviewed = false`. The mechanical cross-check is
automated; the comparison with Emory's own page is a person's reading and is
still outstanding for all 51.

---

## 6. Two audit documents had been deleted while their references remained

`docs/audit/db-data-review.md` and `docs/audit/security-attack-notes.md`
were removed by commit `9a64af3` ("Round 4 pending work"). `docs/architecture.md`
and `docs/audit/README.md` still pointed at them, so the design contract
referenced documents that did not exist. Both are restored.

The data review ends with an acceptance sweep; re-run against the same
collection today:

| probe | 2026-10-20 | 2026-10-30 | target | met |
|---|---|---|---|---|
| A. mentions that resolve to nothing | 905 | 716 | < 300 | no |
| B. self-loops | 10 | 0 | 0 | **yes** |
| C. impossible subject codes | 24 over 5 codes | 0 | 0 | **yes** |
| D. `kind` coverage | `prerequisite` only | `prerequisite` 2821, `corequisite` 88 | more than one kind | **yes** |
| E. mention/code mismatch | 0 | 0 | 0 (keep) | **yes** |
| F. level inversions | 53 edges | 54 edges | a drop | no |

Two notes on the two that moved. Probe A is down by 189 and is dominated by
the Oxford coverage gap rather than by parse errors: an `_OX` course with no
College master row is a coverage gap, not a bad parse. Probe F grew by one
because three of the 54 edges point at `DATASCI 999XFR` — Emory's placeholder
for a course still to be determined, not a 900-level graduate course. The
real inversions are 51, and every dependent course already parses as
`unparsed`, so no tree is built from them.

---

## 7. Two ingest paths counted tokens differently

### The measurement

`chunk_id` is `uuid5(source_id, "{chunker_version}:{chunk_index}")`: it
addresses a chunk by its **index** within its source. The chunking rules are
therefore part of what the id means.

The operator scripts counted with `AutoTokenizer` and no special tokens. The
HTTP upload path counted with the embedder's tokenizer, which adds them. The
two-token difference was enough to move a boundary:

* **42 of the 235 current sources** whose raw bytes are still on disk chunk
  differently under the two counters.
* Across all 437 stored sources — current and superseded — **7,516 chunks were
  cut one way and 21,105 the other**, and nothing recorded which.

### The fix

One chunking measure on every path, from the embedder (`Embedder.chunk_tokens`,
counting the tokens a text occupies) — separate from the embedding measure
(counting the tokens the model sees, special tokens included, because that is
what the model's ceiling applies to). The scripts no longer build a tokenizer
of their own, so they also stop downloading one outside the trusted
allowlist.

A re-index that would move a chunk's text under an unchanged id is now
refused, with a way through for an operator who means it
(`--allow-chunking-change`). The stored chunk records the count the chunker
measured, so the change is detectable: the current counter no longer
reproduces it.

The one current source cut the other way — `undergraduate-business-spring-2027.md`,
75 chunks — was re-indexed. The two counters cut that file **identically**,
so no cited chunk's text changed: 0 of its 16 cited chunks moved. The
remaining 14 sources cut the other way are superseded versions, and the guard
protects them too.

---

## 8. Two of my own findings were probe bugs, not product defects

Recorded so the next round does not re-derive them:

* A hand-written code regex used in the probe could not match a code with two
  letters after the number (`CHEM 202ZL`), which produced two false reports
  about the gold set. The gold set was right on all 51 entries.
* The requirement parser and the program parser disagree on 69 courses; the
  disagreement is in the *program* parser's weaker reader, not in the
  requirement parser. The shared reader is the fix.

---

## Measured and left alone

**Program choice counts.** 375 of 2,894 program clauses name a choice without
an executable count. 140 state a **minimum** ("at least four courses") and
must never become an exact choice of four; 69 are cleanly recoverable as an
exact N; the rest are stated in credits or not at all. No rule release exists,
so nothing reaches a student yet, and a wrong count would be worse than no
count.

**The rule draft's root disposition.** The draft root says
`coverageDisposition: supported` while every clause it creates is
`unresolved`. Measured: the disposition marks the container (the requirement
list is fully accounted for), the children carry the clause status, and the
audit engine reports one row per clause. Making the root `unresolved` would
be more literal but collapses the report from three rows to one, losing the
detail a reviewer needs. The reading is now written down in the code.

---

## Still open

- **The research path is not on by default.** It stays off until its live
  score is at least as good as the legacy path's.
- **The research path's remaining failures are the model's own malformed
  output.** A raw `[1]` citation marker in a block, an unread recommended
  course in `recommended_course_codes`, a malformed `decision.artifact` or
  `unresolved_questions`. The bounded correction does not always land within
  the operation budget, and the run ends in a 422 rather than a refusal.
- **A section handle points at the section's first chunk.** A rule several
  chunks into a long section needs a second page; the read reports its
  cursor, so the rule is reachable, but one page may not hold it.
- **The gold set's 51 entries are all unreviewed.** The mechanical check
  against the Atlas capture is automated; the comparison with Emory's own
  page is a person's reading.
- **48 retrieval eval cases are `pending_human_review`.** A person has to
  read them; the runner is a manual script and is deliberately out of pytest.
- **Oxford coverage.** `_OX` courses with no College master row are a
  coverage gap, not invalid courses. Their prerequisites stay unresolvable
  until the master covers them.
