# Full static bug scan — 2026-10-10

Method: 7 parallel deep reads over all of `backend/app/`, `backend/scripts/`, SQL,
configs, and `frontend/src/`, with every HIGH and top MEDIUM re-verified by hand
against the code (including psycopg's `TypesRegistry`, openpyxl's `Worksheet.cell`,
and the installed package behavior). `ruff check` clean. No tests run.

Scope note: 26k lines Python in `backend/app`, 7k lines frontend. Findings are
grouped by severity. "Checked and clean" at the bottom lists what was ruled out.

---

## Fix first

### 1. Pool: dead guard leaks connection slots — `backend/app/db/connection.py:116`

`"vector" not in conn.adapters.types` is *always* true: `psycopg._typeinfo.TypesRegistry`
has no `__contains__`, so `in` falls back to iterating `TypeInfo` objects and never
matches a string (verified against the installed psycopg: `'vector' in registry` is
`False` even after `registry.add(TypeInfo('vector', 16, 17))`; iteration yields
`TypeInfo` objects, names only reachable by `.name`).

Consequences:
- `register_vector(conn)` (4 `TypeInfo.fetch` catalog round-trips in nested
  transactions) plus `conn.rollback()` run on **every pool borrow** — i.e. every
  request, since `connector()` borrows per request (`app/api/deps.py:51`).
- Any exception other than `psycopg.ProgrammingError` from `register_vector`
  (e.g. `OperationalError` when the server restarts) or from `conn.rollback()`
  escapes between `pool.getconn()` (line 115) and `return PooledConnection(...)`
  (line 123): the connection is never `putconn` and its pool slot leaks forever.
  After `max_size` such blips the pool is permanently exhausted and every borrow
  raises `PoolTimeout` until process restart.

Fix: use `conn.adapters.types.get("vector") is None` (or `try/except KeyError`),
and wrap `getconn()`..`return` in `try/finally: putconn` on error.

### 2. Dependent artifact runs wedge permanently — `app/generation/planned_artifacts.py:441` + `app/jobs/artifact_plans.py:606`

A unit is marked `"calling"` before `generate()` (planned_artifacts.py:441-447) and
only leaves that state via `mark_validated` / `mark_invalid_attempt` / `mark_failed`
in the same invocation. On resume, the loop picks the `"calling"` unit again and
calls `store.mark_calling`, which rejects anything not `pending`/`needs_repair`
("Only an unstarted or repairable unit may begin a provider call",
artifact_plans.py:607-608). `ArtifactUnitConflict` escapes the module's
`except (ProviderError, OperationBudgetError)` (planned_artifacts.py:506).

`ArtifactRunPlanStore.mark_unknown` (artifact_plans.py:758) — the transition
designed to close an unknown call — has **zero callers** in the codebase.

Scenario: lease loss or worker restart while a section's model call is in flight
(routine on CPU where calls exceed lease/timeout windows) → every retry fails,
`max_attempts` is burned, plan stays `"running"` with a stuck `"calling"` unit forever.

### 3. Program parser fabricates course codes — `app/programs/requirements.py:122-141`

When a `_CODE_RE` match has a subject `_subject()` rejects (`Classics`,
`Environmental` — anything not in `SUBJECT_ALIASES` and not all-caps), `subjects`
stays `None` and the match falls through to the bare-number **carry** branch
(lines 131-136), which reuses `last_subjects` on the match's number.

"Math 211, 250, and Classics 350" emits `MATH 350` — a real, resolvable course
code — for "Classics 350". With no prior subject the line is silently dropped.
There is no `leftover`/unparsed surfacing at all in this parser. Fabricated codes
land in `program_course_mentions` and resolve against real course rows, so
advising shows a wrong required course.

Second bug in the same file: `choose_count` (lines 57-58, 149-155). Pattern 1
anchors on the **leftmost** `_COUNT` token and crosses up to 40 chars of `[^.:;]`
to reach "from/of the following". "MATH 351 (3 credits) or one of the following"
matches at the `3` of "(3 credits)" and returns `choose=3` instead of 1;
"MATH 111 (4 credits) Two courses from the following" returns 4 instead of 2.
Any standalone 1-2 digit number within 40 chars before the phrase hijacks the
count, and `program_items.choose` is stored as fact.

### 4. XLSX import OOM bomb — `app/artifacts/xlsx.py:75-79, 171-176`

