# LLM quality fixes: handoff and remaining plan

Updated 2026-10-22 ET / 2026-10-23 UTC. Work stopped at the user's request because
usage was exhausted. This is a continuation plan, not a claim that every QA gap
is closed. Do not start additional model runs until the user resumes the work.

## Implementation status (2026-10-22 ET)

The final validation delta in section 1 is closed on the current provider:
4/4 profile probes accepted, `no-invented-graph` now publishes a safe answer,
the AI minor read all 12 categories, Data Science BS read 38/38 items, the Data
Science minor checklist was correct, and `research_workspace --case
read-all-requirements` read 39/39. Two deterministic follow-ups landed: the
identity guard accepts the reproduced negative-evidence caveats, and a program
cursor paired with another program's handle returns scoped conflict feedback.
The broad adversarial sweep then ran the 25-case context matrix twice, all 18
catalog cases in baseline and tools, all 13 conversation turns in both modes,
and the 18 research cases in bounded batches. It exposed a policy-search focus
problem: research initially missed the S/U cap and the 4+1 rule that baseline
retrieval found. Query-dense discovery snippets, removal of the competing
document-start handle from matching-section leads, and a narrowed policy read
round repaired those cases; research prompt version is now `research-17`.
A follow-up wrong-answer hunt then targeted career and long-comparison prompts.
It caught a confident CS 326/CS 350 content swap, a false DATASCI 350 degree-
inclusion claim, and an ECON 112 “entry point” recommendation that contradicted
its own ECON 101 prerequisite. Prompt `research-17` plus a deterministic
fact-block code guard corrected the degree-inclusion class; a new
entry/starting-point guard blocks the ECON 112 claim, though one rerun returned
422 when the repair reused stale evidence IDs and a later run answered only
partially (40 of 65 program items). Career security improved to two recent runs
naming CS 453 and CS 573, but search instability is not closed. The
full backend suite passed 1,405 tests with two platform skips in 352 seconds.
The live evidence and remaining limits are recorded in
[the audit status](../audit/README.md).

Still open: the Data Science minor run unnecessarily opened the unrelated BS
projection and surfaced its partial coverage; research completeness stays
conservatively partial after a fully read fixture; and EMO-043, EMO-046,
EMO-035, EMO-044, EMO-047, EMO-038, EMO-049, EMO-012/EMO-024 and the broader
operational/product items remain as described below. This status does not turn
those into closed findings.

## Start here

1. Read `AGENTS.md`, [architecture.md](../architecture.md), and the existing
   [audit dispositions](../audit/README.md). Preserve the original
   [round 3 report](../audit/REPORT-round3.md) as the external observation.
2. Inspect `git status` and the relevant diffs before editing. The workspace has
   many uncommitted changes from both the earlier infrastructure review and this
   live-quality work. Do not reset, overwrite, or selectively discard them.
3. Finish the targeted validation below before broadening the implementation.
   There is already substantial passing evidence; avoid another unbounded round
   of model calls or repeatedly running the entire suite without a new reason.
4. Update the audit dispositions with the final evidence and remaining limits.
   The architecture has been partly updated; the audit README still mainly
   describes the earlier infrastructure pass and is stale about live validation.

The user asked to review the issues in docs, create increasingly difficult live
LLM tests, and then fix the reproduced failures. The latest instruction was to
leave a handoff document and plan. No commit, push, deployment, or external
message was requested or performed. No subagents were used for this fix pass.

## Environment and operational constraints

- Workspace: `C:\Users\moomi\personal_projects\Emory_STEM_Advisor`.
- PowerShell; use `login:false` with command tools. Python is
  `.venv/Scripts/python.exe`; backend commands run from `backend/`.
- The configured live provider during these checks was `openai-compatible`,
  model **LongCat-2.5-Preview**, endpoint `https://api.longcat.chat/openai`.
  The original round 3 report used **mimo-v2.6-flash**. These are therefore
  current-provider validations, not a same-model rerun of the original report.
