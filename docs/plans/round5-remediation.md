# Round 5 remediation

Written 2026-11-10 from the QA round 5 report (`REPORT-round5.md`, tested at
`1d0ee54` on the `advisor-20261107.zip` snapshot, model `mimo-v2.6-flash` via
Ollama Cloud, judge LongCat-2.5). Overall: 1548 of 1692 graded items pass
(91.5%), with 23 false positives (FP) and 89 false negatives (FN). The live
artifact suite passed 10 of 23 on minimax.

## Implementation status (2026-11-10)

Phases 1–3 are done and committed (`84321db`). Each has its test rows. Where the build
differs from the plan text below:

- **Phase 1:**
  - Research has its own secret patterns (keys, DSNs with credentials, private
    keys, tracebacks). It doesn't reuse `FORBIDDEN_LEAK_PATTERNS`, because that
    list names model vendors (OpenAI, Anthropic), which a career answer may
    legitimately mention.
  - Prompt text is caught by the 8-word shingle overlap instead.
  - A reply under 12 letters is refused ("Yes." alone included).
- **Phase 2:**
  - No migration. On a retry, `ResearchStore.start(retry=True)` detaches the
    earlier attempt's run from the request: a failed run, or one a dead worker
    left running, which is marked interrupted. That keeps "one run per request"
    true for the queries that join on it.
  - An executor names the failure codes it may retry (`retryable_codes`), and
    `KindDispatchingExecutor` forwards them per job kind. A kind that names its
    codes retries nothing else, in execution or publication. That matters
    because `SafeJobError` defaults to `retryable=True`: with 3 attempts, a
    stale-connection fence would otherwise requeue.
  - Turn jobs get 3 attempts. Only `provider_busy` (a 503 caused by
    `RateLimitError` or `TransientProviderError`) retries, after
    `max(backoff, Retry-After)`, with Retry-After honoured up to 60 s.
  - The final failure says "The advisor is busy right now. Try again in a
    minute."
  - Artifact runs use the shared provider wording.
  - The `/api/query` "check provider settings" cases are still waiting on QA's
    status codes.
- **Phase 3:** the likely cause of run `f057acea`'s 422 was salvage, not the
  gate. Salvage returned nothing when its only kept block was a correct refusal
  that already named the fake code, because no fact block survived and no
  "There is no …" block had to be added. A kept refusal now counts as the
  answer. A code the run's own evidence names is never treated as absent, so a
  corpus without a master row for it isn't forced into "does not exist" wording.

Phases 4–7 are done, uncommitted (prompt `research-49`):

- **Phase 4:** the `renamed_subjects` prompt line now says QTM 100 is DATASCI
  100, and the "prove no substitution" clause is gone. `test_programs.py`
  already pins the item mapping, so there's no new row.
- **Phase 5:** there were two causes.
  - Term listings keep the code the Atlas printed that term. Fall 2025 lists
    `QTM 190`, which the master calls DATASCI 190. `_offerings` now asks for
    both codes and folds each listing back to the current one.
  - A master row the import keeps `current` is never called no longer
    offered. One check decides it for the offering note, the answer gate and
    suggestions.
  - Retired master rows are real courses. The Phase 3 check no longer calls
    them nonexistent.
  - The gate accepts "has not been offered since". It no longer tells the
    model to drop a requisite alternative.
  - The test fixture also exposed a SQL bug. `research_course_offering_terms`
    joined snapshots on `source_id` alone, so a source holding several term
    snapshots gave every row every term. It now joins on
    `(source_id, capture_set_hash)`.
- **Phase 6:** editions are compared only within one `source_category`, and
  term listings (`course_offerings`) are skipped.
- **Phase 7:**
  - On a grading-basis question, `read_program` adds up to 6 of the program's
    own S/U, letter-grade or pass/fail items to the page, wherever they sit.
  - One prompt line gives program rules precedence over College defaults and
    says "above" is not "at least".
  - A fact block with a number next to hours, credits or GPA must cite a
    passage stating that number, in digits or words. Clauses that say the
    evidence doesn't establish something are exempt.
  - `advising_sweep` has no fixed list, by design, so step 4 doesn't apply.
- **Prompt budget:** the 15000-byte rows of
  `test_context_compaction_preserves_exact_passages…` sit within about 20
  bytes of the research prompt's size. A prompt edit that grows it drops every
  exact passage there. Keep prompt edits byte-neutral, or recalibrate that row
  on purpose.

