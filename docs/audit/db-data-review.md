# Database data review — prerequisite graph anomalies

**Scope:** the atlas data written directly into Postgres (`courses`, `prereq_nodes`,
`prereq_mentions`), collection `default` (`cf9d7a06-5d2c-44a8-956f-66eb68394666`).
**Method:** read-only SQL sweeps over the live database. No model judgement about what any
prerequisite *should* be.
**Companion:** `mimo_scan.md` (code review). This document is the data half of that review.

---

## The rule I followed, because it is the rule

`AGENTS.md` states the project's one hard constraint:

> The prerequisite graph is parsed deterministically from Emory's course atlas. **An LLM never
> writes, edits, infers, or fills in any of its nodes or edges.** A prerequisite the parser
> can't handle is a parser fix or a manual fix, never a model call.

**I did not insert, update, or delete a single row.** Everything below was produced by `SELECT`s.
This document is a *worklist for humans*, exactly as that rule requires. Where a fix is
possible without a human, it is a **deterministic parser fix**, and I say so.

Two consequences worth stating plainly:

1. I am not going to tell you `CS 171` "should" require `CS 170`. I don't know that. A person
   with the catalog of record knows that.
2. The correct remediation path is **fix the parser → re-import → re-run the sweep**. Hand-editing
   `prereq_nodes` rows is how this graph becomes un-auditable. The sweep at the bottom of this
   document is the acceptance check.

---

## Scoreboard

| Metric | Value |
|---|---|
| Courses in atlas | 3,342 |
| Prerequisite mentions (`prereq_mentions`) | 2,353 |
| — that resolve to a real course | 1,448 (61.5%) |
| — **that resolve to nothing** | **905 (38.5%)** |
| Courses with ≥1 unresolvable prerequisite | 460 (13.8%) |
| Self-loops (course requires itself) | 10 |
| Cycles | 11 |
| Mentions of courses that do not exist at all (fabricated) | 24 |
| Co-requisites stored as *prerequisites* | 20 courses affected |
| Courses whose spelled-out prerequisites are entirely lost | 28 |
| `kind` values present in the graph | `prerequisite` only (of 3 supported) |
| `parse_status = 'none'` (nothing parsed) | 2,525 (75.5%) |

---

# PART 1 — Confirmed errors. These are bugs, not judgement calls.

Every item in Part 1 is mechanically provable wrong. A course cannot be its own prerequisite.
"TWO 300" is not a course. Each has a single root cause in `backend/app/atlas/requisites.py`,
and each is fixable deterministically.

## 1.1 Fabricated course codes — 24 mentions of 5 impossible courses

The subject regex accepts any 2–5 letter word with no word boundary and no subject
validation (see `mimo_scan.md` §0.1). The parser then writes the result into the graph as if
it were a course.

| Phantom code | × | Written as the prerequisite of |
|---|---|---|
| `TWO 300` | 9 | FREN 460, FREN 460W, FREN 471, FREN 471W, FREN 488, FREN 488W, FREN 495A, FREN 495BW, FREN 497R |
| `OX 100` | 12 | (see 1.1b) |
| `ANY 300` | 1 | GER 405 |
| `ONE 300` | 1 | SPAN 334 |
| `OF 201` | 1 | RUSS 202 |

**Root cause.** These are English words consumed as subject codes:

- `TWO 300` ← *"two 300-level courses"* (FREN 460 et al.)
- `ONE 300` ← *"one 300-level course"* (SPAN 334)
- `ANY 300` ← *"any 300-level course"* (GER 405)
- `OF 201` ← *"…of 201"* (RUSS 202)

The words `TWO`, `ONE`, `OF` are not in any subject list. Note that
`programs/requirements.py:40` already maintains `_NOT_SUBJECTS = {"ALL","AND","ANY","OR","THE",
"NOT","ONLY","PLUS","LEVEL","CU"}` and `_is_level_descriptor()` (`:118`) to prevent exactly this.
`atlas/requisites.py` uses **neither**. `ANY` is caught by that table; `TWO`, `ONE`, `OF` are
not in it either, so even the program parser is not fully safe.