- Keep credentials in local configuration. Never print `.env`, API keys, or
  connection secrets. Provider request IDs were null in the recent live output;
  normal application traces and accounting rows still exist in the dev DB.
- Use cached real Granite embeddings for catalog checks, with
  `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` to avoid downloads.
- Tests use disposable real Postgres. On this Windows setup, embedded pgserver
  requires an approved escalated command because the restricted token cannot
  start it. A skipped DB test is a failure. The two full-suite skips below were
  platform-specific symlink/POSIX-permission cases, not database tests.
- Live catalog diagnostics borrow the already-running dev database. Do not
  migrate, replace, truncate, or shut it down. The smoke harness reads the port
  from `data/pgserver/postmaster.pid`; a previously observed port was 60841,
  which must not be assumed stable. Never kill the dev server process tree.
- `research_workspace` owns a separate temporary fixture database and cleans it
  up after a normal Postgres shutdown. It uses a fake embedder and real LLM.
- Manual/smoke scripts stay outside pytest. Diagnostics go to stdout; do not
  create saved test-output reports unless requested. This handoff was requested.
- Do not write, infer, or fill prerequisite graph nodes/edges with an LLM. None
  of this fix pass changed real course/graph data. SQL stays in named query files.
- The last attempted live profile rerun was **not executed**: automatic approval
  review could not complete because the account hit its usage limit. This was a
  review failure, not a determination that the action was unsafe. Do not bypass
  the approval check.

## Changes already made

### Earlier infrastructure review

The existing audit README records the detailed dispositions and tests. These
changes remain in the same dirty workspace and must be preserved:

- Disposable test-target safeguards and separate Compose/CI test database.
- Worker maintenance connection handling and credential-preserving reconnects.
- Compatible-provider empty-choice errors, bounded upload/chunking failures,
  upload type/duplicate diagnostics, and login transport failures.
- Atomic/checksummed requirement-hold captures and release CLI; backup failures
  for corrupt archives and invalid hold pointers.
- Workspace-owned feedback revision chains and student-facing diagnostic
  redaction, including request-validation credential echo protection.
- Pool checks for dead idle backends and bounded database error responses.
- Literal system-prompt echo rejection; operator documentation updates.

That pass had 1,369 backend tests passing, two platform skips, Ruff, frontend
typecheck, 42 frontend unit tests, and Compose/CI YAML parsing. The current pass
has newer backend evidence below. It has not rerun the browser or Docker stack.

### Answering and conversation fixes

Files: `backend/app/generation/answer.py`,
`backend/app/api/query_service.py`,
`backend/app/api/routes/conversations.py`, and
`backend/app/retrieval/seams/prereq.py`.

- Course-code comparison tolerates printed digit spacing and wrapped subject
  names, preserving campus/suffix identities. It does not repair missing source
  digits or change stored records, parsed prerequisites, or graph edges.
- Explicit first-person completed-course reports can be acknowledged separately
  as unverified reports without claiming they satisfy institutional rules.
  Only the current user message and exact user history turns authorize this
  narrow identity exemption; assistant text and summaries do not.
- Negative-evidence exemptions are evaluated per clause rather than exempting
  an entire paragraph containing a separate positive unsupported claim.
- The existing single bounded citation rewrite can now repair course-identity
  failures. Hints identify which current passages name each code, while making
  clear that identity alone does not prove the surrounding claim. The repaired
  answer goes through the same validation; no citation is assigned automatically.
- Topic-switch handling takes precedence over pronoun continuation. Final
  generation receives the resolved follow-up question, rather than only the
  unresolved raw question. The original stored user message remains intact.
- Prompt guidance preserves the requested credential, requires named-program
  evidence for degree inclusion, and separates student reports from requirements.