Phases 8–9 are done, uncommitted. No research prompt change, so it stays
`research-49`.

- **8.1:** a research run's empty-evidence refusal now comes after the
  database slots. Retrieval runs keep their early check. Still unknown: why the
  plan_fresh-1 run stored no evidence. QA's diag would show it.
- **8.2:** a plan note that names a course, or that isn't copied from a slot,
  is dropped in the same call instead of refused.
- **8.3:** a table header written as a number becomes text, and a lone
  `source_slots` number is wrapped in a list. An over-long plan note is cut at
  a word boundary. Cells were already coerced. Without QA's
  `artifact/live-mm/run0/*.diag.json` the other refusals weren't matched
  one by one.
- **8.4 and 8.6:** code decides both. A question naming a code the master says
  doesn't exist gets no artifact. With an artifact attached, "export",
  "download", "convert", or "as a Word/PDF/Excel file" gets no new run. The
  prompt names neither, because the compaction test leaves no prompt bytes
  spare.
- **8.5:** the prompt already said "say it is being made". A reply that asks
  for an artifact and also says "here's your …", "is ready" or "I've made …" is
  now refused. The frontend run card already says "Couldn't make this. Try
  again." and offers Retry.
- **8.7:** the summary export writes a code the current Atlas lacks as "a
  course not in the current Course Atlas". This covers turn text, findings and
  table cells, in any spelling (`PHYS 444`, `phys444`, `Physics 444`).
- **8.8:** a request naming only "minor", or only "major", must get that kind
  of program from the outline and the plan. The section message now says never
  to write about print layout.
- **Phase 9:**
  - EMO-059: `--cases` now defaults to `DATA_DIR/eval/retrieval.jsonl`.
  - EMO-071: one ASGI middleware returns 422 for a NUL in a JSON body string
    or key, or in a query string. Uploads aren't read by it.
  - EMO-070: the stored error is the pipeline's own `ValueError` text, or
    "The file could not be read or indexed." The full exception is logged.
  - EMO-074: a stopping worker whose drain runs out releases its leases
    through lease recovery. `--once` recovers expired leases first.
  - `ECON _OX 100`: the passage reader joins the stray space. The parser
    already did.
  - Exports: the Sources lists in Markdown, HTML, Word and the workbook show
    each source name and locator once, with no element or chunk ids.
  - Suite: `completed_scheduled` counts only codes in a term heading's own
    list or table.
  - EMO-060: the README counts and `.env.example` research defaults match
    the development database and `config.py`.

New decision for Riley: **deleting a failed upload (EMO-070).** No route
deletes any source. Adding one means choosing what happens to evidence and
citations that point at it, because history is kept for evidence.

Drop-deadline conflict for Riley (Phase 6), current sources in the snapshot:
- `emory-policy-changing-courses.md`: "A student may change courses within the
  first eight days of the semester (the Add/Drop/Swap period). After the
  conclusion of the Add/Drop/Swap period, students may petition to drop a course
  through the fifteenth class day of the semester."
- `academic-policies-and-requirements.md`: "A student may change courses within
  the first two weeks of the semester (the Add/Drop/Swap period). After the
  conclusion of the Add/Drop/Swap period, students may petition to drop a course
  through the twenty-fifth class day of the semester."

Not yet checked live: everything. Ask the QA bot to rerun NX, the
"with 88"-style items, the `placeholder` items (HP04, R4_adv_ED08,
R4_profiles_PF03-t3), and 10 conversations at 8 in flight.

Every cause below was checked against the code at `1d0ee54`. File references
are to that commit. Where the code says something different from the report,
the plan follows the code and says so ("Report correction").

## Rules for whoever implements this

- Work phase by phase. Each phase is one commit with its tests. Run `ruff check .`
  and the whole `pytest` suite before each commit.
- Tests follow AGENTS.md: add rows to the existing table for the behaviour, and
  use `tests/fakes.py` and `FakeClock`. Don't test prompt text, constants or
  manual scripts. Every new row must fail before its fix.
- Never edit `.env`. Never add a model call to the prerequisite graph, the
  requisite parser or any program data.
- Don't change the benchmark cases, the K-checks in
  `scripts/manual/artifact_benchmark.py`, or acceptance in
  `app/artifacts/checks.py`. Items that would need one are under
  "Decisions for Riley" and are not part of this plan.
- Don't change `configs/source_conflicts.toml`, `requirement_gold.toml` or other
  hand-written data without Riley's review.
