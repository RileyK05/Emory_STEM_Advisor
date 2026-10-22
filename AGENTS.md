# AGENTS.md

RAG advisor (Python backend + React frontend). The data layer is built
(keyword, embeddings, prerequisite graph, similarity graph; fusion, traces,
eval runner). An HTTP API serves retrieval, grounded answer generation,
sources, and recommendations; the frontend runs on a mock client by default
and has a real `HttpClient`. An optional research advisor (migrations 037–040,
`ADVISING_RESEARCH_ENABLED`) compares programs and documents under a pinned
source scope and returns a typed, evidence-linked decision. `docs/architecture.md`
is the single design doc and the contract that new code and the frontend API
types must match. It
folds in the Stacks provenance notes and an appendix of prior-system failure
modes; there is no separate build plan.

## Layout / entrypoints

- `backend/app/` — Python package.
  - `llm/`, `embeddings/` — provider seams. Each has `base.py` (Protocol),
    `factory.py` (the only place concrete providers are imported or
    instantiated), with local and native API implementations. Never import a
    concrete provider outside its factory.
  - `db/` — `connection.py` (the only place psycopg connects: `connect()`, plus
    `connector(settings)`, the pooled factory the API and worker use; set
    `DB_POOL_MAX_SIZE=0` to disable pooling), the migration
    runner, and the named-query loader. Fresh installs apply `db/baseline/032_schema.sql`.
    `db/legacy_migrations/001`–`032` is the immutable upgrade archive for existing
    databases/backups. Future numbered changes start at `db/migrations/033_*.sql`;
    the migration directory head is `072_course_lifecycle.sql`. Never edit an applied file or
    the baseline. Every
    SQL statement is a `-- name:` block in `db/queries/*.sql`; no inline SQL in
    app code.
  - `ingest/` — extract, normalize, locators, chunk, embed (`pipeline.py`),
    verified source provenance (`provenance.py`), and the deterministic preparation
    of explicitly reviewed advising documents (`advising_documents.py`).
  - `atlas/` — course atlas records, the deterministic requisites parser, the
    importer, and `catalog.py` (Coursedog catalog page -> atlas record).
  - `advising/` — read-only, citation-scoped atlas/topic snapshots, the pure
    deterministic prerequisite/corequisite sequence planner, mention comparison
    (`comparison.py`), the scoped catalog reader (`catalog.py`), source/applicability
    notices (`applicability.py`), the deterministic program-rule audit engine and
    service (`audit_engine.py`, `audit_service.py`), and program comparison
    (`comparison_service.py`). The research advisor's persisted data layer
    (`research_repository.py` pins an immutable source/program manifest;
    `research_store.py` records runs, steps, evidence and decisions;
    `research_types.py` holds the bounded types). Missing evidence, alternatives
    and conditions produce blockers. Never infer graph facts with a model; an
    audit is a named program/cohort result, not a whole-degree or semester claim.
  - `programs/` — program requirements: the official catalog PDF split per
    program (`catalog_pdf.py`), department pages converted to Markdown
    (`pages.py`, ingested as ordinary documents), the deterministic parser
    (`requirements.py`) that keeps each requirement line raw and reads off course
    codes and explicit "choose N" counts into `programs` / `program_items` /
    `program_course_mentions` (migration 031), and the exact-identity binder
    (`binding.py`, `importer.py`) that ties each program to the source revision it
    was parsed from. `rules.py` and `review_service.py` hold the reviewed
    executable rule releases and approval workflow. Never feeds the prerequisite
    graph; no model.
  - `retrieval/` — config loader, seams (`keyword`, `embed`, `prereq`),
    fusion, trace, `retrieve()`, eval runner, and the scoped catalog
    `directory` that opt-in query planning (`generation/query_plan.py`,
    `QUERY_PLANNING_ENABLED`) reads before retrieval.
  - `generation/` — assembles the grounded answer prompt and maps the model's
    inline citations back to chunks, plus the research loop
    (`research_controller.py`, `research_tools.py`, `research_final.py`), factual
    claim units (`claims.py`), and opt-in query planning (`query_plan.py`). Calls
    the provider seam; never imports a concrete provider.
  - `api/` — FastAPI app factory, shared dependencies, serializers, and
    request/response schemas (mirroring
    `frontend/src/api/types.ts`). Route modules live in `api/routes/`, grouped
    by feature (`corpus`, `artifacts`, `conversations`, `connections`, and so on).
    JSON routes declare a `response_model` from `schemas.py`; those models are
    `extra="allow"` and routes use `response_model_exclude_unset=True`, so a
    handler's omitted keys stay omitted and unknown keys pass through. Model
    names match the TypeScript interfaces, and `tests/test_contract_drift.py`
    fails when their field sets diverge. Routes that return files, redirects,
    or a hand-built `JSONResponse` (headers) have no model. The API
    process and the job worker both build their provider, embedder, retrieval
    config, and connection runtime through `app/runtime.build_runtime` (the only
    caller of the provider and embedder factories outside scripts); `create_app`
    stores the result on `app.state`.
  - `auth/` — server-side sessions and OIDC login; `get_principal` is the one
    authentication dependency, with the student/advisor/admin/auditor capability
    matrix and scoped support grants (`capabilities.py`, `grants.py`). The job
    worker purges expired login rows and long-revoked sessions hourly
    (`auth/housekeeping.py`). `connections/` — encrypted provider connection
    profiles, evaluated routing selection, and the profile runtime.
  - `admin/` — institutional user/role store, audited admin reads and destructive
    actions (conversation delete, job cancel, empty-collection CRUD), source
    review/lifecycle, catalog-refresh batches, prompt versions, the invalidation
    consumer, and the admin API's data layer. `debug/` — the allowlisted runtime
    override registry, scoped admin debug sessions, and request-scoped
    application of those overrides. Neither exposes secrets, auth, database, or
    routing credentials.
  - `jobs/` — persistent job store and worker runtime, the dependent artifact
    run plans, and provider (batch) tracking. `operations/` — persisted
    token/call/time budgets shared across a logical generation operation.
  - `conversations/` — private persisted conversations, the streaming turn
    worker, and debug experiment records (`experiments.py`). `gaps/` —
    human-reviewed retrieval gaps and feedback triage (`feedback.py`).
  - `records/` — revisioned academic records, deterministic CSV import, and
    staff verification. Student-provided or imported facts stay unverified until
    an authorized institutional action. `privacy/` — owner-scoped exports,
    expiring downloads, deletion, and retention handling. `quotas/` — per-student
    query/token/spend reservations and settlements.
  - `flags/` — cohort/workspace feature policy with environment ceilings.
    `pricing/` — dated model price schedules and cost/cache accounting.
    `observability/` — loopback metrics and alert evaluation.
    `storage/` — local/dual storage seam and object metadata; a remote Blob
    provider is still a deployment gate.
  - `artifacts/` — native document/workbook/HTML specs, immutable revisions,
    import/export (Markdown, DOCX, XLSX), and opt-in HTML visual review
    (`visual_review.py`: request a bounded screenshot from the dedicated
    browser sandbox, send it to a vision-capable profile, apply one validated
    correction). `tools/` — allowlisted typed model
    tools and their execution ledger. `sandbox/` — disposable isolated
    execution (Docker/gVisor runner, remote worker, Office conversion).
  - `ops/` — readiness probe and `backup.py` (checksummed database plus
    original-file backup/restore). `util/` — shared low-level helpers
    (`digest.json_digest`/`canonical_json_bytes`, `workspace` key rule).
  - `simgraph/` — department topic graph: fixed department membership, within-department
    clustering, human nudges, goal exploration, containment tree view. No lateral
    course similarity links. `configs/departments.toml` configures fixed membership.