- A hard control caught the model claiming that readiness for CS 171 makes
  CS 253 takeable. Prompt guidance now explicitly distinguishes satisfying a
  course's prerequisites from completing that intermediate course. Two live
  repetitions subsequently preserved the CS 171 step. This is prompt improvement,
  not deterministic proof of every generated eligibility statement.
- **Last local edit:** added reproduced negative-evidence wording (`names no`,
  `no supplied passage establishes`, `does not list`) to the existing identity
  guard, with rows in its existing regression. The 38 generation tests passed;
  the live rerun after this exact edit was blocked by usage exhaustion.

The answer prompt version is currently `10`.

### Retrieval fixes

Files: `backend/app/retrieval/seams/keyword.py`,
`backend/app/db/queries/keyword.sql`, `backend/app/retrieval/types.py`,
`backend/app/retrieval/fusion.py`, `backend/app/retrieval/retrieve.py`, and
`backend/configs/retrieval.toml`.

- Normalize S/U to the fixed indexed compound `satisfactory/unsatisfactory`.
  Searching only `unsatisfactory` did not match the actual PostgreSQL lexeme.
- Preserve numeric references such as `4+1`; expand AI/CS credential acronyms
  deterministically in search queries only.
- Rank adjacent title phrases, verified requested checklists, and exact bound
  named-program evidence ahead of generic repeated words. Program title matching
  uses a phrase, avoiding accidental matches to a Mathematics minor merely
  because a question mentions a Mathematics category.
- Mark exact bound program evidence and reserve its actual chunks before source
  diversity can strand a continuation. Ordinary queries keep the prior diversity
  allocation. Score floors, deduplication, source selection, workspace scope,
  current/indexed-source checks, and evidence bindings still apply.
- Planned retrieval also preserves exact named-program bound evidence from the
  raw keyword run. Other generic raw OR hits remain replaced by planned searches.
- Retrieval config version is `7`; chunker version remains `2`.

### Research fixes

Files: `backend/app/generation/research_tools.py`,
`research_controller.py`, and `research_final.py`.

- Policy discovery includes verified catalog/department/publication documents.
- Search leads point to matching sections, retain a beginning handle, and expose
  read offsets and preceding-unread scope. A long catalog's opening pages no
  longer stand in for the requested policy section.
- Exact document titles/filenames and lexically grounded leads outrank generic
  metadata matches. Program search also exposes verified requirement/checklist
  documents when no parsed program record exists.
- Opened program records with unread pages must continue while the existing
  operation budget permits. At the finish reserve, unread coverage forces partial
  metadata and an explicit read/total limitation. The reserve is not increased.
- A live deep-checklist run repeated a stale cursor. **Latest research edit:**
  continuation requests advertise current pending handle/cursor values and drop
  duplicate prior-page native outputs. The next live run opened **39/39 parsed
  items**, had no cursor left, and listed all 38 synthetic CS courses.
- A factual policy lookup had acquired the correct S/U passage, then dropped it
  after later broad reads. Finishing now restores up to four relevant passages
  already acquired in that run, ranked by unique question-term coverage weighted
  for rarity within the run. Candidate and executed-check support retain priority.
  This is bounded context selection, not entailment validation or a new authority
  gate. No new evidence is acquired by a model-proposed ID.
- The expanded system prompt initially displaced all exact evidence at the
  existing 9,000-byte test limit. Its wording was compacted; all 50 research-type
  tests, including exact-text and omission-accounting checks, then passed.
- Research prompt version is `research-17`; tools version is `8`.

### One deliberate corpus addition

Only the checksum-verified, already-allowlisted Data Science minor checklist was
prepared and published through the normal ingestion path:

- Filename: `emory-college-datasci-minor-checklist-undated.pdf`.
- Source: `984e2bba-2325-5952-a828-580f62750143`.
- Chunk: `6165b1a6-81cb-58a8-933c-4c2fc91a73b8`.
- Verified institutional-department provenance; undated edition and unknown
  applicability remain explicit. Blank total/elective-count fields were not filled.
