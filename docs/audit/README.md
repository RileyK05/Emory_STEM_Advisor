# Audit status

Closed findings from the 2026-10-10 systematic audit and the 2026-10-20 code and
data scans, the items still open, and behavior a refactor must not "fix" away.
The original reports were removed once every confirmed finding closed; they are in
git history. [db-data-review.md](db-data-review.md) is the live data review and
stays until its R1–R11 acceptance pass is re-run.
[security-attack-notes.md](security-attack-notes.md) records the adversarial
input pass and its open denial-of-service limits.
[REPORT-llm-error-suite.md](REPORT-llm-error-suite.md) records the 58-probe
live LLM error suite and Course Atlas concordance across all STEM majors,
minors, divisions, electives, and policies (2026-10-28).
[TEST-SUITE-SPECIFICATION.md](TEST-SUITE-SPECIFICATION.md) defines the full 65-probe
test suite specification, exact query prompts, Course Atlas invariants, and pass/fail criteria.
[REPORT-round5.md](REPORT-round5.md) records the fifth round: one shared
course-identity reader for both answering paths, the research path's identity
check split into identity and claim support, the policy read widened to every
matched section, the gold set cross-checked against the Atlas capture, and one
chunking measure on every ingest path.
[REPORT-round6.md](REPORT-round6.md) records the sixth round: the Atlanta/Oxford
campus equivalence, stated by the database and derived from the Atlas itself.

## Round 6 (2026-10-31)

| Finding | Disposition |
| --- | --- |
| The database stated no Atlanta/Oxford equivalence | Fixed. Migration 079 adds `course_campus_equivalents`: one row per pair, with the basis on which the pair was established. `catalog_requirement` (101 pairs) reads the requirement texts, where Emory states the relation itself ("BIOL 142 or BIOL_OX 142"). `course_record` (48 pairs) reads the course records: same course number, compatible titles. The number alone proves nothing — `PE_OX 101` is "Weight Training/Conditioning" and `PE 101` is "Personal Health" — so the title has to agree. |
| The equivalence is not the retrieval deduplication key | Deliberate. `courses.canonical_code` deduplicates passages; an Atlanta/Oxford pair sharing it would delete one campus's passage, relabel the survivor, blind the answer's identity check to the campuses, and leave the research search unable to open the Oxford record. A test asserts the two keep their own anchor. |
| 406 unresolved prerequisite mentions and 21 program slots named an `_OX` code | Fixed for 355 mentions and 8 slots. A mention that names an Oxford course the master has no row for resolves to the Atlanta course it stands for, keeping its own text code, and the graph traverses either campus's code to the same course. The 51 mentions that remain name an Oxford course whose Atlanta counterpart is not a current course (`MATH_OX 117`, `FREN_OX 310`, `PE_OX 102`–`160`): a stale requirement, visibly so. |
| The planner treated a campus's course as a different course | Fixed. A completed campus equivalent satisfies the requirement: a student who took `MATH_OX 111` at Oxford has taken what a `MATH 111` requirement asks for. A selected campus equivalent satisfies a requirement that names the other campus's course, and the plan reports the student's own course. The equivalence changes what is satisfied, never what is planned. |
| The research identity check rejected a course the catalog publishes | Fixed. The collection's known courses now include the Oxford codes the catalog names, so an answer may name `BIOL_OX 142`. A course nothing publishes is still rejected. |
| The planner read two campus codes of one course as two alternatives | Fixed. "MATH 111 or MATH_OX 111" is one course at two campuses, so a student who selected either one was not blocked with `multiple_alternatives_selected`. The alternatives that name one course are collapsed. |
| The tools path was never measured | Measured. The clarity probe measured the legacy path with the tools off, which is not the development configuration. With the tools on, three probes ended in an HTTP 422 and the path scored 116/122 against the no-tools baseline's 126/126. The probe now has a `--tools` mode. |
| A spent tool budget killed the request | Fixed. The fence holds — none of the calls run — but the model is told the budget is spent and answers from the context and the tool results it already has, instead of the request dying with a 422. |
| A routing fallback found the durable tool budget spent | Fixed. The ledger reports that case as its own error and the controller answers it the same way: the fence holds and the operation still answers. |
| The tools path had no room for the answer's own retry | Fixed and documented. One operation call is reserved for the retry, and `OPERATION_MAX_CALLS` of five is what the tools path needs. The tools path went from 116/122 to 134/135. |
| The prompt-leak guard withheld correct answers | Fixed. It fired on a single shared eight-word stretch, and a careful answer often quotes the advisor's own caveat. It now counts eight shared stretches, so a quotation is not an echo and a wholesale reproduction still is; all four round-4 leak cases still fail. |
| The probe read the corpus with a different reader than the product | Fixed. A passage that writes "171Z" after "CS 171" names CS 171Z; the probe compared the text literally and flagged the answer as naming a course that exists nowhere, including in the passage that named it. Both sides now use the shared reader. |