- Generation stays greedy. Don't change models or sampling.
- Bump `RESEARCH_PROMPT_VERSION` (`research_final.py`) once in any phase that
  edits the research prompt.

---

## Phase 1: the research answer guard (EMO-065, High)

**Finding.** The research path publishes "." and "placeholder", system-prompt
echoes, phishing text and secret-shaped strings. Live, 3 answers were the single
word `placeholder` (HP04, R4_adv_ED08, R4_profiles_PF03-t3).

**Cause (verified).** `validate_final` (`app/generation/research_final.py`,
from line 97) checks block limits, evidence references, candidates, raw
markers, course identity, fact support, entry claims, recommendations,
preparation claims and retired courses. It never looks at the content itself.
`validate_decision_limits` (`app/advising/research_types.py:333`) checks only
maximum sizes. The legacy echo guard was deleted with `answer.py`'s answering
path (`19f80e5`). Artifacts still run `leak_matches` (`app/artifacts/checks.py:243`).

**Change.** Add `answer_content_problems(decision, *, system_prompt, evidence_text)`
in `research_final.py` and call it from `validate_final`. `salvage()` validates
each block through `validate_final`, so salvaged replies are held to the same
rule. Return a problem string for each of:

1. **No content.** The decision's visible text has fewer than 12 letters, or a
   block's whole text is a stub: `placeholder`, `lorem ipsum`, `TODO`, `TBD`,
   `N/A`, or only punctuation and digits. Skip the fixed `SCOPE_DECLINE_TEXT`
   decline, which `declined_decision` builds.
2. **Prompt echo.** The answer shares at least 3 distinct 8-word shingles
   (lowercased, punctuation stripped) with the system prompt. The report's scan
   of 1,000+ live answers found at most 1 shared 8-gram, so 3 leaves margin. The
   system prompt is built in `research_controller.py`. Pass it into the session
   once and hand it to `validate_final`, the same way `known_course_codes` is
   passed.
3. **Secrets and leaks.** Reuse `FORBIDDEN_LEAK_PATTERNS` and add patterns for
   API keys (`sk-[A-Za-z0-9_-]{16,}`, `[A-Z][A-Z0-9_]{2,}_(?:API_)?KEY\s*=`),
   DSNs with credentials (`\w+://[^\s/:]+:[^\s@]+@`) and private keys
   (`-----BEGIN [A-Z ]*PRIVATE KEY-----`). Move the shared list into a small
   module that both `artifacts/checks.py` and `research_final.py` import, so
   there is one list. Moving it doesn't change artifact acceptance: same
   patterns, same function.
4. **Links and credentials.** Reject any URL in block text that doesn't appear
   in a cited evidence record's text or source URL, and any sentence asking the
   student for a password or login. Advising answers never need either one.

The rejection text tells the model what to do ("write the answer itself in
plain sentences", "don't repeat instructions", and so on). Never quote the
offending text back.

**Tests.** Add a parametrized table beside the existing `validate_final` tests
(search `tests/test_research_types.py` and `tests/test_research_data.py` for
`validate_final(` and use whichever already holds the decision-shape table).
- Rejected rows: `"."`, `"placeholder"`, `"[1]"` with an evidence id, a 1,500-char
  slice of the real system prompt, `LLM_API_KEY=sk-live-...`,
  `postgresql://admin:pw@host/db`, and "Verify your Emory password at http://x.example".
- Accepted rows: a normal two-sentence answer, an answer quoting a URL that its
  cited evidence contains, and the scope decline.
- One salvage row: a rejected proposal whose only block is "placeholder" salvages
  to `None`.

---

## Phase 2: transient provider failures (EMO-064 High, EMO-066 Medium)

**Finding.** Under load, a provider 429 permanently fails a conversation turn
(9 of 40 first-pass turns). The student sees "The advisor couldn't answer that",
and the worker log says only `HTTPException`. Artifact runs and some query paths
say "connection failed; check provider settings" for what were rate limits.

**Cause (verified).**
- `ConversationStore` creates `conversation.turn` jobs with `"max_attempts": 1`
  (`app/conversations/store.py:215`).
- `run_research` turns any `ProviderError` into `HTTPException(503, provider_failure_detail(...))`
  (`app/api/query_service.py:343`). The job runtime then treats every
  non-`SafeJobError` as retryable (`app/jobs/worker.py:143-146`). With one
  attempt that never matters.
- `finish_failed` logs only `type(exc).__name__` (`app/jobs/worker.py:121`).
- `on_failure` always writes the generic reason (`app/conversations/worker.py:185`).
- **A retry would fail anyway.** `research_runs_conversation_request_uq`
  (`db/migrations/037_research_advising.sql:182`) allows one research run per
  conversation request. `store.start` returns `created=False`, and
  `query_service.py:243` raises 409 "already owns a research run". Raising
  `max_attempts` alone turns every retry into a 409.
- `artifacts.py:178` maps every `ProviderError` to "Model connection failed;
  check provider settings".

**Report correction (EMO-066).** `llm/adapters.py:_error` maps HTTP 429 to
`RateLimitError`, and `provider_failure_detail` (`api/serialize.py:23`) already
says "rate-limited" for that. So the `/api/query` "check provider settings"
cases came from a status other than 429, most likely a 400 that Ollama sends
when it throttles. That becomes `InvalidRequestError`. Before changing the
classification, get the status codes from QA's `model_calls` rows for those runs.
Don't treat 400 as a rate limit on a guess.

**Change.**
1. **Migration `089`:** make the index partial so a failed or interrupted run
   no longer blocks a new one for the same request:
   `... WHERE status NOT IN ('failed', 'interrupted', 'cancelled')`. Check the
   status values `ResearchStore.finish` writes. Check every query that loads "the
   run for a request" (`grep -n conversation_request research_store.py` and the
   `.sql` blocks) still picks the live or newest run.