- Preparation files and exact-SHA manifest are under ignored
  `data/staging/advising-fixes-20261023/`.
- The reviewed-upload check validated all 23 dispositions, of which 13 are include.
  The other 12 include documents were **not** blindly imported: some would replace
  already-bound program sources and need explicit reconciliation.
- The older QSS 2024 source remains historical/separate-origin evidence. No new
  parsed program projection or prerequisite graph was invented for this PDF.

## Validation evidence and limits

| Check | Observed result | Qualification |
| --- | --- | --- |
| Full backend suite | **1,380 passed, 2 platform skips**, 250.81 seconds | Snapshot preceded the final current-cursor schema change and last negative-wording edit. No DB skips. |
| Research data/types + generation focused suite | **156 passed**, 52.73 seconds | After current-cursor/schema change and prompt compaction; before the final negative-wording edit. |
| Final generation suite | **38 passed**, 1.22 seconds | After the last negative-wording edit. |
| Ruff and diff whitespace check | Passed earlier in this pass | Rerun once after final edits; do not imply a fresh check already occurred. |
| Controlled live context set | 25 cases: 23 accepted, 2 withheld; 30 calls | Acceptance is not a semantic grade. One accepted chain error led to the prompt fix; citation-999 and upload-conflict drafts were withheld. |
| Full live baseline catalog set | 18 cases: 17 answers, 1 refusal; 20 calls | Main retrieval failures repaired; graph-edit pressure was safely refused, but its draft also exposed a negative-comparison guard false positive. Some wording/scope defects remain. |
| Profile/topic-switch conversation | All 4 turns answered in the targeted repaired run; 6 calls | Withdrawal honored; final follow-up answered 4+1 six-credit rule rather than full-time enrollment. Not broad conversation certification. |
| Live research S/U cap | Correct 20 hours and ARCHE counting; 9 calls | After factual-evidence retention. Trace `130f71e0-86bc-44df-89d9-c338b015fb4f`. |
| Live research grading-basis deadline | Correct forty-third class day; 7 calls | Actual grading-basis passage, not withdrawal substitution. Trace `5bf725d6-bc59-466c-b467-c012a318fa49`. |
| Live research 4+1 | Correct ordinary six-credit cap and sequential bachelor's award; 5 calls | Trace `184e1299-0802-4fe6-8d38-3ffcee34af62`. |
| Live research AI/Data Science minor | Correct targeted list/checklist in repaired runs | Repeat after the latest continuation edit before broad closure claims. AI read 48/48 parsed items in an earlier repaired run. |
| Workspace recovery controls | 3/3 passed; 10 calls | Older evidence reopened, long student report recovered, empty-source fence preserved. |
| Synthetic deep policy | Passed; 7 calls | Retrieved final 17-hour/41st-day/5-credit/written-board-approval fixture rules, not real policy. |
| Synthetic exhaustive checklist, final run | Passed; 9 calls, 50,933 accounted tokens | Final prompt showed **39/39 items, next_cursor=null** and all 38 course codes. Final response remained conservatively partial at the finish reserve. |

The manual scripts are `backend/scripts/smoke/adversarial_advising.py` (new,
untracked), `research_workspace.py`, and `live_advising.py`. The adversarial suite
has 25 controlled cases, 18 real-corpus questions, and 13 conversation turns.
It prints rubrics, usage, latency, and provider request IDs; humans must grade
meaning and scope. No new saved results report was created.

The strengthened exhaustive-checklist smoke check now requires actual full page
coverage, not only all codes appearing in prose. An earlier run that listed all
codes but read only 32/39 items must not be cited as an exhaustive-read success.
All known long-running test/probe sessions had finished when this handoff was
written; the dev application/database was left running.

## Remaining work, in order

### 1. Close the final validation delta (closed 2026-10-22)

- Re-run the four profile-report probes (two phrasings, two repetitions) after
  the last negative-evidence wording change. Immediately before it, 3/4 were
  accepted; one correct caveat said the context "names no" reported course and
  was withheld. Confirm the fix accepts that caveat while rejecting a separate
  positive unsupported requirement in the same paragraph.