**Impact.** Nine French seminars advertise a prerequisite that cannot be satisfied. The
sequence planner cannot place these courses, and the retrieval seam
(`prereq_upstream`) will happily serve a "prerequisite" chunk for a course that does not exist.

**Fix (deterministic, in the parser):** apply `_NOT_SUBJECTS` + `_is_level_descriptor()` from
`programs/requirements.py`, require a word boundary before the subject, and reject a subject
that is not in the known-subject/alias set. **Do not fix by deleting the 24 rows** — re-import
after the parser fix.

**Add to `_NOT_SUBJECTS`:** `TWO`, `ONE`, `OF`, `SOME`, `EACH`, `UPPER`, `GRADUATE`.

## 1.1b `OX 100` — an `_OX` parsing artifact (12 mentions)

`OX 100` is not a course; it is the tail of an Oxford code (`QTM_OX 100`, `PE_OX 100`) that the
scanner re-split when the `_OX` subject form appeared after a carry separator. Note
`programs/requirements.py:45-46` already handles this case explicitly:

> `# "ENVS OX 230" is how the catalog sometimes writes ENVS_OX 230.`

Same asymmetry. Fix in the scanner; the alias already exists next door.

## 1.2 Self-loops — 10 courses require themselves

A course listed as its own prerequisite is impossible on its face. Three distinct root causes,
all of them **text that is not a requirement at all being read as one**:

| Course | Raw requisites text | Root cause |
|---|---|---|
| `PE 150` | `PROHIBITS: Enrollment PE 150` | prohibition, not prerequisite |
| `PE 173` | `PROHIBITS: Enrollment PE 173` | prohibition |
| `PE 250` | `PROHIBITS: Enrollment PE 250` | prohibition |
| `MATH 111` | `Students completing MATH 119 or equivalent cannot enroll for MATH 111.` | enrollment restriction |
| `PSYC 111` | `Students completing PSYC_OX 100 or equivalent cannot enroll in PSYC 111.` | enrollment restriction |
| `FREN 313` | `FREN 310 … as prerequisite. Used for: FREN 313, FREN 314` | "Used for:" trailer |
| `FREN 314` | `FREN 310 … as prerequisite. Used for: FREN 313, FREN 314` | "Used for:" trailer |
| `HLTH 321` | `HLTH 220 or HLTH 221 … Used for: HLTH 321` | "Used for:" trailer |
| `HLTH 415` | `HLTH 210 & HLTH 230 & HLTH 250 … Used for: HLTH 415` | "Used for:" trailer |
| `MATH 275` | `AP Calculus BC … Used for: MATH 275` | "Used for:" trailer |

**Prevalence of the offending patterns:** 5 courses say `PROHIBITS`, 3 say `cannot enroll`,
13 say `Used for:`.

**What `Used for:` actually means.** It is a *shared requirement block* trailer: the requirement
text before it applies to the courses named after it. So for `FREN 313`'s record, the trailer
names `FREN 313, FREN 314` — the courses the block governs, **not** their prerequisites. The
parser reads them as prereqs, producing both self-loops and the `FREN 313 → FREN 314 → FREN 313`
cycle in §1.3.

**Fix (deterministic):**
1. Strip (or separately classify) three patterns before scanning: `PROHIBITS: …`,
   `cannot enroll (for|in) …`, and everything after `Used for:`.
2. Never emit a mention whose target equals the course's own code. This is a one-line invariant
   and it should be asserted, not hoped for.

## 1.3 Cycles — 11, all artifacts

```
FREN 313 -> FREN 314 -> FREN 313     (the only non-trivial cycle)
HLTH 415 -> HLTH 415   MATH 111 -> MATH 111   MATH 275 -> MATH 275
FREN 313 -> FREN 313   FREN 314 -> FREN 314   PE 150 -> PE 150
PE 173 -> PE 173       PE 250 -> PE 250       PSYC 111 -> PSYC 111
HLTH 321 -> HLTH 321
```

Ten of the eleven are the self-loops from §1.2. The eleventh (`FREN 313 ↔ FREN 314`) is the
`Used for:` trailer naming both courses. **No genuine circular prerequisite was found.** Fixing
§1.2 eliminates all 11.