- `backend/configs/` — `retrieval.toml` (versioned tunables; unknown keys are
  errors), `routing.toml` (task-to-model-role routing signals),
  `simgraph_nudges.toml` (hand-written cluster nudges), and
  `programs.toml` (hand-picked department requirement pages),
  `advising_sources.toml` (the reviewed allowlist for the local advising-upload
  pilot; unlisted files are excluded), and `catalog-directory.md` (the supplied
  discovery directory for LLM planning; not citable evidence).
- `backend/scripts/` — `scrape_catalog`, `scrape_programs`, `import_programs`,
  `ingest`, `import_atlas`, `build_simgraph`, `eval_retrieval`, `serve`,
  `worker` (background jobs), `migrate`, `backup`, `rotate_profile_key`,
  `schema_baseline` (disposable schema parity check), `restore_consolidated`
  (verified backup into a new embedded baseline database),
  `reconcile_source_origins` (explicit legacy program versions), and
  `prepare_advising_documents` (prepare the reviewed advising-upload pilot;
  `--check` validates the registry) (run with
  `python -m scripts.<name>` from `backend/`). They use `DATABASE_URL`, or an
  embedded `pgserver` database under `DATA_DIR/pgserver` when it is unset.
  `scripts/smoke/` holds explicit model/service smoke checks, invoked as
  `python -m scripts.smoke.<name>`. `scripts/manual/` holds one-off probes and
  compatibility audits. These can load real models or use the network; never
  add them to the test suite. Print diagnostics in the terminal; do not create status
  reports or saved test output unless the user requests them.
