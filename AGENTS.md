# AGENTS.md

RAG advisor for Emory STEM students: a Python backend (FastAPI, Postgres +
pgvector) and a React frontend. `docs/architecture.md` is the design contract
that new code and the frontend API types must match; `docs/plans/` holds active
plans and `docs/audit/` the review status.

## Layout / entrypoints

- `backend/app/` — the Python package. Rules that span packages:
  - `llm/`, `embeddings/` — provider seams: `base.py` (Protocol) and `factory.py`,
    the only place concrete providers are imported or instantiated. The API and
    the job worker build providers, embedder, retrieval config and connection
    runtime through `app/runtime.build_runtime`.
  - `db/` — `connection.py` is the only place psycopg connects (`connect()`, and
    `connector(settings)`, the pooled factory; `DB_POOL_MAX_SIZE=0` disables
    pooling). Fresh installs apply `db/baseline/032_schema.sql`;
    `db/legacy_migrations/` is the immutable upgrade archive; numbered changes
    live in `db/migrations/` and the highest number there is the head. Never edit
    the baseline or an applied migration. Every SQL statement is a `-- name:`
    block in `db/queries/*.sql`; no inline SQL in app code.
  - `api/` — app factory, dependencies (`deps.py`: connections, admin-panel
    gates), routes grouped by feature in `api/routes/`, and `schemas.py`, which
    mirrors `frontend/src/api/types.ts`. JSON routes declare a `response_model`
    (`extra="allow"`, `response_model_exclude_unset=True`); model names match the
    TypeScript interfaces and `tests/test_contract_drift.py` fails when their
    field sets diverge.
  - Deterministic data: `atlas/` (course records, the requisites parser,
    importer, `course_writer.course_uuid`), `programs/` (catalog PDF split, page
    conversion, requirement parser, exact-source binding, reviewed rule
    releases), `advising/` (citation-scoped snapshots, the sequence planner,
    program audit engine, comparisons, research advisor data layer),
    `simgraph/` (fixed departments → learned topics → courses; no lateral
    course links). None of these call a model.
  - Generation: `retrieval/` (seams, fusion, traces, eval runner, catalog
    directory), `generation/` (grounded answers and citation mapping, the
    research loop, query planning, claim review), `tools/`, `operations/`
    (shared token/call/time budgets), `jobs/` (job store, worker runtime,
    dependent runs, provider batches), `conversations/`, `artifacts/`,
    `sandbox/` (isolated Office/HTML execution on a separate worker).
  - Accounts and governance: `auth/` (`get_principal` is the one authentication
    dependency; capabilities and support grants), `connections/` (encrypted
    provider profiles, evaluated routing), `admin/`, `debug/` (allowlisted,
    expiring overrides), `records/`, `privacy/`, `quotas/`, `flags/`,
    `pricing/` (price schedules; `record_model_call` writes every model_calls
    row), `gaps/`, `observability/`, `ops/` (readiness, backup).
  - `storage/` — local/blob/dual-read storage seam and `storage_objects`
    queries. Deliberately not wired in yet: groundwork for Azure hosting.
- `backend/configs/` — `retrieval.toml` (unknown keys are errors),
  `routing.toml`, `simgraph_nudges.toml` and `departments.toml` (hand-written),
  `programs.toml`, `advising_sources.toml` (the reviewed upload allowlist), and
  `catalog-directory.md` (planning directory; not citable evidence).
- `backend/scripts/` — run as `python -m scripts.<name>` from `backend/`; they
  use `DATABASE_URL`, or an embedded `pgserver` under `DATA_DIR/pgserver`.
  `scripts/smoke/` (real models/services) and `scripts/manual/` (reusable
  diagnostics) may load models or use the network; never add them to the test
  suite. Print diagnostics in the terminal; do not create status reports or saved
  test output unless the user requests them.
- `backend/tests/` — fast tests that never load models or touch the network.
  Shared fakes are in `tests/fakes.py` and helpers (`ingest_text`, `api_client`,
  `create_user`, `lenient_config`) in `tests/helpers.py`. Any test requesting
  `db`, `collection`, `migrated_dsn` or `pg_dsn` runs against a real Postgres (a
  temporary `pgserver`, or `TEST_DATABASE_URL`); a skipped DB test is a failure.
  The `db` fixture cleans only the tables a test wrote to, so tests using fresh
  connections through `migrated_dsn` must also request `db`. Tests that change
  the schema or migration history use `isolated_database` in
  `test_db_migrate.py`. Real HTTP transports are blocked; inject a mock transport.
- `frontend/src/` — `api/` (`types.ts` mirrors the contract, `client.ts` is the
  single factory; `VITE_API_MODE` selects `mock` (default) or `http`), `admin/`
  and `debug/` (separate Vite entry bundles sharing `SessionGate`, `Shell` and
  the API client), `components/` (one chat with artifacts made from it;
  `shell/` holds the Swoop = light Emory blue/gold, Dooley = dark black/white
  theme switch), `lib/` (shared hooks such as `useAsyncAction`, `useLoad`).
  `frontend/tests/fixtures/` holds the offline real-API browser test server.
- `data/` — gitignored: uploads, the dev database, atlas files, eval sets,
  personal review notes.
- `sandbox/` and `compose.yaml` — worker/sandbox images and the local compose
  stack (see `sandbox/WORKER.md`). `infra/` and `deploy/` — the Azure base
  (see `infra/README.md`).

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
../.venv/Scripts/python -m scripts.ingest_offerings --captures DIR --career NAME --collection NAME [--publish]   # term offerings
../.venv/Scripts/python -m scripts.build_simgraph --collection NAME [--nudges FILE]
../.venv/Scripts/python -m scripts.prepare_advising_documents           # reviewed uploads -> data/staging/
../.venv/Scripts/python -m scripts.eval_retrieval [--cases FILE]
../.venv/Scripts/python -m scripts.serve                 # HTTP API on :8000
../.venv/Scripts/python -m scripts.worker                # background jobs and saved conversation turns
../.venv/Scripts/python -m scripts.load_test --scenario query --requests 200 --concurrency 20   # fake-model load harness
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
npm run test:unit   # source-level client and artifact-module suites (seconds)
npm test            # test:unit, then a browser-test build and the Chromium checks
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
- All backend env config is in `backend/app/config.py` (the separate sandbox
  host reads its own `SANDBOX_*` variables in `sandbox/host_app.py`); nothing
  model-specific is hardcoded. Defaults: `LLM_PROVIDER=hf-local` (MiniCPM-2B-128k),
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