2. **Turn executor:** decide what is retryable, explicitly. In the conversation
   executor, catch `HTTPException` around the research call:
   - a 503 whose `__cause__` is `RateLimitError` or `TransientProviderError`
     becomes `SafeJobError("provider_busy", <the detail>, retryable=True,
     retry_after=<exc.retry_after>)`;
   - every other failure becomes `SafeJobError(..., retryable=False)`, so
     raising the attempt limit retries nothing else.

   Then set turn jobs to `max_attempts=3`.
3. **`SafeJobError.retry_after`:** add an optional field. `_finish_failure` uses
   `max(retry_delay(attempt), retry_after)` for `retry_at`, capped at 60 s for
   turns.
4. **Reason text:** if the final attempt fails with `provider_busy`, `on_failure`
   writes "The advisor is busy right now. Try again in a minute." Everything
   else keeps the current text.
5. **Logging:** `finish_failed` logs `exc.code` and `exc.safe_message` for a
   `SafeJobError`, and `status_code` and `detail` for an `HTTPException`. Both
   are server-written text. Never log `str()` of other exceptions, which may
   carry model output.
6. **EMO-066 wording:** move `provider_failure_detail` from `api/serialize.py`
   to `app/llm/base.py` so generation doesn't import from `api/`. Use it in
   `artifacts.py:178` with subject "The model". Keep the `SafeJobError` code
   `provider_failed`.
7. **Leave out:** artifact-job retries. The tool ledger binds an operation to
   one scope fingerprint, and an artifact rerun needs its own review.

**Tests.**
- In the existing job-runtime retry tests (`tests/test_jobs*.py`), add rows:
  - `provider_busy` with `retry_after=7` is requeued at ≥7 s on `FakeClock`;
  - `retryable=False` is not requeued.
- In the conversation worker tests, a turn whose first attempt raises a
  rate-limited 503 (`FakeProvider` scripted to raise `RateLimitError` once)
  answers on attempt 2 and has one non-failed research run. A turn that keeps
  failing settles with the busy text.
- The migration is covered by the existing schema tests
  (`test_db_migrate.py`, `schema_baseline`).

---

## Phase 3: refusals of courses that don't exist (EMO-072 and EMO-063, Medium)

**Finding.**
- Correct refusals ("ENVS 667 does not count toward the CS BS") are rejected,
  so 4 of 70 phantom-course questions end in 422.
- 28% of research runs had at least one rejected finish.
- Separately, "Can I declare CS with 88 credit hours?" opens with "There is no
  course called WITH 88". 33 live answers start with such a sentence.

**Cause (verified). Two gates contradict each other.**
- `ResearchTools.finish` (`research_tools.py:1266-1270`) rejects a finish that
  doesn't name each `unknown_question_codes()` entry ("say so plainly, naming it").
- `validate_final`'s identity check (`research_final.py:141-158`) rejects any
  fact or limitation clause naming a code outside `established`, unless the
  clause matches `NONSUPPORT_RE`. `established` is the evidence plus
  `known_course_codes`. A fake code is never in it.