Worth noting for the record: the graph is *not* structurally circular. That's genuinely good.

## 1.4 Co-requisites are stored as prerequisites — 20 courses

The parser supports three mention kinds (`prerequisite`, `corequisite`, `recommended`).
The database contains **one**:

```
prereq_mentions kinds : {'prerequisite': 2353}
prereq_nodes kinds    : {'prerequisite': 1875}
courses whose raw text says "co-requisite": 20
```

Verified case by case:

| Course | Raw text | Stored as |
|---|---|---|
| `BIOL 120L` | `BIOL 120 or BIOL_OX 120 as **Corequisite**` | `prerequisite` |
| `BIOL 142L` | `BIOL 142 as a **corequisite**.` | `prerequisite` |
| `CHEM 202ZL` | `CHEM 202Z as **co-requisite**.` | `prerequisite` |
| `SOC 358L` | `SOC 358 or equivalent transfer course as **corequisite**.` | `prerequisite` |
| `ENVS 347L` | `ENVS 347 as **co-requisite**.` | `prerequisite` |
| `PHYS 151` | `… as prerequisites and (MATH 112 …) as **co-requisites**` | `prerequisite` |

**This is the most user-visible error in the database.** A co-requisite may be taken
*concurrently*; a prerequisite may not. As stored, the sequence planner will tell a student they
must **finish** `CHEM 202Z` before enrolling in `CHEM 202ZL` — the catalog says they must take
them **together**. Same for every lab course in the list (`BIOL 120L`, `CHEM 150L`, `CHEM 202L`,
`CHEM 203L`, `CHEM 204L`, `CHEM 205L`, `CHEM 300L`, `SOC 358L`, `ENVS 240L`, `ENVS 347L`).

It also **manufactures false level inversions** — see the note in §3.

Note the delicious detail: `prereq_upstream`/`prereq_downstream` (`db/queries/prereq.sql:24, 41`)
already filter `m.kind IN ('prerequisite','corequisite')`. The read layer is ready for a kind
the write layer never produces.

**Fix (deterministic):** emit `kind='corequisite'` when the requirement clause is headed by a
corequisite label. `atlas/requisites.py` already tokenises `cond`/label text — the label is
right there in the raw string (`"… as co-requisite"`). Also emit `recommended` for
"recommended preparation", which the parser recognises and the DB never stores (0 rows).

## 1.5 Spelled-out subject names are silently dropped — 28 courses lost every prerequisite

Emory's requisites text frequently spells the subject out. The atlas scanner cannot read it,
because a spelled-out name is 6–14 characters and `_SUBJECT` only accepts 2–5 (`[A-Za-z]{2,5}`)
or 6–7 uppercase (`[A-Z]{6,7}`):

| Course | Prerequisites named in the catalog | Stored |
|---|---|---|
| `ANT 308` | `Anthropology 201` or `Biology 142` | **nothing** |
| `BIOL 302` | `Chemistry 301`, `Chemistry 222`, `Biology 141` | **nothing** |
| `PHYS 312` | `Physics 253` | **nothing** |
| `PSYC 335` | `Psychology 110` | **nothing** |
| `REL 470` | `Philosophy 358` | **nothing** |
| `THEA 350` | `Theater 251` | **nothing** |
| `ENGRD 221R/W` | `English 101` | **nothing** |
| `ARAB 102/201/301/497R` | `Arabic 101 / 102 / 202 / 302` | **nothing** |
| `HEBR 102/201/202/301/301W/440/497R` | `Hebrew 101 / 102 / 201 / 202 / 302` | **nothing** |
| `PERS 102/201/202/301/302/302W` | `Persian 101 / 102 / 201 / 202 / 301` | **nothing** |
| `ITAL 470/470W` | `Italian 302` | **nothing** |

**154 courses** contain a spelled-out subject + number in their requisites text.
**28 of them produced zero parsed mentions** — their prerequisites are gone entirely.

**Look at the language departments.** The complete Arabic sequence (`102 → 201 → 301`), Hebrew
(`102 → 201 → 202 → 301 → 440`), and Persian (`102 → 201 → 202 → 301 → 302`) chains are **all
missing**. A student planning Arabic is currently told `ARAB 301` has no prerequisites. This is
the failure mode the user described — *"this 300-level course has no prereqs listed"* — and it
is not a data gap, it is a parser gap.