- Repeat the `no-invented-graph` catalog control. Its safe refusal was acceptable,
  but a correct draft's negative CS 170 comparison triggered the guard. Do not
  make user-mentioned codes globally acceptable or weaken suffix/campus checks.
- Repeat research `ai-all-electives`, `data-science-all-items`, and
  `ds-minor-list` after the current-cursor narrowing. Inspect actual read/total
  progress and final requirement content. Earlier correct results are useful
  evidence, but do not cover the final schema edit.
- Review both code and behavior for multiple pending programs: the narrowed
  schema currently advertises pending handles and cursors as separate enums.
  Confirm a mismatched pair receives scoped recoverable feedback and cannot
  authorize the wrong source. Add a behavioral row only if this reveals a gap.
- Run Ruff and `git diff --check` once. The full suite plus later focused suites
  cover the final code in pieces; rerun the full suite only if new changes or
  unresolved failures justify it.

### 2. Separate repaired failures from residual semantic errors

- **EMO-041:** the original alias/prose failures, OCR spacing, and narrow user
  reports are substantially repaired. Remaining natural-language caveats can
  still interact with the identity guard. Prefer small reproduced cases in the
  existing invariant test; do not turn the guard into a model-written authority
  or eligibility engine.
- **EMO-048 / EMO-007:** the reproduced topic-switch/follow-up chain is repaired.
  Broaden to correction, pronoun, and long-history controls using exact saved
  turns, retrieved context, and drafts. A lexical relevance score is not proof
  that an answer addresses the current question.
- **EMO-008 / EMO-009:** CS minor and S/U retrieval now reach the required text.
  Confirm source scope/edition and paraphrase consistency. A baseline S/U answer
  unnecessarily included withdrawal text; another described the cap as stated
  specifically in the ARCHE paragraph. Improve answer focus without inventing
  authority beyond the captured wording.
- **EMO-034:** exact named-program retrieval and enforced continuation now cover
  long lists. Verify comparisons, multiple candidates, low budgets, cancellation,
  and partial-coverage disclosure. Do not silently increase budgets or label
  unread lists complete. The final full-read fixture still reports partial due
  to reserve exhaustion; decide whether that conservative status merits a later
  narrowly scoped improvement, rather than clearing unrelated limits.
- The latest published profile conversation had a phrase suggesting a Course
  Atlas record established a student's completion. Atlas is not a transcript.
  Keep student report attribution explicit and inspect semantic drafts.
- The controlled upload-only answer repeated false uploaded rules with clear
  unverified attribution. It also unnecessarily labelled an upload assertion as
  a student completion report. Do not count either as verified institutional
  advice or claim the source-authority problem is solved.

### 3. Open issues from the original review

These are still open, not promised complete by this pass:

| Finding | Next action / completion evidence |
| --- | --- |
| EMO-043: vacuous/off-topic but cited answers | Literal prompt echo is blocked. Develop meaningful claim/support and current-question quality checks; test genuine counterexamples. Citation syntax and lexical overlap are not entailment proofs. |
| EMO-046: uploaded falsehood authority | Define behavior for upload-only claims and conflicts with verified institutional sources. Preserve provenance and historical evidence; do not make an upload official because it asserts authority. Test supported quotation/attribution separately from advice. |
| EMO-035: research deadline overrun | Short 12/40-second probes did not reproduce overshoot; the original 240-second/356-second observation remains unclosed. Reproduce with operation budget, per-call timeout, retries, transport behavior, and finalization timing; add fake-clock regressions where applicable. |
| EMO-044: synchronous ingest isolation | Size/chunk/cooperative limits are in place. Extraction/embedding still needs process-level isolation, a hard deadline, and abandoned-attempt recovery. Preserve failed-status and revision/publication fences. |
| EMO-047: orphaned synchronous turns | Dead pooled connections recover. Design durable pending-turn recovery after DB/process loss without automatically replaying an unknown provider outcome or charging twice. |
| EMO-038: held-source GUI observation | Existing held-source exclusion/conflict reason has a regression. Verify the actual browser flow; do not add another hold gate on assumption. |
| EMO-049: legacy hold captures | New captures are atomic/checksummed. Existing out-of-tree historical pointers require explicit verified relocation; released historical holds remain backup inputs. |
| EMO-012 / EMO-024 | Inspect the carried scraper/parser issue and current static-type errors from the report before choosing a fix. No closure or current error count was established here. |
| Remaining operational/product review | Quota charging policy, API-documentation exposure, frontend portability, Docker/browser validation, and broader deployment checks remain as documented. Do not equate offline backend success with production certification. |