So the model must name the code, and may name it only in `NONSUPPORT_RE`
wording. "Does not count", "isn't a current course" and "doesn't exist" are not
in that list (`generation/answer.py:34-49`).

**Cause of EMO-063 (verified).** `_resolve_subject` (`retrieval/seams/prereq.py`)
accepts any 2–7-letter word except a short denylist (`_PROSE_SUBJECTS`, line 35).
"WITH", "TOP", "HAVE", "TAKEN", "DONE", "MORE", "NOW" and "ANOTHER" pass.
`course_codes_in_query` (line 101) feeds `unknown_question_codes`
(`research_tools.py:1360`), and the finish gate then *requires* the sentence.

**Change.**
1. **One rule for codes the master settles as absent.** Pass
   `unknown_question_codes()` into `validate_final` as `absent_codes`.
   - In both the identity check and the fact-support check, a clause naming an
     absent code passes when it also says the course doesn't exist. Use a new,
     narrow regex: `does ?n[o']t exist|no such|not (?:a|an) (?:current )?(?:emory )?course|isn't (?:a|an) (?:current )?course|there is no|not in the course atlas|not a real course`.
   - A clause that names an absent code *without* saying so is rejected with
     "{code} is not a course; say it does not exist".
   - Don't widen `NONSUPPORT_RE` for real courses. "BIOL 141 does not count
     toward X" with no evidence is still a requirement claim and must stay gated.
2. **Real subjects only.** In `unknown_question_codes`, keep a code only if its
   subject is a known subject in the collection, or the student wrote the
   subject in capitals. Known subjects are the distinct subjects of
   `_known_course_codes()`; derive them in Python, no new query. "ENVS 667" stays
   (real subject, fake number). "XYZ 101" stays (written in capitals). "with 88"
   and "top 10" go.
   - Don't change `_resolve_subject` itself. It also serves retrieval, where an
     over-match only costs a search.
3. **Why salvage didn't save run `f057acea`.** Salvage adds a "There is no
   {code} in the Course Atlas." block for each unknown code
   (`research_tools.py:1328-1331`), so this phantom question should have been
   salvaged.
   - Reproduce with `FakeProvider` scripted the way QA's run went: refusal
     rejected, then JSON in prose, then the identity rejection.
   - Find which exception reached `ResearchController.run` with
     `session.last_call is None`, or which type it raised that the salvage path
     doesn't catch.
   - Fix that path.

**Tests.**
- In the existing finish/identity table, add rows:
  - "ENVS 667 does not exist in the Course Atlas, so it can't count toward the CS BS" is accepted;
  - "ENVS 667 counts toward the CS BS" is rejected;
  - "BIOL 141 does not count toward the CS BS" with no evidence is still rejected;
  - for the question "Can I declare CS with 88 credit hours?", `unknown_question_codes()` is `{}`;
  - for "Is XYZ 101 real?" it is `{"XYZ 101": [...]}`.
- One scripted-provider row for the f057acea sequence ends in an answer, not 422.

---

## Phase 4: the QTM→DATASCI rename on the answer path (EMO-075, Medium)

**Finding.** "Can DATASCI 100 count toward the Psychology BA?" gets the answer
"No, the program lists QTM 100". The catalog still writes `QTM nnn`, and the
master has only DATASCI rows.

**Cause (verified).**
- Code already treats the rename as the same course: `normalize_code` maps QTM
  to DATASCI (`atlas/records.py:62-72`), and `canonical_codes_in("QTM 100")`
  returns `DATASCI 100`.
- `read_program` returns each item's `mentioned_codes` as DATASCI, plus
  `renamed_subjects` (`research_tools.py:807-815`).
- The research prompt contradicts this. Line 92 of `research_controller.py`
  says "Search both former QTM and DATASCI names; renaming or number changes
  prove no substitution". Line 103 says "Use the new code; the catalog's old
  one is only its wording".

The model follows line 92.

**Report correction.** The artifact outline gate does not have this bug.
`_match_program` collects allowed codes with `canonical_codes_in`
(`artifact_pipeline.py:497`), which already renames. So "DATASCI N is not named
by DATA SCIENCE (BS)'s requirement lines" means that DATASCI code really is not
in the lines. Don't change the gate.

**Change.** Reword line 92: a subject rename listed in `renamed_subjects` is the
same course under its new code. Only a number change, or a different course,
needs a stated equivalence. Keep the instruction to search both names. Bump the
prompt version.