**Fix (deterministic, and the fix already exists):**
`programs/requirements.py:25-38` defines `SUBJECT_ALIASES` containing exactly the needed
mappings — `"Arabic": "ARAB"`, `"Hebrew": "HEBR"`, `"Persian": "PERS"`, `"Italian": "ITAL"`,
`"Anthropology": "ANT"`, `"Biology": "BIOL"`, `"Chemistry": "CHEM"`, `"Physics": "PHYS"`,
`"Psychology": "PSYC"`, `"Music": "MUS"`, `"Hindi": "HNDI"`, `"Theater": "THEA"`, `"Film":
"FILM"`, `"ANTH": "ANT"`, `"BIO": "BIOL"`. The retrieval seam even imports it
(`retrieval/seams/prereq.py:14, 27-31`) with the comment *"so the seam does not go dormant on a
spelled-out subject"*.

So the query path normalises spelled-out subjects. **The ingest path does not.** A student
asking about "Arabic 202" finds the course; the graph does not know `ARAB 301` requires it.

## 1.6 Subject alias near-misses — 8 mentions of courses that exist under their canonical code

Unresolved codes where the canonical form **is in the atlas right now**:

| Stored (unresolved) | × | Exists as | Title |
|---|---|---|---|
| `HINDI 101/102/201/301/302` | 5 | `HNDI 101…302` | Elementary/Intermediate/Advanced Hindi |
| `MUSIC 121`, `MUSIC 122` | 2 | `MUS 121`, `MUS 122` | Theory, Analysis, Aural Skills I/II |
| `DANCE 150R` | 1 | `DANC 150R` | Movement Improvisation |

Also present as unresolved: `ANTH_OX 201/202/205` (10 mentions) where `ANT 201`, `ANT 202`,
`ANT 205` exist — `SUBJECT_ALIASES` maps `"ANTH": "ANT"` already.

These are the cheapest wins on this list: the target rows exist, the mapping exists, the ingest
path just doesn't consult it.

## 1.7 Restriction and prohibition text read as requirements

Beyond the self-loops, this class corrupts edges generally:

- **`PROHIBITS: Enrollment …`** (5 courses) — a prohibition is the *opposite* of a prerequisite.
- **`Students completing X cannot enroll in Y`** (3 courses) — an exclusion rule.
- **`Used for: …`** (13 courses) — a shared-block scope trailer.

None of the three is a prerequisite statement. All three currently produce prerequisite edges.
Note `HLTH 495A` / `HLTH 495BW` carry `Used for: HLTH 415`, so the parser can also produce
*backwards* edges between unrelated courses (the block belongs to 415, not to 495A).

---

# PART 2 — Coverage gaps. Not errors — omissions that make the graph lie.

## 2.1 Oxford College is entirely absent — 697 mentions into a void

`courses` contains **zero** `_OX` courses. 697 prerequisite mentions point at them.

| Subject (Oxford) | Mentions | Distinct codes |
|---|---|---|
| `ECON_OX` | 123 | 6 |
| `MATH_OX` | 105 | 13 |
| `BIOL_OX` | 99 | 6 |
| `CHEM_OX` | 74 | 17 |
| `ENVS_OX` | 66 | 8 |
| `PHYS_OX` | 46 | 7 |
| `FILM_OX` | 33 | 7 |
| `PE_OX` | 25 | 13 |
| `CS_OX` | 18 | 4 |
| `QTM_OX` | 17 | 5 |
| + 12 more | ~88 | — |

`BIOL_OX 142` alone is cited by **82 courses** — the single most-referenced prerequisite in the
entire graph, and it does not exist. `ENVS_OX 131` (47), `ECON_OX 101` (35), `ECON_OX 201` (34),
`MATH_OX 221` (18) follow.

The scraper evidently covers one school. Whether Oxford courses should be ingested, or whether
`_OX` references should be mapped to their Atlanta equivalents, is a **product decision for a
human** — either way, 697 edges currently point at nothing.