`_preflight_sheet_xml` admits any sheet whose used rectangle is ≤ 2,000,000 cells
and counts only real `<c>` elements toward `MAX_WORKBOOK_CELLS` (100k). But
`_import_sheet` then calls `worksheet.iter_rows()`, and openpyxl's `Worksheet.cell`
**creates and stores a Cell object for every empty coordinate** in the rectangle
("Creating cells in memory when they are first accessed").

A ~20 KB xlsx with 100 sheets each holding one cell at row 1,999,999 col A passes
every check (`total_cells=100`, product ≤ 2M) and then materializes ~200M Cell
objects. Even one such sheet allocates ~1-2 GB. Authenticated
`POST /api/artifacts/import` can OOM-kill the API process.

### 5. Standalone worker kills its own database — `scripts/worker.py:74-79`

With `DATABASE_URL` unset (the documented dev default), `open_connection` starts
pgserver with `cleanup_mode="stop"` (`db/connection.py:187`) and its `finally`
calls `server.cleanup()` (line 199), which stops the postmaster the moment the
`with` block exits at worker.py:79. Everything after — `JobStore(connector(settings))`
(worker.py:109) and the `while True` loop — connects via the now-dead
`settings = replace(..., database_url=dsn)`.

Scenario: fresh clone, `python -m scripts.worker` (or `--once`) alone → first
`store.recover_expired` (worker.py:113) raises `psycopg.OperationalError` uncaught
and the worker dies. It only survives if `scripts.serve` happens to hold the same
pgdata alive. Compare `serve.py:26-31`, which correctly keeps `uvicorn.run`
inside the context.

### 6. Frontend: "New conversation" bricks the panel — `frontend/src/components/conversation/ConversationPanel.tsx:225`

`loadingList` / `loadingConversation` are only cleared inside epoch guards
(lines 131-133, 171-173, 220-222), but `startNewConversation` (225-234) bumps
`viewEpoch` without resetting either flag and starts no replacement load.

Scenario: open Saved conversations and click "New conversation" before the initial
`listConversations` resolves → the `.then` bails at line 131 and `loadingList`
stays `true` forever: the toolbar shows a permanent "Loading conversations…" and
"Refresh list" is disabled (ConversationToolbar.tsx:19,36). Same shape with
"Refresh conversation" then "New conversation": `loadingConversation` sticks true,
so `Composer`'s `busy` (line 424) disables sending forever. No in-panel recovery;
only remount (tab switch) fixes it.

---

## Medium