The audit also retains DA-08 (centroid-gating design choice), broader no-LLM guard
coverage, one-sided contract-type coverage, and real-sleep cleanup opportunities.
The data review's counts/anomalies remain valid until its deterministic re-import
and sweep are deliberately rerun. Never hand-edit prerequisite nodes/mentions.
An earlier full-suite teardown took 2,360 seconds; the latest full run completed
in 251 seconds and did not reproduce it. Do not claim that intermittent cleanup
delay has been diagnosed.

### 4. Finish documentation and review

- Update `docs/audit/README.md` with the current-provider live evidence above and
  precise partial/open dispositions. Preserve original report text and avoid a
  single aggregate "pass rate" from accepted/answer outcomes.
- Update the architecture's research-continuation description for current-cursor
  narrowing and omission of duplicate native outputs. It already covers bound
  program retrieval, policy sections, checklist fallback, factual retention,
  explicit reports, and resolved follow-up generation.
- Review the final diff for source/workspace scoping, current-source/index
  revisions, query-plan behavior, bounded calls/tokens/time, and migration rules.
  No new baseline/applied migration edits are needed for this fix family.
- Summarize exactly which gaps are repaired and what remains. Do not claim all
  LLM semantic failures are filled. Leave a reviewable diff; commit/push only
  when requested.

## Resume commands

Run from `backend/`, with the local provider configuration already present.
Real-model commands use the network and may need the normal approval path.
Do not run them while usage remains exhausted. The examples are bounded; inspect
stdout and grade against each printed rubric.

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'

../.venv/Scripts/python -u -m scripts.smoke.adversarial_advising --stage context --case student-profile-guard --case profile-withholding-paraphrase --repeat 2 --max-calls 12 --token-budget 30000 --timeout 90 --max-output-tokens 4096

../.venv/Scripts/python -u -m scripts.smoke.adversarial_advising --stage catalog --mode baseline --case no-invented-graph --max-calls 4 --token-budget 25000 --timeout 90 --max-output-tokens 4096

../.venv/Scripts/python -u -m scripts.smoke.adversarial_advising --stage catalog --mode research --case ai-all-electives --case data-science-all-items --case ds-minor-list --max-calls 36 --token-budget 500000 --timeout 90 --max-output-tokens 4096

../.venv/Scripts/python -u -m scripts.smoke.research_workspace --case read-all-requirements --max-calls 30 --token-budget 1000000 --timeout 90

../.venv/Scripts/python -m ruff check .
git -c core.safecrlf=false diff --check
```

For a justified regression rerun, choose a fresh unique basetemp under data:

```powershell
$taskRunId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
../.venv/Scripts/python -m pytest tests/test_generation.py tests/test_research_data.py tests/test_research_types.py -q -p no:cacheprovider --tb=short --basetemp "../data/test-runs/pytest-handoff-$taskRunId"
```

Avoid enabling `--planning` on a broad live batch by default: the full planning
directory previously cost roughly 130,000 provider input tokens per case. Add
small explicit planning comparisons only when they resolve a particular gap.
Reproducible generation remains greedy; do not introduce sampling to chase a pass.