**Tests.** None for the prompt text (AGENTS.md). If no row already covers it,
add one to the `read_program` tool-result table: an item reading "QTM 100" has
`mentioned_codes == ["DATASCI 100"]` and `renamed_subjects == {"QTM": "DATASCI"}`.
Check it live with PI031, PI059 and TY10 from the report.

---

## Phase 5: "no longer offered" for courses that are current (EMO-073, Medium)

**Finding.** Seven answers call an Oxford alternative from requisite text
(`CHEM_OX 141`, `MATH_OX 207`, …) "no longer offered", and some drop it from
the prerequisite list. PN026 says DATASCI 190 is no longer offered, though its
master row is `lifecycle='current'` with Fall 2025 evidence.

**Cause (verified).** `_retired_courses` (`research_tools.py:958-977`) treats as
retired any named, known code that no checked term listing shows. It never reads
the master's `lifecycle`. `retired_course_problems` (`research_final.py:58-84`)
then forces "no longer offered" wording, or the code's removal.

**Change.**
1. A code whose master row is `lifecycle='current'` is never retired, whatever
   the term listings say. Get lifecycle with the same query that fills
   `_known_course_codes`, or add a `-- name:` block beside it. No inline SQL.
2. A code named only as an alternative in requisite or program text, with no
   offering in checked terms, is "not offered in any checked term" rather than
   retired. The gate accepts that wording as well as "no longer offered", and
   it never asks to drop a code from a requisite list. Accept "not offered in
   any checked term" and "has not been offered since" in `_RETIRED_SAID`.
   Change the problem text to: "say it is not offered in any checked term; a
   student who completed it still meets the requirement".
3. Recommending such a course stays refused, as now.

**Tests.** In the existing `retired_course_problems` and `_retired_courses` rows:
- a current master course missing from the listings is not retired;
- a requisite-only Oxford code named with "not offered in any checked term" passes;
- a recommended retired course is still refused.

---

## Phase 6: the editions banner (EMO-069, Medium) and real conflicts (part of EMO-076)

**Finding.** "Caution: Cited sources contain conflicting editions" fires on 41%
of answers.

**Cause (verified).** `build_applicability_notice` (`advising/applicability.py:207-210`)
flags any two distinct `edition` labels. In the snapshot, every term listing
(`document_roles = ['course_offerings']`) carries its own edition label
("Spring 2027 · Oxford College", "Fall 2025 · Oxford College", …). Career
references ("May 2025") count as conflicting with the catalog
("Emory College of Arts and Sciences Catalog 2026-2027") too.

**Change.**
- Compare editions only among sources of the same category that are not term
  listings. Group by `source_category` and skip items whose roles include
  `course_offerings`. A different edition within a group is still a conflict:
  two catalog years, or two handbook versions.
- If the rows passed to `build_source_applicability_item` don't carry
  `document_roles`, add it to the query that loads them.
- Leave the cohort and superseded rules alone.

**Tests.** Add rows to the existing applicability table:
- three term listings plus the catalog: no conflict;
- catalog 2025-26 plus catalog 2026-27: a conflict;
- a career reference plus the catalog: no conflict.

**Real conflict found by QA.** The two policy files disagree on the
drop-petition deadline (15th vs 25th class day) and the Add/Drop length. That
belongs in `configs/source_conflicts.toml`, which is hand-written. List the two
sources and their exact sentences for Riley to confirm. Don't add the entry.

---

## Phase 7: department rules lose to the College default (EMO-076, High)

**Finding.** Confident Yes/No answers contradict the deciding sentence: Biology
letter-grade rule, Chemistry S/U, first-semester declaration, "above 3.5" read
as "at least", the AP cap of 12, two majors plus a minor, and the CS minor S/U
rule (CV02-t5).

**Cause.** Retrieval and reading, not a single line of code. The generic College
policy chunk is read and the program's own rule is not, or a comparison word is
read loosely.

**Change (deterministic first, prompt second).**
1. **Grading-basis rule lines.** When the question asks about S/U, pass/fail or
   letter grades and a program is pinned, `read_program`'s result should carry
   the program's grading lines.
   - Match items and fields against `letter grade|S/U|pass/fail|satisfactory`.
   - Return them as exact evidence even if they're beyond the first page of
     items, the same way `_open_courses` auto-reads course records without a
     model round.
   - This is a retrieval aid built from the program's own lines. No model writes
     it.