## Round 5 (2026-10-30)

| Finding | Disposition |
| --- | --- |
| The identity gate withheld correct answers | Fixed. The gate compared the answer's canonical code against the passage's codes read by a weaker reader, so the corpus's own spellings (`Math_OX 112`, `Psyc_ox 110`, `BIOL 151or`) made correct answers look unsupported. One shared reader, `canonical_codes_in`, now reads both sides; it finds every code the requirement parser finds (1,257/1,257) and every code the previous reader found (0 misses, 16 gains). Live: 3 refusals and 3 malformed codes became 6/6 answers with the canonical code. A/B over 172 recorded answers: the previous reader rejects 9, the new one 7, and every changed verdict moves toward accepting. |
| The probe's `states-the-fact` check missed an honest answer | Instrument fixed. The check looked for "not" within three words of "listed/required/named/part of"; "do not establish it as a required core course" needs four. The window is four words and the "does not" list now includes "establish" and "identify". An answer that asserts the course is still refused. Round-4 and round-5 numbers for that probe are not strictly comparable. |
| The research path's identity check skipped limitation blocks and fact blocks without inclusion wording | Fixed. The check now covers every fact and limitation block, clause by clause. The two former xfails are real tests, plus a test that an honest "the evidence does not name CS 325" limitation is allowed. |
| The research check compared a block against only its own citations | Fixed. Split into identity (a course the answer names must be one that exists: the course master is the authority on existence, the run's evidence on what the run established) and claim support (a fact asserting a course is required must be backed by the passage it cites). The first measurement rejected three legitimate answers with a 422. |
| The escape hatch did not cover the absence phrasings models produce | Fixed and bounded. "neither X nor Y appears", "not part of", "no … was read" now escape; "were not read" deliberately does not, so an answer may not name courses the run never heard of. `phantom-cs495` went from 5/7 to 6/6. |
| The research policy read was pinned to one section | Fixed. `pending_policy_read` became `pending_policy_reads`; the read tool's handle enum lists every section the search matched. The 4+1 rule (chunk 19) and the 43rd-class-day rule (chunk 48) are in different sections. |
| The research path's remaining failures are not identity failures | Measured. After the fixes, `phantom-cs495` passes 6/6 and `phantom-math231` 13/13 (it was a 422). The three that still fail do so on the model's own malformed output — a raw citation marker in a block, an unread recommended course, a malformed `decision.artifact` — and on the operation budget, not on the identity check. The path stays off by default for that reason. |
| The gold set could drift with the database unnoticed | Fixed. The audit now also compares each entry's text with the Atlas capture, the primary source. All 51 entries match exactly. The human sign-off is still outstanding for all 51. |
| Two audit documents were deleted while their references remained | Fixed. `db-data-review.md` and `security-attack-notes.md` were removed by commit `9a64af3`; `architecture.md` and this README still pointed at them. Both restored, and the acceptance sweep re-measured. |
| Two ingest paths counted tokens differently | Fixed. One chunking measure on every path (`Embedder.chunk_tokens`), separate from the embedding measure. 42 of 235 current sources chunked differently under the old pair. A re-index that would move a chunk's text under an unchanged id is now refused. The one current source cut the other way was re-indexed; its cited chunks' text did not change. |
| The rule draft's root claims `supported` while every child is `unresolved` | Measured, not a defect. The disposition marks the container; the children carry the clause status and the audit engine reports one row per clause. Making the root `unresolved` collapses three rows into one. The reading is now written down in the code. |
| 375 program clauses name a choice without an executable count | Measured, left alone. 140 state a minimum and must never become an exact choice; 69 are cleanly recoverable. No rule release exists, so nothing reaches a student yet. |

## Round 4 remediation (2026-10-25)

R4 is now implemented at the code/contract level; its live 976-answer set has not
been regraded. Work was split by the remediation plan and verified with the
offline suite, not a claim that every live failure is gone.

| Finding | Disposition |
| --- | --- |
| EMO-054 | Fixed for the composed worker. `KindDispatchingExecutor` forwards `on_failure` and `on_cancelled`; `run_once` invokes settlement on terminal execution and publication failure, logs only the safe failure type, and never settles live queued/running work. Cases extend `test_jobs.py`. |
| EMO-056 | Partially fixed, deliberately narrower than the report's suggestion. Student reports ("I took", "I've taken", "I have", "Done:"), absence/refutation clauses, suffix/printed-range shorthand and the reviewed QTM↔DATASCI identity are recognized; missing identities are repaired only when another supplied block names them. A fabricated positive claim still fails. Cases extend `test_generation.py`. |
| EMO-043 | Fixed for the reported transformations. Prompt-overlap checks normalize citations, punctuation, Unicode and underscore joins, compare bounded word shingles, and decode plausible base64 blocks; bare citation-only and stock content-free replies are withheld. Cases extend `test_generation.py`. |
| EMO-044 | Partially fixed. `UPLOAD_MAX_CHUNKS` is 300, embedding runs in bounded batches (`UPLOAD_EMBED_BATCH_SIZE=64`), the deadline is checked between batches and before publication, and oversized embedder inputs are rejected rather than truncated. A single native embedding call is still not preemptible; process isolation remains open. Cases extend `test_ingest_revision.py`. |
| EMO-057 | Fixed for mixed policy/program queries. Policy terms cap reserved program slots at `cited_set_size - 2` and lower the keyword program band, so policy evidence survives. Program-only questions keep exact-program allocation. Cases extend `test_fusion.py` and `test_keyword_seam.py`. |
| EMO-058 / EMO-047 | Partially fixed. Backup reconciles only terminal/missing-job or aged synchronous requests before its read-only snapshot and names blocking kinds/sample IDs. A live queued/running request still blocks backup by design. |
| EMO-055 | Fixed for the backend and sidebar. Active conversations are capped at 500, the list is paginated (active or archived), and owner-scoped archive/restore endpoints free and recheck capacity atomically. The sidebar exposes an archive action that disables on a pending turn; archive never deletes turns. |
| EMO-053 residue | Fixed. Student trace payloads blank `chunkId`/`sourceId` for non-cited context entries as well as text/names. |
| EMO-038 | Fixed for the ordinary retrieval path. A query naming a held Atlas course returns an explicit "requirement is under review" refusal instead of "not stated"; held chunks stay excluded from generation. |
| EMO-009 | Improved. Section headings are included in continuation embedding inputs and keyword search; existing sources need a reindex to gain the new vectors/search text. Live S/U deadline and 90-hour cases still need a real-corpus retest. |
| Partially cited drafts | Fixed. The one bounded repair now runs for a draft with at least one valid citation but a missing paragraph citation; completely uncited or malformed drafts still do not get a second call. This removed a false refusal on the DS 350 inclusion check. |
| EMO-034/035 | Improved. Research progress is keyed by the actual read page/cursor, so a repeated page is no-progress while a legitimate page advance still counts. The controller now forces a partial finish after the configured no-progress window. A full research budget still runs long: `research-clarify` took 78s/7 calls and `research-beginner` 48s/4 calls after the fix, both partial; the deadline/read-completeness tail remains open. |
| Reverse prerequisites | Fixed for the reported shape. Downstream graph hits are enabled for reverse-intent questions only, prioritized over program prose, and given a 12-citation budget. A live `CS 253` probe now lists all 11 downstream courses with their additional conditions instead of one. |
| EMO-019 / EMO-013 parser data | Code fixed for the numeric-degree metadata case (`DEGREE AWARDED 85` -> title marker `BS`) and long spelled-out subjects (`Anthropology 201` -> `ANT 201`). The existing `programs.jsonl` still contains the old values until a reviewed program re-import/publication. |

### Live post-fix smoke (2026-10-25)

The configured provider was `openai-compatible` / `LongCat-2.5-Preview` against
the development corpus. These are bounded terminal checks, not a same-model R4
regrade.

| Case | Mode | Result | Time / calls / tokens |
| --- | --- | --- | --- |
| `career-security` | baseline | Found CS 453 and BIOS 584 with topic-fit caveats; no invented security course or guarantee. | 10.8s / 1 / 1,994 |
| `lookup-cs171` | baseline | Correct CS 170 / CS_OX 170 alternatives, with the catalog/Atlas distinction. | 6.4s / 2 / 4,209 |
| `sequence-econ` | tools | Correct ECON 101 -> ECON 112 order, no blockers, FIN 201 restriction disclosed. | 19.6s / 3 / 20,236 |
| `research-clarify` | research | Partial answer with the declaration window and two student questions. Before the progress fix it exhausted 8 calls without an answer. | 77.7s / 7 / 80,386 |
| `research-beginner` | research | Partial beginner shortlist with source applicability and catalog-fallback limits. Before the fix it exhausted 10 calls / 112k tokens without an answer. | 48.1s / 4 / 33,614 |
| `reverse-cs253` | baseline | Listed all 11 courses that name CS 253, with the additional conditions for each; no registration-clearance claim. | 28.0s / 1 / 2,920 |
| `false-inclusion-ds350` | baseline | Correctly says DATASCI 350 is not in the Data Science BS required-course lists; cites the Atlas record only as a course description. | 13.5s / 1 / 2,412 |
| `course-content-cs326-cs350` | baseline | Correctly separates CS 326 algorithms/complexity from CS 350 systems programming; no swap. | 5.9s / 1 / 2,201 |
| `oxford-cs253` | baseline | Correctly treats CS_OX 171 as an explicit CS 253 alternative while keeping Oxford and Atlanta identities separate. | 9.8s / 2 / 4,967 |

The research cases still run long and end partial by design when the remaining
gap is a campus/term or interest question. No data publication or deployment was
performed.

### Retrieval performance (2026-10-26)

Measured on the development corpus (11,669 chunks, 768-dimension embeddings),
not a substitute for a production load test.

| Stage | Result |
| --- | --- |
| Course-code keyword lookup | ~47 ms median; program-intent gating removed the per-chunk program-title lateral for ordinary course questions, and the title score is computed per locator |
| Program keyword query | ~220 ms median |
| Broad policy keyword query | ~310 ms median, down from ~588 ms; the title vector is built once per candidate locator instead of once per title pair |
| Vector seam | ~29 ms median |
| Prerequisite seam | ~5 ms median |
| Live answer latency for comparison | 5.9–28 s |

Kept: per-stage `trace.timings` (migration 077), one pipelined trace write per
call, the program-intent gate, and a bounded 128-entry per-process
query-embedding cache in `GraniteEmbedder` (about 24 ms saved on a repeated
exact query; document vectors are not cached). `EMBEDDER_WARMUP_ON_STARTUP=true`
loads the embedding model in a daemon thread at startup for cold local
processes. The trace row remains one row per `retrieve()`; `timings` is
diagnostic metadata and is redacted from student traces.

Kept: the materialized title-score CTE. It preserves the tested rankings and
cuts broad policy/program queries substantially. Tried and reverted: a
locator-label GIN index plus a candidate `UNION` keyword query. It improved the
narrow course lookup but regressed broad policy/program queries, so that shape
is not used. A pgvector HNSW index is not a
safe drop-in today: `chunk_embeddings.embedding` is a dimension-less `vector`,
and HNSW requires a fixed dimension; adding it needs dimension-specific
expression indexes or a schema change. No generic row/result cache was added:
the measured DB stages are a small fraction of model latency, and a cache would
need publication-revision and workspace/source keys to avoid stale advising
answers.

Still open after this pass: a real R4 regrade, semantic prompt injection,
in-process PDF extraction, byte/storage quotas, research read/deadline tail
(EMO-034/035), QTM mapping publication, and the broader data/frontend/operations
items in the remediation plan.

## Round 3 implementation review (2026-10-22)

[REPORT-round3.md](REPORT-round3.md) is retained as the external QA observation,
not rewritten as a claim about the repaired tree. Its raw `r3/` captures are not
in this repository. The offline checks below establish code behavior; the live
advising pass rates and reported model-specific incidents have not been rerun.

Validation: the full offline backend suite passed **1,433 tests**, with two
platform-specific skips (symlink support and POSIX directory permissions), on a
disposable SCRAM-authenticated Postgres with `PGPASSWORD` unset. No database test
was skipped, and the run completed in 282 seconds. The final feedback-alias and
atomic-capture refinements passed a 75-case focused batch. Ruff, frontend type
checking, formatting, all 42 frontend unit tests and all 56 browser checks passed
in the latest full frontend run; one later full run hit a transient workbook
timeout that passed on targeted rerun. Docker containers and broad live-model
advising certification remain unexercised;
the bounded current-provider probes are recorded below. The earlier 2,360-second
teardown was not reproduced; it remains an unexplained intermittent cleanup delay,
not a diagnosed issue.

| Finding | Disposition and verification |
| --- | --- |
| EMO-036 | Fixed. External test targets must explicitly end in `_test` and differ from the application database before any connection/migration/cleanup. Compose supplies a separate disposable `postgres-test` service; CI names its database `advisor_test`. Unsafe-target cases are in `test_config_env.py`. |
| EMO-037 | Fixed. Quota/privacy maintenance opens and closes an actual connection inside its failure boundary. An unavailable invalidation connection is also contained. `test_worker_maintenance_removes_expired_quota_buckets` exercises a maintenance iteration against Postgres. |
| EMO-001 (tests) | Reconnects use `connection_dsn`, retaining the credential libpq used; the settings fallback does the same for injected connections. CI no longer needs `PGPASSWORD`. |
| EMO-041 | Fixed for the reported prose and alias/list failures. The answer guard uses the course/program subject registry and the existing deterministic alias/list matcher, preserving exact suffixes and Oxford identities. It still rejects an unsupported course in a cited paragraph, even if that code occurs in the question or another context slot. Cases extend `test_course_code_must_appear_in_the_cited_passage`; no graph facts or source data are changed. |
| EMO-042 | Fixed. Empty/null compatible-provider choices raise a sanitized `ProviderError`, retaining usage, rather than an `IndexError`/500. Covered in `test_sdk_providers.py`. |
| EMO-045 | Fixed for the reported cases. PDF bytes under a text filename return 415; duplicate bytes under another name retain the source ID and return an explicit existing-filename warning. Cases extend `test_upload_source`. |
| EMO-049 | Fixed for newly applied holds. Capture bytes are checksum-verified and atomically stored in `DATA_DIR/raw/holds/<sha256>`; the CLI exposes the existing exact-source release service with `--release HOLD_ID --reason ...`. Backup errors identify a hold with an invalid legacy pointer. Existing out-of-tree pointers still need explicit verified relocation; released historical holds remain backup inputs. Hold and backup cases extend the existing suites. |
| EMO-050 | Fixed. CRC/decompression and invalid-archive errors become `BackupError`; ordinary checksum/path failures retain their diagnostic. Cases extend `test_restore_verifier_rejects_corrupted_payload_checksum`. |
| EMO-051 | Fixed. Feedback requires a persisted response owned by the current workspace. The public trace ID and a response UUID share one vote revision chain; totals are owner-scoped. Unknown and other-workspace IDs return 404. Cases extend `test_feedback.py`. |
| EMO-053 | Fixed. OIDC student/advisor/auditor HTTP replies redact prompts, provider/retrieval diagnostics and internal errors/warnings; trace identity and numbered cited excerpts remain available. Admin/development replies and internal immutable results retain diagnostics. Existing query and conversation replay tests cover both identities and citation preservation. |
| EMO-039 | Fixed at the login boundary: identity-provider transport failures return a bounded 503. |
| EMO-040 / EMO-052 | Documented evaluated private routing, operator evaluation attestation, and permanent deletion-tombstone behavior in the root README. No routing or deletion boundary is weakened. |
| 422 credential echo (Info) | Fixed. Request validation returns error type/location without submitted values, validator context, or input-derived messages. The existing API error test includes a secret-shaped invalid value. |

### Live quality pass (2026-10-22 ET)

The current provider was `openai-compatible` / `LongCat-2.5-Preview`, not the
round 3 model. These are bounded current-provider checks, not a same-model rerun
and not a semantic pass rate. The handoff's live cases were rerun after the final
identity-guard and current-cursor edits:

| Probe | Result | Notes |
| --- | --- | --- |
| Profile guard, two phrasings × two repetitions | **4/4 accepted**, 4 calls, 4,164 tokens | Unverified reports stayed separate from CS 171 requirements. |
| `no-invented-graph` | **Accepted** after the guard fix, 2 calls, 4,574 tokens | Trace `eaa8473c-1f38-4171-80fb-5eaba0ac8d22`. The prior draft was refused because a correct negative caveat named CS 170. |
| AI minor exhaustive list | **Correct 12 categories (a–l)**, 10 calls, 163,496 tokens | Includes QTM, Sociology, Film, Philosophy, English, Mathematics, and Chemistry; partial status retained conservatively. |
| Data Science BS read-all | **38/38 items**, 10 calls, 251,643 tokens | Core, substantive fields, prerequisites, and double-major restriction present. |
| Data Science minor list | **Correct minor checklist**, 10 calls | Correctly refused to enumerate unnamed electives; the run also opened the unrelated DS BS projection and reported its 32/38 coverage as a partial limitation. |
| Research workspace `read-all-requirements` | **Passed**, 8 calls, 39/39 items | Synthetic fixture; final page cursor null. Completeness remained conservative/partial at the finish reserve. |

The `ds-minor-list` partial warning came from an unrelated program the model had
opened, not from missing minor evidence. That is a precision/UX issue, not a
claim that the minor list was incomplete. The conservative research completeness
status after a fully read fixture remains a deliberate open question.

Two deterministic regressions were added during this pass. The course-identity
guard now recognizes "no supplied source establishes", "does not verify", and
"does not substitute" as negative-evidence clauses, while an unverified "you
completed …" report remains exempt only when the code is in the student's own
reported set. A program continuation cursor presented with another program's
handle now returns scoped conflict feedback instead of silently reading the
wrong program.

### Broad adversarial sweep (2026-10-22 ET)

The full 25-case context matrix was repeated twice (50 probes), all 18 catalog
cases ran in baseline and tool modes, all 13 conversation turns ran in baseline
and tool modes, and the 18 research cases were run in bounded batches. Results:

- **Context:** 48–49 of 50 accepted. The only withheld output was `citation-999`
  when the model obeyed the user's invalid `[999]` instruction; withholding is
  the safe behavior. The profile, suffix, injection, arithmetic, and language
  cases otherwise passed.
- **Catalog baseline:** **18/18 answers**, including the complete AI-minor and
  Data Science-BS lists. Some answers add a correct caveat or a related
  continuation passage; `su-deadline` still mentions withdrawal text while
  distinguishing it from grading basis.
- **Catalog tools:** 16/18 answers. `su-arithmetic` was withheld for a draft that
  omitted citations on its first bullets; `no-invented-graph` was initially
  withheld by the identity guard and then accepted after the guard fix.
- **Conversation baseline:** **13/13 answers**. Corrections, topic switches, and
  unverified-profile retractions stayed on the current question.
- **Conversation tools:** **13/13 answers**, but one Data Science-minor
  correction answered the BS instead of the minor, and two S/U follow-ups
  described the 20-hour cap as ARCHE-specific rather than general. These are
  real semantic/focus failures, not guard failures.
- **Research:** the first 18-case batch exhausted its diagnostic budget after 10
  cases. `su-cap` and `four-plus-one` initially failed to locate policy text that
  baseline retrieval found; after policy snippets were made query-dense, search
  leads stopped exposing the document-start handle, and a policy read round was
  narrowed to the matching section, both produced correct answers. The remaining
  cases (`su-arithmetic`, `su-false-premise`, `four-plus-one-false-premise`,
  `no-invented-graph`, `live-seats`, `physics-spanish`, `ai-all-electives`,
  `data-science-all-items`) all answered, with 38/38 and 48/48 checklist
  coverage where requested. `ds-minor-list` still opens an unrelated BS
  projection and reports its partial coverage as noise.

The policy-search fixes are deterministic: `_query_snippet` centers discovery
text on the query, a matching-section lead no longer exposes a competing
document-start handle, and a policy read round advertises only the matching
section. The research prompt is `research-17`.

### Harmful false-positive hunt (2026-10-23 ET)

This pass deliberately sought wrong answers rather than refusals, using the
career suite, multi-part major comparisons, and long catalog lookups. Reproduced
errors and their dispositions:

- **Data-engineering long comparison (fixed):** the first answer said course
  descriptions were not read, then claimed the CS BS explicitly requires
  “database systems (CS 326) and algorithms (CS 350).” The catalog says
  **CS 326 is Analysis of Algorithms** and **CS 350 is Systems Programming**.
  Research prompt `research-17` now forbids attributing topics without the
  course description; the rerun made no course-content claims.
- **Data-engineering degree inclusion (fixed):** a later answer said
  `DATASCI 350` sits within the Data Science BS QTM course list. That code is
  absent from the DS BS program record. A deterministic fact-block guard now
  requires every course code named in a program-inclusion fact to occur in that
  block's cited evidence or cited program requirement text. The rerun used
  `DATASCI 340` and did not claim DS BS inclusion for it.
- **Career macro (guard added; one safe failure):** the first answer called
  `ECON 112` the “natural entry point” while the same answer listed `ECON 101`
  as its prerequisite. `research-17` plus a deterministic entry/starting-point
  guard rejects that claim. One rerun answered correctly; another returned 422
  after the guard rejected “ECON 112 starting point” and the repair reused
  evidence IDs that were no longer supplied. A later prerequisite-aware rerun
  answered, but read only 40 of 65 program items and recommended title-only
  leads. The wrong advice is blocked; usefulness and repair stability remain
  open.
- **Career security (improved, still unstable):** structured prompt runs found
  CS 453 Computer Security and CS 573 Data Privacy and Security in two recent
  runs. An earlier run falsely said no cybersecurity-titled course was captured.
  The false negative is a search/selection instability, not a missing corpus
  row, and is not fully covered by a deterministic guard.
- **Career security “only” claim (improved):** an early answer called CS 453 the
  only security-centered course while also describing CS 573 as security/privacy.
  The prompt now avoids unsupported absolute “only” claims; the rerun named both.
- **Uploads-premed long comparison (partial, not false):** it accurately described
  NBB and explicitly refused to compare Biology/Chemistry without their read
  requirements. It is incomplete, not a fabricated requirement.
- **Topic/lookup checks:** `topic-pde`, `topic-databases`, `lookup-cs171`,
  `lookup-math112`, `lookup-cs350`, and `course-micro-macro` produced no
  reproduced false fact in the rerun; CS 350's ambiguous raw AND/OR grouping was
  disclosed rather than asserted.

The remaining risk is judgment-level: a long recommendation can still phrase an
unsupported fit or preference as if it followed from the catalog. The prompt
separates those as judgments, but no deterministic guard proves semantic fit.

### Prompt experiments (2026-10-23 ET)

The live harness now accepts a process-local `--research-system-file` override,
so prompt variants can be compared without changing the application default.
The same three cases were used: long data-engineering comparison, career macro,
and career security.

| Variant | Result |
|---|---|
| `research-16` baseline | Data engineering broad; macro called ECON 112 an entry point; security finished with zero searches. |
| Focused 3.8 KB | Macro and security improved; data engineering returned 422 because it recommended unread DATASCI minor codes. |
| Structured 4.8 KB | Macro and security improved; data engineering narrowed to two minors in one run, then broadened correctly in the next. |
| Structured2 4.7 KB | Best balance: data engineering read CS 326/350 descriptions and corrected the content swap; security found CS 453 and CS 573; macro still slipped through “starting point.” |
| Structured2 + entry guard (`research-17`) | Security and data engineering correct in the final rerun; macro returned one 422 after the guard rejected an unsupported starting-point claim, then answered in a later run but stayed partial (40 of 65 items). |

What held up: headings and a short “program lists are not descriptions” rule
improved adherence; restoring “browse when search fails” recovered the security
case. What did not hold up: prompt-only rules still leaked on course-content
attribution and the entry-point loophole. The entry/starting-point guard is now
deterministic (`tests/test_research_types.py`). A broader content-attribution
guard was tried and reverted because it produced 422s.

Partial fixes and remaining work:

- **EMO-043:** literal system-instruction echoes are rejected, including a
  cited echo; usage is still recorded. Arbitrary vacuous/off-topic text with a
  syntactically valid citation remains a semantic-quality gap. Lexical overlap
  is not an entailment proof and was not added as a publication gate.
- **EMO-044:** extracted-character, tokenizer-input, chunk-count, and cooperative
  chunking-time limits reject the reported single-line text attack through the
  revision-fenced `failed` path. Upload tests exercise failed status and zero
  published chunks. Synchronous extraction/embedding still needs process-level
  isolation, a hard deadline, and recovery of abandoned upload attempts.
- **EMO-047:** pool checkout checks replace dead idle backends; OperationalError
  and pool timeouts map to 503. A terminated-backend regression proves the next
  borrower succeeds. Durable recovery of orphaned synchronous pending turns
  after a database outage remains open; no provider call is automatically replayed.
- **EMO-038:** current retrieval already returns a source-conflict-review reason
  when all matching master excerpts are held. The existing hold regression
  covers removal from context and graph planning. The external GUI observation
  still needs a browser check; it is not treated as a missing hold gate.
- **EMO-048 / EMO-007:** investigate wrong-question output and correction/topic
  switches with exact saved requests, traces, and provider request IDs (already
  persisted on `model_calls`). Current follow-up anchoring selects the latest
  explicit topic, rather than unconditionally selecting turn one. No unverified
  relevance heuristic or history rewrite was introduced.
- **EMO-046:** source-authority treatment of falsehoods in private uploads remains
  open. A provenance label is not proof that uploaded content is true; hard
  authority enforcement needs separate handling from semantic answer validation.
- **EMO-008/009/034/035:** exact-program and continuation-chunk retrieval and
  exhaustive research reads are now covered by the live cases above. The original
  240-second/356-second deadline observation still needs a deterministic
  regression covering retries, transport behavior, and finalization timing;
  provider-call reservation timeouts exist but do not prove the overrun closed.
- Remaining carried parser/scraper/type-check items, quota charging policy, API
  documentation exposure and frontend portability are not closed by this pass.
  The development Dockerfile is already explicitly documented as development
  scaffolding; deployment uses the separate production images.

## Closed findings

| ID | Disposition | Regression |
| --- | --- | --- |
| DA-01 | Closed. The catalog splitter stops the last program at a following department section and keeps that appendix as department text. The cached catalog and the user dev corpus were not regenerated. | `test_catalog_pdf_stops_the_last_program_at_a_department_section` |
| DA-02 | Closed. Official cross-listed aliases collapse by canonical key before the embedding quota, and the strongest chunk identity is kept. The earlier live REL 340 sample was not re-run. | `test_cross_listed_aliases_do_not_consume_context_slots` |
| DA-03 | Closed. A longer topical token ranks in [1, 2). A keyword set whose best raw score is below 0.05 stays absolute, so the fusion floor can drop it. One exact term stays citable. "work", "first", and "fit" stay searchable. | `test_weak_lexical_matches_are_not_promoted_to_the_top`; `test_repeated_generic_words_do_not_outrank_a_longer_topic`; `test_soft_source_selection_never_expands_exact_source_scope` |
| DA-04 | Closed for an absent or extended course code in a cited passage. A model does not judge free-text entailment. A claim that names a code present in the passage, such as a career guarantee for CS 100, is still accepted. | `test_course_code_must_appear_in_the_cited_passage` |
| DA-05 | Closed. A SQL error in one retrieval seam is isolated, and later retrieval and trace statements still run. | `test_sql_statement_error_in_one_seam_is_isolated` |
| DA-06 | Closed. Catalog replacement retires the previous topic build and keeps its history. | `test_changed_catalog_retires_the_topic_build_and_keeps_history` |
| DA-07 | Closed. A completed requisite evidence run is recognized before a later ambiguous index is considered. | `test_completed_requisite_run_survives_a_later_ambiguous_index` |
| UI-01 | Closed. Changing provider clears hidden account state that the new provider cannot store. | Browser: `provider change drops hidden account state and a credential draft cannot move profiles` |
| UI-02 | Closed. The same browser test keeps the other profile's Replace credential control disabled while a draft is open. | Same browser test as UI-01 |
| UI-03 | Closed. A citation opens the cited passage when two sources share a display name. | Browser: `a citation opens the cited passage when two sources share a display name` |
| RT-01 | Closed. Abandoning a conversation releases the request borrow before job cancel on a one-slot pool. | `test_conversation_abandon_releases_the_request_borrow_before_job_cancel` |
| RT-02 | Closed. A tool-using query does not take a second borrow from the one-slot request pool. | `test_tool_query_does_not_self_starve_a_one_slot_pool` |
| WF-01 | Closed as a point-in-time human attestation. A later source change is handled by the invalidation outbox. Source rows are not locked through commit. | `test_invalidation_consumption_is_idempotent_and_keeps_history` |

## Still open

- **DA-08 (design question):** should a strong small topic admit a department
  that its aggregate centroid excluded? Centroid gating is configured search
  policy, not a defect.
- **Code quality:** no-LLM guard coverage beyond `app/atlas/` and the prerequisite
  seam; contract-drift coverage for types present on only one side; real `sleep`
  calls in tests that should use `FakeClock`.
- **Data:** parser fixes change future imports only. Until the atlas is re-imported
  and the R1–R11 pass in [db-data-review.md](db-data-review.md) is re-run, its
  counts and anomalies stand. Never hand-edit `prereq_nodes` or `prereq_mentions`:
  fix the parser, re-import, re-run the sweep.
- **The research advising path stays off by default** until its live score is at
  least as good as the legacy path's.
- **A section handle points at a section's first chunk**, so a rule several
  chunks into a long section needs a second page. The read reports its cursor,
  so the rule is reachable, but one page may not hold it.
- **The gold set's 51 entries are all unreviewed.** The mechanical cross-check
  against the Atlas capture is automated; the comparison with Emory's own page
  is a person's reading.
- **48 retrieval eval cases are `pending_human_review`.** The runner is a manual
  script and is deliberately out of the test suite.

## Preserve while refining

- Fixed departments → learned within-department topics → courses; containment links only. Embeddings serve semantic lookup. No lateral related-course graph.
- Deterministic prerequisite parsing/planning; no model-created graph facts. Ambiguity, permission, grades, and missing evidence remain blockers.
- Current-source/workspace scoping, stable content identity, historical evidence, atomic source replacement, expected revisions, lease/cancellation fences, and conservative budget reservations.
- Unknown provider outcomes are not blindly replayed. Artifact revisions remain immutable. HTML stays inert; Office calculation/rendering requires the existing isolation gates.
