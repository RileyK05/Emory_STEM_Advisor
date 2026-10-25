# Audit status

Closed findings from the 2026-10-10 systematic audit and the 2026-10-20 code and
data scans, the items still open, and behavior a refactor must not "fix" away.
The original reports were removed once every confirmed finding closed; they are in
git history. [db-data-review.md](db-data-review.md) is the live data review and
stays until its R1–R11 acceptance pass is re-run.
[security-attack-notes.md](security-attack-notes.md) records the adversarial
input pass and its open denial-of-service limits.

## Round 3 implementation review (2026-10-22)

[REPORT-round3.md](REPORT-round3.md) is retained as the external QA observation,
not rewritten as a claim about the repaired tree. Its raw `r3/` captures are not
in this repository. The offline checks below establish code behavior; the live
advising pass rates and reported model-specific incidents have not been rerun.

Validation: the full offline backend suite passed **1,405 tests**, with two
platform-specific skips (symlink support and POSIX directory permissions), on a
disposable SCRAM-authenticated Postgres with `PGPASSWORD` unset. No database test
was skipped, and the run completed in 352 seconds. The final feedback-alias and
atomic-capture refinements passed a 75-case focused batch. Ruff, frontend type
checking, all 42 frontend unit tests, and Compose/CI YAML parsing passed. Docker
containers and broad browser/live-model advising certification remain unexercised;
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

## Preserve while refining

- Fixed departments → learned within-department topics → courses; containment links only. Embeddings serve semantic lookup. No lateral related-course graph.
- Deterministic prerequisite parsing/planning; no model-created graph facts. Ambiguity, permission, grades, and missing evidence remain blockers.
- Current-source/workspace scoping, stable content identity, historical evidence, atomic source replacement, expected revisions, lease/cancellation fences, and conservative budget reservations.
- Unknown provider outcomes are not blindly replayed. Artifact revisions remain immutable. HTML stays inert; Office calculation/rendering requires the existing isolation gates.