## 2.2 Missing subjects — schools outside the scraped catalog

| Subject | Mentions | Note |
|---|---|---|
| `QTM` + `QTM_OX` | 51 | Quantitative Theory and Methods — a real Emory department |
| `FIN` | 34 | Goizueta Finance (`FIN 201` cited 34×) |
| `BUS` / `BUS_OX` | 9 | Goizueta (`BUS 350`) |
| `ISOM` | 2 | Goizueta ISOM |
| `IBS` | 1 | International Business Studies (`IBS 514`) |
| `ASTR_OX`, `GHCS`, `ART_OX` | 4 | other units |

`QTM` deserves particular attention: `DATASCI 220` and `POLS 208` both require `QTM 110`/`QTM 210`
and none resolve.

## 2.3 Upper-level courses with nothing parsed

| Level | Requisites text present | Count |
|---|---|---|
| 300 | no text | 1,052 |
| 300 | text, `unparsed` | 53 |
| 300 | text, `parsed` | 1 |
| 400 | no text | 530 |
| 400 | text, `unparsed` | 101 |
| 400 | text, `parsed` | 2 |

**154 upper-level courses have requisites text and no parsed prerequisites.** Roughly half are
genuinely prose and correctly refused — see Part 4. The other half are §1.5.

Overall `parse_status='none'` covers 2,525 courses (75.5%). Zero of those have requisites text
(verified: `parse_status='none'` and non-empty raw ⇒ 0 rows), so `none` means "no requisites
published", not "parse failed". That is a clean signal and worth keeping.

---

# PART 3 — Suspicious, faithfully parsed. Needs a human.

**These are not parser bugs.** The source text says what it says and the parser read it
correctly. They are flagged because a 100-level course should probably not require a 200-level
course, and someone who knows the curriculum should look.

Full inventory: **53 edges where the prerequisite is at a higher level than the course that
requires it.** 28 are PE (§3.2). 25 are below. By transition:

| Dependent level | Prerequisite level | Edges | What it is |
|---|---|---|---|
| 1xx | 2xx | 11 | intro courses requiring intermediate ones |
| 2xx | 3xx | 14 | includes 6 `ENVS → BUS 350`, 4 `POLS 208 → …` |
| 2xx | 4xx | 26 | all 26 are `PE 230`/`PE 232` → `PE 401R…413R` |
| 3xx | 4xx | 1 | `CS 312 → CS 470` |
| 4xx | 5xx | 1 | `BIOL 450 → IBS 514` |

Note that a 1xx→2xx edge is not automatically wrong (`PORT 111 → SPAN 212` is legitimate, see
below). The flag is "look at this", not "this is false".

## 3.1 The poster child

**`CS 171` — Introduction to Computer Science II — requires `MUS 221`.**

```
requisites_raw: This course requires MUS 221 or equivalent transfer credit as a prerequisite.
parse_status:   parsed
```

A second-semester computer science course requiring *Theory, Analysis, and Aural Skills*. The
text is unambiguous and the parser read it correctly, which means either the catalog of record
has an error or a requirement group is attached to the wrong course upstream in Coursedog.
**This is the single most important item on the human review list.** Verify against the catalog
PDF before changing anything.

## 3.2 Other level inversions worth a look

| Course | Requires | Why flag |
|---|---|---|
| `CS 312` (300) | `CS 470` (400) | higher-level course in an OR list (`CS 211 or CS 323 or CS 325 or CS 334 or CS 470`) — plausible but unusual |
| `BIOL 450` (400) | `IBS 514` (500) | graduate course offered as an undergrad alternative |
| `ECON 112` (100) | `FIN 201` (200) | intro course with a business-school alternative |
| `ENVS 250/260/270` (200) | `BUS 350`, `BUS_OX 350` (300) | 6 edges |
| `POLS 208` (200) | `POLS 310`, `POLS 310W`, `ISOM 350`, `BUS 350` | 4 edges; `POLS 208` also has 9 unresolvable prereqs |
| `PE 230`, `PE 232` (200) | `PE 401R` … `PE 413R` (400) | **26 edges** — see below |
| `PHYS 222` (200) | `CHEM 331` (300) | one alternative in a 3-way OR (`CHEM 205 or CHEM 331 or PHYS 253`) |
| `DATASCI 220` (200) | `MATH 361`, `MATH 362` (300) | nested branch `(MATH 361 and MATH 362)` inside a larger OR |
| `PORT 111`, `PORT 114` (100) | `SPAN 212`, `SPAN_OX 212` (200) | **probably correct** — "Intensive Portuguese for Speakers of Another Romance Language" |