| Where | What |
|---|---|
| `app/api/query_service.py:61` | Gap-error handler does `outcome.trace.warnings.append(...)`, but `RetrievalTrace` (`retrieval/types.py:43-52`) is a frozen dataclass with **no `warnings` field** → `AttributeError` turns graceful degradation into a 500 *after* `persist_response` already committed. Trigger: transient DB error during `ensure_gap_identity` on a retrieval refusal. |
| `app/retrieval/fusion.py:75` | `if budget > len(source_order)` should be `>=`. Docstring says displacement only happens "if the budget is smaller than the number of sources"; with `budget == len(source_order)` the displacement path still runs. 3 sources, `cited_set_size=3`, A1=0.9/A2=0.8, B1=0.5, C1=0.4 → `[A1,B1,C1]` then A2 displaces C1 → `[A1,B1,A2]`; source C vanishes even though the budget covered every source. |
| `app/atlas/importer.py:142-166` | `import_atlas` never rejects an empty course list (`load_atlas` returns `[]` for an empty file, `records.py:52-53`), then `delete_courses_not_in_atlas` runs with `codes = []`. `NOT (code = ANY('{}'))` is true for every row (`atlas.sql:49-52`) → **all courses in the collection are deleted** and program mentions are `SET NULL`. A scrape yielding 0 records imports "successfully". `build_simgraph` rejects the empty case; the importer should too. |
| `app/atlas/requisites.py:18-20` | `_CODE_RE = ({_SUBJECT})\s?({_DIGITS})\|\b({_DIGITS})\b` — the subject alternative has **no trailing `\b`**, and `_DIGITS` accepts a one-letter suffix. "MATH 111or 112" matches "MATH 111o" → bogus mention `MATH 111O` + leftover "r", directly contradicting the comment at lines 15-17. Separately `_DIGITS` can't match the catalog's documented `202zL` suffix (`[a-z][A-Z]`): "MATH 202zL" → `MATH 202Z` + leftover "L". Status is `unparsed` (tree is safe) but the wrong code is permanently recorded in `prereq_mentions`. |
| `app/atlas/catalog.py:168-174` | `write_jsonl` opens the canonical `data/atlas/emory_college.jsonl` with `"w"` and writes in place — no temp+`replace()` like `_store_raw` (same for `programs.jsonl`, `scripts/scrape_programs.py:143-147`). A crash truncates the file; truncation at a line boundary still parses as valid JSONL, so `import_atlas` treats the smaller course set as authoritative and drops the missing tail courses, silently. |
| `app/jobs/provider_batches.py:352-381` | `idempotent_replay` returns a snapshot for *any* existing parent row, so `create_artifact_batch` returns without ever reaching `ProviderBatchService.submit` — the only code that advances `prepared` → `submitting`. `poll` explicitly refuses to advance `prepared` batches with no provider handle (lines 379-381). A batch that dies between item-insert and submission is stuck in `prepared` forever on retry. Worse: crash before the item-insert commits + retry → permanent 409 "batch ID was already used for different inputs" for byte-identical input (lines 366-367). `_ensure_reservations`' resume branch (591-600) is unreachable. |
| `app/generation/claim_review.py:383-390` | `_record_intent` commits the `pending` row **outside** the fail-closed try; only `_publish` is sealed on error. Exceptions from `_call_provider`'s post-call bookkeeping (`account_usage` line 495, `_audit` line 500) propagate with no seal → `_stored_review` conflicts forever ("pending or has an unknown outcome", line 405) and `_raise_if_operation_reviewed` blocks any new review ID for that operation. |
| `app/generation/planned_artifacts.py:648` | `_select_provider` passes `job_kind="artifact_authoring"` to `resolve()` unconditionally, while the sibling path passes `artifact_authoring if authoring else None` (`generation/artifacts.py:235-238`). `artifact_authoring` is in `fast_job_kinds` (`configs/routing.toml`) → `fast_generation` role. Grounded multi-section runs route to the wrong role, and it is frozen into `provider_policy` for the plan's lifetime (line 345). |
| `app/artifacts/docx.py:154, 168-173` | Two 500s on crafted input. Missing `word/document.xml` → `KeyError` from `archive.getinfo` (`safety.py:131`), which `inspect_office_archive` never requires. Missing `w:pgSz`/margins → `None.inches` `AttributeError` (`section.page_width.inches`). The route maps only `ArtifactError` and `ValueError` (`artifact_routes.py:85-88`), so neither becomes a clean 4xx. |
| `app/artifacts/xlsx.py:25-27, 106, 272` | Formula blocklist misses legacy DDE (`=cmd\|'/c calc'!A1`, `=MSEXCEL\|...!A1`) at both import and export — exactly the class the blocklist exists to stop, and `HYPERLINK` *is* blocked. |
| `app/persistence_backup.py:143-213` | `create_backup` does `conn.commit()` (silently committing the caller's in-flight work), then opens `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ_ONLY`, and returns at line 213 without commit/rollback. Any subsequent write on that connection fails ("cannot execute … in a read-only transaction") and the pinned snapshot delays vacuum. Latent today (sole caller `scripts/backup.py:71-76` closes the connection immediately). |
| `app/programs/requirements.py:42, 48-49` | `\b` before `_NUMBER` treats the hyphen in "200-level" as a word boundary → "Two QTM 100-level courses" emits `QTM 100`, a real course, so the bogus mention **resolves**. `_CARRY_SEP_RE` also allows `-` (intended for "421-422"), so "MATH 200-300 level" fabricates both `MATH 200` and `MATH 300`. |
| `scripts/scrape_catalog.py:157` (also `scrape_programs.py:203`) | `Blocked` subclasses `FetchError` (line 43-44), and the requirement-groups loop's `except (FetchError, ValueError)` records a bot challenge as an ordinary per-batch failure and keeps requesting the remaining `requirementGroups` batches — contradicting the stop-on-challenge policy the course loop enforces with `except Blocked: ... break` (lines 133-137). Keeps firing at a WAF-challenged host; invites a hard block/IP ban. |
| `frontend/src/components/artifact/ArtifactWorkspace.tsx:318` | The HTML textarea (318), Markdown textarea (324) and generation title (GenerationPanel.tsx:118) are not `disabled={busy}`, but `applyRecord` (84-91) unconditionally overwrites `htmlDraft`/`documentDraft`/title with the saved result. Edit → Save revision → keep typing while the POST is in flight → your keystrokes are replaced with the pre-save content, no error, no warning. |
| `frontend/src/components/connection/ConnectionSettings.tsx:85` | `submit` falls through to `createConnectionProfile` whenever `profiles.find(item => item.profile_id === editing)` misses (85-92), and `removeProfile` (122-129) deletes + refreshes without clearing `editing`. Edit profile A → Delete profile A → "Save profile" creates a brand-new duplicate while the notice claims "Profile saved." |
| `frontend/src/components/artifact/GenerationPanel.tsx:132` | "Start generation job" is disabled only by `busy`/empty-prompt, not by an active run (`running` at line 21 only gates Cancel). `useArtifactGeneration.start` unconditionally `setCurrentRun(result)` (useArtifactGeneration.ts:154). Starting run B orphans run A server-side: no status display, no reachable Cancel (cancel only targets `currentRun`). |

---

## Low (worth a look, not urgent)

### Correctness

- `app/db/queries/embeddings.sql:21-22`, `keyword.sql:12-13`, `documents.sql:176-177,194-195` — `(%(workspace_key)s::text IS NULL OR …)` means "NULL parameter ⇒ no fence at all", so retrieval/citation queries with `workspace_key=None` return chunks from **every private workspace**. The source-fence queries (`documents.sql:226, 259`) correctly treat NULL as "shared sources only". Today the API always passes a real workspace; evals/scripts pass None — an eval against a DB containing private uploads silently pulls private chunks into "official corpus" results.
- `app/db/migrate.py:97-114` — `verify_release_history` (readiness, `canonical_only=True`) rejects the legacy digests `apply_migrations` deliberately accepts (`_accepted_digests`, lines 66-72, exists for the CRLF-checkout case). A fully-migrated legacy-history DB reports `schema_mismatch` until some process runs `apply_migrations`. Standard entrypoints normalize first; `persistence_backup.restore_backup` does not.
- `app/jobs/store.py:115-128` + `app/api/run_routes.py:189-196` — run idempotency key is not bound to the request payload: `ON CONFLICT (scope_key, idempotency_key) DO NOTHING` returns the existing job regardless of `kind`/`payload`, and `create_run` returns it as a normal 200. A client that reuses `idempotencyKey` after a timeout but with edited inputs silently receives the *old* run. Batches hard-fail fingerprint mismatches; runs are the inconsistent path.
- `app/llm/routing.py:112` — `len(query.split()) >= config.complex_token_threshold` compares whitespace **words** against a threshold named and worded as **tokens** (`routing.toml:12` = 1200). A 300-900-word query never trips the complexity escalation.
- `app/retrieval/evals.py:31` — `case_type` is loaded but never consulted (`EvalCase.negative` is `not self.expected`). A case authored `case_type: "negative"` with non-empty `expected` silently scores as a positive recall case, skewing fused/seam recall denominators.
- `app/ingest/extract.py:192-203` — the atlas path renders straight from `decode_text(...)` without `normalize_text`, unlike markdown/plain (207-211). `\r`, zero-width, C1 or NUL characters in scraped titles/descriptions survive into chunks and embeddings (a NUL would fail the Postgres insert).
- `app/ingest/extract.py:209-211` + `pipeline.py:255-266` — whitespace-only text files "index" with zero chunks and empty warnings (`_line_range_locators` emits a locator over an all-whitespace span; the chunker then emits nothing). The PDF path raises `EmptyExtraction` for the equivalent case (extract.py:178-179).
- `app/ingest/pipeline.py:131-132` — `_replace_index` returns without writing when a concurrent attempt claimed a newer `index_revision`, but `ingest_file` then returns the `source_id` indistinguishably from a successful index. `import_atlas` proceeds to `refresh_atlas_requisites` against a possibly mid-flight source and scripts print success.
- `app/ingest/pipeline.py:60-61` — chunk IDs are `uuid5(source_id, f"{chunker_version}:{chunk_index}")` and locators `uuid5(source_id, f"loc:{ordinal}")`. Changing `target_tokens`/`overlap_tokens`/`text_lines_per_locator` without a `chunker_version` bump keeps every ID but moves the text underneath them (and shrinks the chunk count, silently orphaning high-index citations).
- `app/atlas/records.py:52-53`, `importer.py:101-102` vs `ingest/normalize.py:30-46` — `load_atlas` uses strict `utf-8` while `extract` uses BOM-aware `decode_text`. A BOM'd atlas file is accepted by ingest but dies with `JSONDecodeError: Unexpected UTF-8 BOM` in `load_atlas`.
- `scripts/scrape_catalog.py:89-91, 123` — `_write_json` is non-atomic. A crash mid-write leaves a truncated cache file whose `JSONDecodeError` is caught (lines 138-140) as an ordinary failure with `continue` — the course is never re-fetched and is silently missing forever. A truncated `requirement_groups.json` hits the unguarded `_read_json` at line 147 and kills every re-run until deleted by hand.
- `scripts/reconcile_source_origins.py:29-37` — provenance regexes require `Source: …\nRetrieved: YYYY-MM-DD`, but current `render`/`render_section` (`scrape_programs.py:85-86, 106-108`) moved timestamps to the sidecar manifest. Any group whose `current_sha256` is a re-scraped file fails at line 128 and can never be reconciled again.
- `scripts/scrape_programs.py:65-71, 174-183` — `catalog_origin_key(title)` slugs titles and truncates at 120 chars; two distinct titles that slug identically get filenames disambiguated but **identical origin keys**. With `sources_current_origin_uq` + `supersede_source_origin`, each ingest flips the other doc to `is_current = false`, so only one of the two distinct program documents is retrievable at a time.

### Security / robustness

- `app/generation/answer.py:61-62, 103-110, 153-160` — prompt trust-boundary sentinels (`<<<CONTEXT …>>>`, `<<<END CONTEXT>>>`, `<<<CONVERSATION HISTORY …>>>`) are not escaped in untrusted `chunk.text` / `history_context`. A source document containing the literal `<<<END CONTEXT>>>` closes the declared untrusted region early and the remainder is read as prompt-level instructions.
- `app/auth/service.py:199`, `app/auth/oidc.py:157` — the nonce is compared with plain `!=`, while `state_matches` correctly uses `hmac.compare_digest` (service.py:168). Low real-world impact, but it is the odd one out in the login flow and the one comparison guarding against token replay from another login transaction.
- `app/artifacts/xlsx.py:288, 298` vs `models.py:134, 191` — specs that pass model validation can be permanently unexportable: `WorkbookSheet.column_widths` accepts arbitrary keys/values and cell strings have no per-cell bound, but `_write_sheet` raises bare `ValueError` for a non-`^[A-Z]{1,3}$` key, a width outside (0, 255], or a multiline cell whose forced row height exceeds 409pt. No handler for plain `ValueError` → `GET /download?format=xlsx` 500s forever.
- `app/artifacts/storage.py:213-227` + `db/baseline/032_schema.sql:1300` — concurrent same-`draftId` saves: `revise()` reads the draft row outside the CAS transaction then inserts; two concurrent identical requests both see no draft and the second hits `UNIQUE (artifact_id, draft_id)`. `UniqueViolation` is not `RevisionConflict` → 500 instead of the idempotent replay draft IDs exist for (a later retry does heal).
- `app/artifacts/markdown.py:137, 144, 153-156` — models allow `\n` in `HeadingBlock.text`, list items and `CodeBlock.language`; `render_markdown` splices them into structural positions. A `language` of `"js\n# injected"` emits a heading outside the fence. Corrupts exported .md structure.
- `app/artifacts/markdown.py:130, 171` — table round-trip silently corrupts cells containing literal `<br>`: `_escape_table_cell` maps newlines → `<br>` and `_table_row` maps *every* `<br>` → newline.
- `app/artifacts/inert_html.py:20, 64-69` — `style` attribute values are copied verbatim into inert markup. No script is possible (no event handlers/URL sinks; inner CSP `img-src data:` blocks CSS fetches), but arbitrary CSS enables overlay/UI-redress phishing inside the sandboxed preview frame. Deserves an explicit decision.
- `app/artifacts/xlsx.py:364-368` — `_recalculation_problems` feeds LibreOffice/remote-worker bytes straight to `load_workbook` without the `inspect_office_archive` preflight user uploads get, though the comments declare runner output untrusted. openpyxl's `resolve_entities=False` blunts XXE/entity bombs.
- `app/sandbox/runner.py:495-499`, `office.py:213-223` — if `shutil.rmtree` raises OSError in `finally`, it raises `SandboxError`/`IsolationUnavailable`, discarding a successful run's collected outputs or replacing the original timeout/cancel error — and the temp dir still leaks.
- `app/sandbox/runner.py:62-72` — `_safe_filename` allows `CON`, `NUL`, `data.` (no reserved-device check, unlike `_safe_output_component` at 75-85). Windows normalizes trailing dots/spaces: distinct inputs `data` and `data.` silently collide/overwrite in the input mount.
- `app/sandbox/worker.py:60-67` — `_bounded_json` calls `await request.body()` before checking the limit, so a chunked request with no `Content-Length` is fully buffered first. Bearer-authenticated, so impact is bounded to token holders.
- `db/baseline/032_schema.sql` (no trigger; cf. `:2220-2244`) — `artifact_revisions` immutability is app-enforced only. Sibling `artifact_run_*` tables have `*_immutable` triggers; nothing in the DB prevents a future UPDATE/DELETE from rewriting revision history.
- `app/generation/planned_artifacts.py:774-819, 892-948, 491` — untyped `ValueError`/`KeyError` (`_preflight_remaining_budget`, `_assemble_dependent_spec`, `validate_spec` outside any try) escape the safe-error mapping and die as opaque internal job errors instead of safe coded failures.
- `app/llm/batches.py:42` — `_expected` correlation-id map grows without bound: `submit()` stores all correlation ids and nothing ever pops them (`results()` reads but never evicts; callers pass `expected_ids` explicitly anyway). The registry caches batch adapters per profile revision, so a long-lived process accumulates every batch's full id list (up to 50k strings each) forever.
- `app/llm/batches.py:460` — Google batch rows lacking `metadata.custom_id` (and file rows lacking `key`) fall back to `expected[index]`. Out-of-order or partially-tagged rows silently match outputs to the wrong correlation ids and publish wrong generations under wrong requests.
- `app/llm/batches.py:365` — `AnthropicBatchProvider._complete` overrides the shared `_complete_results` and skips its duplicate/unknown-id validation: duplicates collapse last-wins and unknown ids are silently dropped where the shared path raises `InvalidRequestError`.

### Frontend

- `frontend/src/api/types.ts:96` — `DiagramSpec` intersects an object type with an array type: `nodes: { id: string; label: string } & ArtifactElementRef[]` types the field as an *array that also has* `id`/`label` (unsatisfiable). Should be `({ id: string; label: string } & ArtifactElementRef)[]`. Same for `edges`.
- `frontend/src/components/artifact/download.ts:8` — `revokeObjectURL` runs synchronously right after `link.click()`; Firefox in particular cancels such downloads silently. Defer with `setTimeout`/`requestAnimationFrame`.
- `frontend/src/components/artifact/useArtifactGeneration.ts:94` — each successful poll tick runs `setError(null)` against the shared workspace error slot, wiping an error set by another action (e.g. a failed save) within ~1.2 s while any run is active; a transient poll failure (line 96) overwrites it with a polling message.
- `frontend/src/components/artifact/ArtifactWorkspace.tsx:176` — "Save revision" on an unchanged document/HTML always mints a revision. The workbook path returns `null` when nothing changed (158-161), but the document path (`edited` always non-null, `changed` unchecked) and HTML path (177) always call `saveWithId`, contradicting the `saveCurrent` docstring.
- `frontend/src/components/conversation/ConversationPanel.tsx:294` — `setNotice(result.type === 'history' ? '…' : null)` gives no feedback when `sendConversationTurn` returns `type: 'failed' | 'abandoned'`. Contrast the advising path, which notices failed/refused terminals (307-310).
- `frontend/src/components/artifact/PlanEditor.tsx:51` — `key={index}` on removable plan rows; "Remove section N" deletes from the middle (99) and positional DOM reuse migrates focus/fieldset identity onto different rows' data.
- `frontend/src/components/artifact/previewHtml.ts:19` — the sanitizer's attribute pass strips `on*` and a fixed list but leaves SMIL (`<animate>`, `<set>`, `attributeName`, `values`, `to`/`from`, which can rewrite `href`/`src` at parse time), `<style>` and `style` attributes. Not exploitable today because PreviewPane always renders via `sandbox=""` + `script-src 'none'` CSP (PreviewPane.tsx:22-27, previewHtml.ts:27-32), but any future use of `sanitizePreviewHtml` output without that sandbox becomes XSS.

---

## Checked and clean

- **Content-addressed IDs** — `util/digest.py` uses `sort_keys=True`, fixed separators, `allow_nan=False`, UTF-8; no ordering, float-formatting or locale nondeterminism. Source IDs hash raw bytes (`ingest/pipeline.py:47-61`); no timestamps in hashed content.
- **Chunker / locators** — offsets, sentence/word spans, `_split_word` budgeting, pack-loop progress and overlap walk-back all consistent; stored `chunk.text` equals `text[start_char:end_char]`; no empty chunks; locators tile the text (`_assert_cover`).
- **Source replacement ordering** — `_replace_index` swaps locators/chunks/vectors in one transaction under a revision claim plus advisory origin lock; `supersede` runs before `activate` in the same transaction (`documents.sql:74-87`), so the old atlas source is never un-currented before the next deterministic graph is written. The AGENTS.md atlas rule holds.
- **Hard rule** — no model/LLM/provider imports anywhere in `app/atlas/`, `app/advising/`, `app/ingest/`, `app/programs/`, `app/simgraph/` or the prereq seam. Advising tools call only `plan_sequence`. Graph facts are parser-only.
- **Advising planner** (`advising/sequence.py`) — cycles detected (DFS over `bundle_edges` + intra-bundle edges); blockers propagate to a provably-terminating fixpoint; stage order and blocker lists fully deterministic (every set sorted before output); inputs never mutated; unparsed/ambiguous/grade-conditioned requisites always block — no silent under-blocking.
- **HTML inert/active sanitization** — only allowlisted tags/attrs emitted; no `href`/`src`/on\* survive; `base`/`meta`/`link`/`a`/`form`/`iframe` dropped; values quote-escaped; `img src` restricted to strict-base64 `data:image/{png,jpeg,webp}` ≤ 1 MB; `active_html` escapes `srcdoc` correctly and wraps in `sandbox=""` iframe + inner/outer CSP. `render_document_html` escapes first, then substitutes tags.
- **Zip-slip / zip-bomb (entry level)** — names normalized; absolute/`..`/duplicates rejected (`safety.py:42-47`); nothing extracts by entry name; declared sizes + ratio caps + double size checks in `read_bounded_entry`; `ZipExtFile` honors `file_size`. (The xlsx cell-rectangle issue is the remaining resource hole.)
- **Sandbox hardening** — argv-only `shell=False` throughout; user source never enters argv; `--network none --read-only --cap-drop ALL --no-new-privileges --user 65532:65532`, pids/cpu/memory/swap caps, `noexec` tmpfs; only fixed `--env` values (host env not passed in); images pinned by regex and resolved to `sha256:` IDs in production; bind mounts verified under work root with a symlink walk; output collection rejects symlinks/non-regular files with byte/file caps. Remote clients: HTTPS-only origin, ≥32-char bearer, redirect suppression, health attestation, byte caps, per-file `compare_digest`.
- **Auth core** — single-use login transactions (`DELETE … RETURNING`), expiry enforced in SQL for transactions and sessions, session token generated server-side post-verification (no fixation), `__Host-` cookies with `secure`/`httponly`/`samesite=lax`, workspace key derived from `issuer\0subject` hash (never request data). CSRF: `get_principal`/`_boundary` requires matching Origin on non-safe methods in OIDC mode and rejects `sec-fetch-site: cross-site`; admin routes validate loopback Host + client IP + Origin.
- **Authorization / IDOR** — every non-public router mounts `Depends(get_principal)`; conversations/sources/gaps/decisions/artifacts/batches/runs all filter by `workspace_key`/`scope_key` in SQL. No secrets in responses (`secret_configured` only; `_safe_error` redaction).
- **Job concurrency** — claim uses `FOR UPDATE SKIP LOCKED` + lease-token fencing on every transition; stale publishers lose on `lease_token`/`lease_expires_at` checks; cancel/claim race serialized by row locks; `finish_job_failure`/`recover_expired` SQL is status/`finished_at`-consistent with the schema CHECKs. Conversation abandon correctly returns the request borrow before `JobStore.cancel`.
- **Budget accounting** — `reserve_before_call` CAS-guarded, `account_usage` transactional (partial settles roll back), unknown usage intentionally stays reserved, `settle_operation_call` one-shot guard prevents double-settle.
- **Citation mapping** — `citation_numbers_visible` bounds every number to `[1, len(chunks)]` and rejects reversed/oversized ranges, so `chunks[number - 1]` cannot go out of range; every paragraph must carry a citation or the answer is rejected; `persist_response` resolves against the same chunk list.
- **Fusion / eval edge cases** — min-max division guarded (`high == low → 1.0`); `min_score` filter runs before dedupe/quota/allocation; empty-safe quota slices; deterministic tie-break keys `(-score, source_id, chunk_index, chunk_id)`; keyword tsquery parameterized/character-sanitized; `_recall` guards empty expected; recall denominator guarded.
- **Control loops** — `ToolConversationController.run` bounded by `max_provider_calls`/`max_tool_calls`; the planned-run repair loop bounded by per-unit repair quota and the shared operation budget; tool resource IDs enforced against `ToolScope.resource_ids` before handlers run.
- **Tool registry / ledger** — strict typed I/O, capability gate, resource-membership checks, output byte caps, handler exceptions wrapped without leakage. Fail-closed ledger transitions; failures marked `unknown`; replay returns only previously validated output; `UNIQUE (operation_id, call_id)` blocks double-claims. Calculator is pure bounded AST arithmetic.
- **SQL safety** — no f-string/format-built SQL anywhere in the loader path; `%%` escaping in `queries/schema.sql` only used where psycopg collapses it; join cardinality verified (`locator_id` global PK; `courses` UNIQUE per collection+code); `upsert_source` conflicts land on the PK arbiter and match `sources_current_origin_uq`.
- **Migration runner** — advisory `pg_advisory_xact_lock(1938306)` serializes runners; per-migration savepoints make `apply_migrations` all-or-nothing; baseline vs legacy-history dispatch (`_manifest_for_rows`) correct. Legacy archive 001-032 spot-checked against `032_schema.sql`.
- **Backup/restore integrity** — archive member names reject traversal/backslash/symlinks; manifest↔archive sets cross-checked; per-member sha256 verified at extraction; the hash-vs-DB check plus re-hash during copy close the TOCTOU window; restore validates every DB-referenced file inside the uncommitted path-update transaction and rolls back pointers + removes files on failure.
- **Frontend state/races** — mock/http contract parity (method-by-method); `credentials: 'same-origin'` is fine given the backend Origin boundary; `react-markdown@9` default `urlTransform` strips `javascript:`; draft persistence (`documentDraft.parseIdList`, theme `localStorage`) is try/caught; ConversationPanel `viewEpoch`/`ownsPoll`, `useGapQueue` `readEpoch`, `useArtifactGeneration` `stopped`, and `useGapResolution`/`useReindex` `active` guards all correct (the two `busy`/loading epoch-guarded `finally` leaks are the exception, reported above); only three non-null assertions in the frontend, all runtime-safe.
- **Provider seams** — L2 normalization applied (`granite.py:173,179`); CPU default; granite assembly closed to safetensors + fixed modules; hf-local rejects oversized prompts rather than truncating; greedy sampling preserved; SDK clients built with `max_retries=0`/`attempts=1` so job-layer retries can't double-bill.
- **Factory isolation** — concrete providers imported only inside `app/llm/factory.py`, `app/embeddings/factory.py`, the provider modules themselves, tests, and the allowed manual `audit_granite_compatibility.py`.
- **Config loaders** — `retrieval.toml` / `routing.toml` / nudges / departments strict (unknown keys error) and the shipped keys line up; `programs.toml` validated with coverage checks. `Settings`/`OIDCConfig` cross-validate TTLs and limits at startup; secrets are `repr=False`.
- **Lifecycle / ports** — `serve` runs uvicorn inside the DB context; 8000 (serve/compose) and 8080 (sandbox worker, `WORKER.md` ↔ `compose.worker.yaml`) consistent; scraper `get()` capped at 3 attempts with `Retry-After`; only public Coursedog endpoints hit; manual scripts' `rmtree` calls are root-guarded; logging never retains SDK strings or secrets.