- `backend/tests/` — fast tests. They never load models or touch the network
  (use `tests/fakes.py`; shared helpers such as `ingest_text` and `api_client`
  are in `tests/helpers.py`). Any test that requests `db`, `collection`,
  `migrated_dsn` or `pg_dsn` is auto-marked `db` and runs against a real
  Postgres: a temporary `pgserver` instance, or `TEST_DATABASE_URL` if set. A
  skipped DB test is a failure. `DATA_DIR` is pointed at a temp dir for the
  session. The `db` fixture cleans only the tables a test wrote to (a `DELETE`
  with foreign keys off; it falls back to `TRUNCATE` without superuser), so
  persistent-store tests using fresh connections through `migrated_dsn` must
  also request the `db` fixture so their committed rows are cleaned up. Tests
  that change the schema or migration history use `isolated_database` in
  `test_db_migrate.py`, never the shared session database.
  Real HTTP transports are blocked in pytest; inject a mock transport instead.
- `frontend/src/api/` — `types.ts` mirrors the architecture doc; `client.ts`
  is the single factory the app uses. `VITE_API_MODE` selects `mock` (default)
  or `http`; `HttpClient` implements the workspace-scoped HTTP contract.
- `frontend/src/admin/` and `frontend/src/debug/` — separate Vite entry bundles
  for the Admin and Debug surfaces; they share `SessionGate`, `Shell`, and the
  API client with Main but are not shipped in its entry. `src/app/SessionGate.tsx`
  is the shared authentication gate. The Admin bundle manages users/roles,
  audited inspection, support grants, source/program review, catalog-refresh
  batches, flags/cohorts, feedback triage, prompt promotion, experiments,
  privacy, and telemetry; the Debug bundle owns an expiring scoped override
  session and its test chat.
- `frontend/src/components/` — one chat with artifacts made from it. `shell/`
  is the layout (top bar, sidebar, dialogs, the mascot theme switch: Swoop =
  light Emory blue/gold, Dooley = dark black/white). `chat/` has a single
  `ChatController` with two engines: `useSavedChat` (HTTP conversations,
  streamed turns, never replayed) and `useDemoChat` (mock, stateless query,
  nothing saved). `artifact/` is the side panel, editor canvas, and generation
  dialogs; which chat an artifact belongs to is kept in browser storage plus the
  artifacts a message referenced. `source/` is the sources and explore dialogs;
  `batch`, `gap`, and `connection` render inside the Tools dialog; `records/`,
  `audits/`, `feedback/`, and `privacy/` hold the student workflows added with
  the completion plan. Pure helpers in `artifact/*.ts` are unit-tested directly.
  `frontend/tests/fixtures/` holds the offline real-API browser test server.
- `data/` — gitignored: raw uploads, the dev database, atlas files
  (`data/atlas/*.jsonl`), eval set (`data/eval/retrieval.jsonl`).
- `sandbox/` and `compose.yaml` — worker/sandbox images and the local compose
  stack (see `sandbox/WORKER.md`).
- `infra/` and `deploy/` — the Azure base: Bicep for Container Apps, PostgreSQL
  Flexible Server + pgvector, storage, Key Vault and monitoring; production
  API/worker/migrate and nginx images; and the two-phase
  `deploy/azure/deploy.sh` (see `infra/README.md`). Public ingress, Azure Files
  for `DATA_DIR`, and the existing OIDC adapter are deliberate base limitations.
- `docs/` — `architecture.md` (the one design contract), `plans/` (requested
  implementation handoffs), and `audit/` (existing review findings).

## Hard rule: the prerequisite graph is deterministic

The prerequisite graph is parsed deterministically from Emory's course atlas.
**An LLM never writes, edits, infers, or fills in any of its nodes or edges**:
not at ingest, not as a fallback when parsing fails, not as a suggestion. A
prerequisite the parser can't handle is a parser fix or a manual fix, never a
model call. `tests/test_no_llm_in_prereq.py` fails if `app/atlas/` or the
prerequisite seam imports a model or provider.