**`PE 230` / `PE 232`** list thirteen 400-level research courses among ~35 alternatives
(`PE 110,112,113,…,401R,402R,…,413R, or PE_OX 100,101,…`). Semantically this is "any approved
activity course", and the catalog expresses it as one enormous OR. The 26 "inversions" are real
rows but almost certainly not real sequencing constraints. Worth a rule-level review of how PE
activity lists are represented, not 26 individual corrections.

## 3.3 Caveat: some inversions are an artifact of §1.4

`PHYS 151` and `PHYS 152` (100-level) "require" `MATH 210`/`MATH 211` (200-level) — but their raw
text says those are **co-requisites**:

```
(MATH 111 or MATH_OX 111) as prerequisites and (MATH 112 or MATH_OX 112 or MATH 112Z
or MATH 210 or MATH_OX 210) as co-requisites, or equivalent transfer credit.
```

Once §1.4 is fixed and those edges become `corequisite`, these inversions disappear. **Fix
§1.4 before working the inversion list**, or you will review the same rows twice.

## 3.4 Flat mention lists overstate nested structure

`DATASCI 220`'s raw text is `(QTM 110 or QTM_OX 110) and [QTM 210 or QTM_OX 210 or (MATH 361 and
MATH 362)] and (MATH 221 or MATH_OX 221) or …`. `prereq_nodes` preserves the `all`/`any`/`course`
nesting (1,875 nodes) and is structurally sound. `prereq_mentions` is a flat list that cannot
express "361 **and** 362, but only as one alternative to QTM 210".

`prereq_upstream`/`prereq_downstream` read the **flat** layer. So the retrieval seam reports
`MATH 361` and `MATH 362` as unconditional prerequisites of `DATASCI 220`, which they are not.
Not a wrong edge — a wrong *shape*. Flag for whoever owns the seam.

---

# PART 4 — Things that look broken and are not

Because a review that only complains teaches nobody anything.

- **Prose prerequisites are correctly refused.** `AAS 495A` — *"approval of adviser and the
  director of undergraduate studies"*; `ANT 332` — *"Juniors or Seniors only or permission of
  instructor"*; `ENG 387` — *"one course in religion and one in literature"*; `HIST 494R` —
  *"One HIST course 200 level or above"*. The parser emits nothing. **Correct.** This is the
  hard rule working: no course code, no edge. Do not "improve" this into inventing codes.
- **`parse_status='none'` is honest.** 0 courses have requisites text and status `none`. `none`
  means "the catalog published no requisites", not "we failed".
- **No duplicate edges.** `(course, target, kind)` is unique across all 2,353 rows. Zero dupes.
- **No genuine cycles.** All 11 are artifacts of §1.2. The graph is acyclic underneath.
- **`prereq_nodes` structure is intact** — `all`/`any`/`course`/`condition` types are used
  sensibly and the CHECK constraints are satisfied.
- **ID stability holds.** Content-addressed `uuid5` identity is consistent; re-import preserves
  citation-stable IDs as designed.

---

# Recommended actions

Ordered by (damage × certainty). Nothing here requires judgement about curricula except §R6.

| # | Action | Type | Fixes | Priority |
|---|---|---|---|---|
| **R1** | Apply `_NOT_SUBJECTS` + `_is_level_descriptor()` + subject validation to `atlas/requisites.py`; require a word boundary before the subject | Parser fix | §1.1 (24 rows), §1.1b (12) | **P0** |
| **R2** | Never emit a self-referential mention; strip `PROHIBITS:`, `cannot enroll`, and `Used for:` trailers before scanning | Parser fix | §1.2 (10 rows), §1.3 (11 cycles), §1.7 | **P0** |
| **R3** | Emit `kind='corequisite'` / `'recommended'` from the clause label | Parser fix | §1.4 (20 courses), removes false inversions in §3.3 | **P0** |
| **R4** | Route `atlas/requisites.py` through `SUBJECT_ALIASES` (already exists) | Parser fix | §1.5 (28 courses, 26+ lost prereqs), §1.6 (8 rows) | **P0** |
| **R5** | Re-import the atlas after R1–R4, then re-run the sweep as the acceptance gate | Process | all of Part 1 | **P0** |
| **R6** | Human review of `CS 171 → MUS 221` against the catalog of record | Manual | §3.1 | P1 |
| **R7** | Product decision: ingest Oxford College (`_OX`), or map `_OX` codes to Atlanta equivalents | Manual | §2.1 (697 edges) | P1 |
| **R8** | Decide whether Goizueta (`FIN`, `BUS`, `ISOM`, `IBS`) and `QTM` belong in scope | Manual | §2.2 (64 edges) | P1 |
| **R9** | Rule-level review of PE activity-course lists (`PE 230`/`PE 232`) | Manual | §3.2 (26 edges) | P2 |
| **R10** | Teach the retrieval seam to prefer `prereq_nodes` structure over the flat mention list | Code | §3.4 | P2 |
| **R11** | Work the residual inversion list (`CS 312`, `BIOL 450`, `POLS 208`, `PHYS 222`, `DATASCI 220`) | Manual | §3.2 | P2 |

**Do not** hand-edit `prereq_nodes`/`prereq_mentions` to apply R1–R4. The graph is regenerated
from the atlas by the importer; hand edits are silently overwritten and leave no provenance.
If a single edge must be corrected before R1–R4 land, that is a **manual fix under the hard
rule**, and it should be recorded as one — not smuggled in as an ad-hoc `UPDATE`.

---

# Appendix — the acceptance sweep

Re-run these after re-import. Every count in the left column should reach the right.

```sql
-- A. fabricated / unresolvable targets (expect a large drop after R4+R1)
SELECT count(*) FROM prereq_mentions WHERE target_course_id IS NULL;
--   before: 905   target: < 300 (the irreducible _OX + Goizueta gap until R7/R8)