2. **Precedence in the prompt.** One rule: a program's or department's own rule
   on grading basis, declaration or limits overrides the College default. When
   both are read, the answer gives the program rule. Comparisons must be
   quoted: "above" isn't "at least".
3. **A decline-to-guess rule** for numeric policy claims. A fact block that
   states a cap, a minimum or a GPA threshold must cite the passage containing
   that number. Check this the way `_INCLUSION` does: the number in the block
   must appear in the cited text. This catches "24 hours" when the cited text
   says 12. Build it as a narrow check (numbers next to "hours|credits|GPA")
   with its own table. If it rejects more than a few correct answers in the live
   check, drop it.
4. **Live regression bank.** Add the 6 R4 items plus CV02-t5 and FK15 to
   `scripts.manual.advising_sweep`'s fixed cases, if it has a fixed list. It's a
   manual script, so it gets no tests.

**Tests.**
- A grading-basis question over a fake program whose only "letter grade" line
  is item 14 (past the first page) returns that line as evidence.
- Number check rows: "the cap is 24 hours" citing "12 hours" is rejected;
  "the cap is 12 hours" is accepted; a block with no policy number is unaffected.

---

## Phase 8: artifact runs that waste their calls (EMO-068, EMO-067)

**Findings and verified causes.**

| # | Symptom | Cause | Change |
|---|---|---|---|
| 8.1 | `no_grounding` "The research run has no evidence to ground this artifact" after research answered (plan_fresh-1) | `_ground` (`generation/artifacts.py:236-242`) refuses when the research run's stored evidence is empty, *before* `add_database_slots` adds the program lines and course records that would ground it | Move the empty check after `add_database_slots`; refuse only if the slots are still empty. Then find out why an answered run stored no evidence (an answer from program fields only?) and note it here |
| 8.2 | 13 refusals "Copy the note from a supplied slot or leave it empty", each costing a call from the shared 40 | `stage_note` (`artifact_pipeline.py:916-923`) refuses an optional note that isn't verbatim slot text | Accept the call and drop the note (empty note, no slot). Same for a note containing a course code. The note is optional, so dropping it loses nothing a student needs |
| 8.3 | Cells not strings (6), `source_slots` not a list (2), note too long (3) | Remaining schema nits after `9d185f6`/`1d0ee54` | Look at the exact refusals in QA's `artifact/live-mm/run0/*.diag.json` (ask the QA bot for them). Coerce numbers to strings in cells; wrap a single slot number in a list; truncate a too-long optional note at a word boundary. Use `repair_arguments` where it fits |
| 8.4 | A fake-course request ("Make a plan that includes MATH 967") still requested an artifact | `_with_requested_artifact` (2.1, `research_controller.py`) adds the request even when `unknown_question_codes()` is non-empty | Don't add the artifact when the question names a code the master says doesn't exist. Research answers "MATH 967 doesn't exist" |
| 8.5 | The chat says "Here's your checklist" when the run failed (CV04-t5) | The answer is published before the artifact run, and the model writes as if it's done | Research prompt: when requesting an artifact, say it is being made, never that it is ready. Also check the frontend artifact card shows a failed run plainly |
| 8.6 | "Export it to Word" started a new run that failed | The handoff treats export as a new artifact | When the turn references an existing artifact and asks to export, download or convert it, request no artifact. Answer with the panel's export option. Code decides this in `_with_requested_artifact`, the same way it reads `_ARTIFACT_ASK` |
| 8.7 | The summary path fails K4 on codes in the saved turns (the 2026-11-10 five-case random check: G11-summary-1, PHYS 444/499) | `build_advising_summary_spec` is built in code with no repair round | Run the same `not_in_course_atlas` filter on the summary's text. A code the master doesn't have is written as the course title from its slot, or dropped from that line |
| 8.8 | compare-4 compared the Applied Math BS when the student asked about the minor, and wrote "Print readiness…" into the document | Program choice in the outline; meta text from the section prompt | Outline gate: when the question names "minor" or "major", the chosen program's kind must match. Section prompt: never write about print layout. Check whether `_SECTION_RULES` invites it |

**Tests.** Add rows to the existing tables in `tests/test_artifact_generation.py`
and `tests/test_native_artifact_tools.py`:
- 8.1: empty research evidence plus a named program produces slots;
- 8.2: an off-slot note is accepted as empty;
- 8.4: a fake-course plan request gets no artifact;
- 8.6: an export request with an artifact reference gets no artifact;
- 8.7: a summary naming PHYS 444 doesn't publish the code.