Course requirement authority: the public Course Atlas (`atlas.emory.edu`) takes precedence over the catalog when they disagree (user decision, 2026-10-21). Use exact captured Atlas registration restrictions, not model intuition, to update the graph. Oxford is a separate campus listed in the same Atlas; import its displayed course identities alongside Atlanta records and preserve campus/suffix codes. An absent College master row is a coverage gap, not evidence that an Oxford course is invalid. `scripts.reconcile_atlas` prepares and explicitly publishes an Atlas-primary master from verified saved offering snapshots, with clearly labeled catalog fallbacks only where Atlas evidence is absent. Never run a plain catalog import over an Atlas-primary master.

The similarity graph's nudges file is also hand-written data. Don't generate
it with a model.

## Prior project (Stacks)

The design grew out of an earlier project, Stacks. It is a reference, not a
template: the two do not map 1:1. The "Provenance: this grew out of Stacks"
section of `docs/architecture.md` lists what is kept, adapted, dropped, and
deferred. Do not bring in a Stacks pattern, table, or term unless that section
lists it.

## Commands

Backend (Python 3.12):

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "backend[dev]"    # Windows venv layout
```

From `backend/`:

```bash
../.venv/Scripts/python -m pytest                       # all tests, incl. DB
../.venv/Scripts/python -m ruff check .
../.venv/Scripts/python -m scripts.ingest --collection NAME FILE [FILE ...]
../.venv/Scripts/python -m scripts.scrape_catalog        # courses -> data/atlas/emory_college.jsonl
../.venv/Scripts/python -m scripts.scrape_catalog --offline   # rebuild JSONL from raw cache
../.venv/Scripts/python -m scripts.scrape_programs       # majors/minors -> data/programs/{catalog,departments}/, programs.jsonl
../.venv/Scripts/python -m scripts.ingest --collection NAME --origin-manifest ../data/programs/ingest-manifest.json ../data/programs/catalog/* ../data/programs/departments/*
../.venv/Scripts/python -m scripts.import_programs --collection NAME   # after ingest: parsed requirements -> DB, bound to exact sources
../.venv/Scripts/python -m scripts.import_atlas --collection NAME FILE
../.venv/Scripts/python -m scripts.reconcile_atlas               # prepare Atlas-primary JSONL from verified saved captures
../.venv/Scripts/python -m scripts.reconcile_atlas --publish     # publish Atlas-primary records; retains labeled catalog fallbacks
../.venv/Scripts/python -m scripts.build_simgraph --collection NAME [--nudges FILE]
../.venv/Scripts/python -m scripts.prepare_advising_documents           # reviewed uploads -> data/staging/
../.venv/Scripts/python -m scripts.eval_retrieval [--cases FILE]
../.venv/Scripts/python -m scripts.serve                 # HTTP API on :8000
../.venv/Scripts/python -m scripts.schema_baseline       # baseline == legacy final schema, disposable DBs
../.venv/Scripts/python -m scripts.restore_consolidated BACKUP --data-dir NEW_DIRECTORY
../.venv/Scripts/python -m scripts.smoke.local_model    # slow, downloads weights
../.venv/Scripts/python -m scripts.smoke.embeddings     # ~250 MB download
```

`ruff` (line-length 100) is the linter; `pytest` config sets `testpaths=["tests"]`.
If Windows denies access to pytest's default temp directory, use a fresh unique
`--basetemp ../data/test-runs/pytest-<run-id>` from `backend/`. Never reuse an
active run's basetemp or put new test scratch beside the source folders.

Frontend (from `frontend/`):

```bash
npm run dev         # Vite dev server
npm run typecheck   # tsc --noEmit
npm run build       # tsc --noEmit && vite build — type errors fail the build
npm test            # offline client and Chromium behavior checks
npm run test:integration # real API/Postgres/Chromium, offline fake models
npm run format:check    # Prettier over src/**/*.{ts,tsx}; `npm run format` fixes
```

## Constraints worth not re-learning

- `DB_POOL_MAX_SIZE` bounds each role's pool per process. Conversation workers
  separate `conversation_execution` from the default control/lease pool, so
  their combined bound is twice the setting; tool execution ledgers use a
  separate `tool_execution` role. Run API handlers release bootstrap and pooled
  request borrows before store-owned transactions — conversation abandon returns
  its request borrow before `JobStore.cancel`; never nest same-pool borrows.
- Authoritative sources use `origin_key` plus `is_current`; replacements stage
  before atomic publication. Keep historical sources/chunks for evidence, and
  filter current retrieval on indexed/current sources. Atlas replacement never
  deletes the previous source before preparing the next deterministic graph.
- Program acquisition timestamps/download URLs belong in sidecars, outside
  canonical Markdown identity. Use the scraper's exact-SHA origin manifest for
  ingestion; reconcile legacy duplicates only with explicitly verified groups.
- Source provenance is a verified classification
  (`institutional_catalog | institutional_department | institutional_policy |
  institutional_publication | career_reference`) with independent title,
  institution, issuing unit, document roles, applicability facets and an
  applicability status. Only verified importers write it; ordinary uploads cannot
  classify themselves. `is_current` is the latest published version of an origin,
  not the applicability of its rules. The reviewed advising-upload allowlist
  (`configs/advising_sources.toml`) is explicit; never glob the originals.

- `transformers==4.38.2` is pinned exactly: MiniCPM-2B-128k ships remote code
  against the 4.38 API. `sentence-transformers==3.0.1` was revalidated with
  identical Granite CPU vectors, rankings, and token boundaries; its declared
  dependencies accept 4.38.2. Do not change either without re-validating.
  Granite loads only a fixed, trusted module assembly and safetensors weights;
  never restore generic model-authored module loading.
- All env config is in `backend/app/config.py`; nothing model-specific is
  hardcoded. Defaults: `LLM_PROVIDER=hf-local` (MiniCPM-2B-128k),
  `EMBEDDING_PROVIDER=granite` (`granite-embedding-125m-english`, 512-token
  ceiling), `DATABASE_URL` unset (embedded `pgserver`), `DATA_DIR=data/`.
  Embedders default to CPU and L2-normalize output.
- Generation is greedy deliberately so eval runs are reproducible. Don't add
  sampling defaults.
- IDs are content-addressed `uuid5` (see architecture D3) so re-ingest keeps
  citations stable. Don't switch them to random IDs.
- Frontend has Node test suites and Chromium behavior/integration checks plus
  `typecheck`/`build`. Prettier (`format:check`) is the only formatter; no
  frontend linter is configured.

## Testing policy

The suite is small on purpose. Before adding a test:

- Search for the invariant first. Each behavior is tested in one place; a bug
  fix adds a row to the existing table or a case to the existing test, not a
  new file.
- Test behavior, not plumbing. No tests that assert constants (model ids,
  default values), SQL text, route tables, file lists, or what a mock was
  called with. A test must fail only when behavior breaks.
- Table-drive variants (`pytest.mark.parametrize`) instead of near-identical
  functions. Use the shared fakes and helpers; never redefine a provider,
  embedder, or ingest helper per file.
- Use the database only when the logic lives in SQL. Pure logic gets a plain
  unit test. Use `FakeClock`, never `sleep`.
- Do not write tests for manual or one-shot scripts (`scripts/manual/`,
  `scripts/smoke/`) or for test doubles.
- Frontend: rules that are unit-tested in `artifact-modules.test.mjs` are not
  re-tested through the browser; browser tests prove what only a browser can
  (rendering, focus, isolation, drafts surviving errors).

## Emory data sources (updated 2026-10-21)

- Courses: `college.catalog.emory.edu` (Coursedog; robots.txt allows all). Course
  pages embed the record; requisite text comes from the public
  `app.coursedog.com/api/v1/emory_peoplesoft_direct/requirementGroups/<ids>`
  endpoint the page itself calls. The catalog sits behind an AWS WAF that
  answers fast crawls with a CAPTCHA; the scraper stops on it and resumes from
  cache. Never solve or route around the challenge. Coursedog's search/course
  APIs require auth; don't use them. Public Course Atlas search/detail pages
  (`atlas.emory.edu`) are available and are primary for course requirements.
  Click through the public UI and preserve raw captures; do not use hidden APIs.
- Program requirements: not on the catalog website, but in the catalog's
  generated PDF (catalog of record), found via
  `.../pdf-reports/catalogs/<catalogId>`. Department pages in
  `configs/programs.toml` supplement it and can be stale.
- Killing `scripts.serve`'s process tree also kills the embedded Postgres, and
  its crash recovery then hangs on Windows: recovery fsyncs every file in
  `data/pgserver`, including the `log` file the postmaster holds open (sharing
  violation). Recover once with the log outside the data dir, then stop cleanly:
  `pg_ctl stop -D data/pgserver -m fast`, `pg_ctl start -D data/pgserver -o "-p 62999" -l <elsewhere>/pg.log -w`,
  `pg_ctl stop -D data/pgserver -m fast` (`pg_ctl` is under
  `.venv/Lib/site-packages/pgserver/pginstall/bin`).
- Local generation on CPU (MiniCPM-2B, no GPU) needs more than the 60 s
  default: set `LLM_TIMEOUT_SECONDS` / `OPERATION_TIMEOUT_SECONDS`.