-- B. self-loops (expect 0 after R2)
SELECT count(*) FROM prereq_mentions m JOIN courses c ON c.course_id = m.course_id
WHERE m.target_code = c.code;
--   before: 10    target: 0

-- C. impossible subject codes (expect 0 after R1)
SELECT target_code, count(*) FROM prereq_mentions
WHERE split_part(target_code,' ',1) IN ('TWO','ONE','ANY','OF','ALL','SOME','EACH','OX')
GROUP BY 1;
--   before: 24 rows over 5 codes   target: 0

-- D. co-requisite kind coverage (expect > 0 after R3)
SELECT kind, count(*) FROM prereq_mentions GROUP BY 1;
--   before: prerequisite 2353 only   target: three kinds present

-- E. every mention resolves to a course whose code matches the mention
SELECT count(*) FROM prereq_mentions m JOIN courses c ON c.course_id = m.target_course_id
WHERE c.code <> m.target_code;
--   before: 0     target: 0   (keep this at 0)

-- F. level inversions for human review (expect a drop after R3, which removes the
--    false "co-requisite stored as prerequisite" edges)
SELECT c.code AS dependent, m.target_code AS prerequisite,
       substring(c.code from '(\d{3})')::int / 100 AS dependent_level,
       substring(m.target_code from '(\d{3})')::int / 100 AS prerequisite_level
FROM prereq_mentions m
JOIN courses c ON c.course_id = m.course_id
WHERE substring(m.target_code from '(\d{3})')::int / 100
    > substring(c.code from '(\d{3})')::int / 100;
--   before: 53 edges (28 PE, 25 non-PE)
--   by transition: 1xx<-2xx 11 | 2xx<-3xx 14 | 2xx<-4xx 26 | 3xx<-4xx 1 | 4xx<-5xx 1
```

The probes used for this review are read-only and re-runnable; they were run against the
embedded `pgserver` instance under `DATA_DIR/pgserver` via `app.db.connection.open_connection`.