8.5 and 8.8 are prompt text: no tests.

---

## Phase 9: smaller fixes (Low)

| ID | Change | Where (verified) | Test |
|---|---|---|---|
| EMO-059 | `--cases` defaults to `settings.data_dir / "eval" / "retrieval.jsonl"` | `scripts/eval_retrieval.py:24-25` (`REPO_ROOT / "data"`) | none (script) |
| EMO-071 | Reject `\x00` in every request string with one shared validator, giving a 422 | `api/schemas.py` request models; the 500 came from `retrieval/trace.py:34` and `conversations/store.py:51` | a row in the existing request-bounds table |
| EMO-070 | Store a safe message, not `type(exc).__name__: exc`, and let a failed source be deleted | `ingest/pipeline.py:145` | rows in the existing upload-failure test |
| EMO-074 | On stop, release the leases of jobs that didn't finish (so they requeue at once), and run `recover_expired` in `--once` | `scripts/worker.py:151,190`; `install_stop_signals` | a row in the worker stop test with `FakeClock` |
| Data | Normalize `ECON _OX 100` (stray space) to `ECON_OX 100` in the requisite parser | `app/atlas/requisites.py` (deterministic) | a row in the requisite parser table |
| Exports | `.md`/`.html` "Sources" lists show block ids and source UUIDs. Show the source name and locator only | artifact export renderers | a row in the export test |
| Suite | `completed_scheduled` matches a mention, not a term slot | `scripts/manual/artifact_suite.py` | none (manual script) |
| EMO-060 | Update the README corpus counts (593 sources, 374 current, 155 programs, 6,014 courses / 4,969 current) and make `.env.example` match `config.py`'s research defaults (360 s, 10,240, 52,000) | `README.md`, `.env.example` | none |

---

## Not in this plan: decisions for Riley

1. **EMO-052, a deleted user can never return.** The workspace key is a
   function of the identity, and the deletion tombstone is permanent by design:
   it fences late jobs. Letting them return means giving workspaces a generation
   number so a new login starts a new workspace. That's a privacy design call.
2. **EMO-061, the quota seed.** 1M tokens/day for the whole deployment is about
   53 research questions. Choose a per-student budget, and decide whether the
   reset time is shown in local time.
3. **EMO-038, holds never surfaced.** A curator hold on MATH 412 should put a
   review notice in the answer. This needs a rule for how a hold changes an
   answer (withhold, or caveat).
4. **Suite K1 scope (EMO-074).** K1's `queue_empty` counts open jobs in the
   whole workspace, so one orphaned job fails every case. Scoping it to the
   case's own jobs is a K-check change.
5. **Model baseline.** QA recommends re-baselining the live tests on
   mimo-v2.6-flash (93% vs minimax's 85% on the same items, 2.4× faster).
   minimax passed 10/23 artifact cases against mimo's 0/8.
6. **The drop-petition deadline conflict** (Phase 6) for `source_conflicts.toml`.
7. **EMO-062/EMO-013 program data:**
   - "Dance and Movement Studies (Minor)" 19 h / 12 courses;
   - French Studies minor 29–31 h for 5 courses;
   - History minor `courses_required: null`;
   - Chinese Studies minor 18–36 h;
   - the Classics page carrying Chemistry blocks;
   - "Mathematics 107" in the Anthropology & Human Biology BS.

   Each needs the source checked by hand, then a parser fix or a manual fix,
   never a model.
8. **The overhedging family (EMO-034, most of the 89 FNs).** The program table is already a
   research tool (`list_programs`, `read_program`). The losses are runs that
   stop before reading it, or that answer from too little on short follow-ups.
   That's a research-loop design question (reading budget, how follow-ups carry
   the resolved program forward), not a bug fix. Plan it after Phases 1–7 land
   and the R4 set is rerun.

## Order and check

Phases 1, 2 and 3 first: they are the two Highs plus most of the 422s and extra
round trips. Then 4–7, then 8, then 9.

After each phase, run the offline gates. After Phases 1–3 and again after 8,
ask the QA bot to rerun:
- NX, PQ and R4 for Phases 1–7;
- 10 conversations at 8 in flight, for Phase 2;
- the artifact suite, for Phase 8.

Compare against this report's numbers. Record each phase's result at the top of
this file the way `artifact-suite-fixes.md` does.
