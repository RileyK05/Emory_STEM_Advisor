# AGENTS.md

RAG advisor (Python backend + React frontend). The data layer is built
(keyword, embeddings, prerequisite graph, similarity graph; fusion, traces,
eval runner). An HTTP API serves retrieval, grounded answer generation,
sources, and recommendations; the frontend runs on a mock client by default
and has a real `HttpClient`. `docs/architecture.md` is the single design
doc and the contract that new code and the frontend API types must match. It
folds in the Stacks provenance notes and an appendix of prior-system failure
modes; there is no separate build plan.

## Layout / entrypoints

- `backend/app/` — Python package.
  - `llm/`, `embeddings/` — provider seams. Each has `base.py` (Protocol),
    `factory.py` (the only place concrete providers are imported or
    instantiated), and one impl (`hf_local.py`, `granite.py`). Never import a
    concrete provider outside its factory.
  - `db/` — `connect()` (the only place psycopg connects), the migration
    runner, and the named-query loader. Schema lives in `db/migrations/NNN_*.sql`
    (numbered, append-only: never edit an applied file, add a new one). Every
    SQL statement is a `-- name:` block in `db/queries/*.sql`; no inline SQL in
    app code.
  - `ingest/` — extract, normalize, locators, chunk, embed (`pipeline.py`).
  - `atlas/` — course atlas records, the deterministic requisites parser, the
    importer.
  - `retrieval/` — config loader, seams (`keyword`, `embed`, `prereq`),
    fusion, trace, `retrieve()`, eval runner.
  - `generation/` — assembles the grounded answer prompt and maps the model's
    inline citations back to chunks. Calls the provider seam; never imports a
    concrete provider.
  - `api/` — FastAPI app factory, request/response schemas (mirroring
    `frontend/src/api/types.ts`), dependency wiring, and routes. Concrete
    providers are built once in `create_app` and stored on `app.state`.
  - `simgraph/` — similarity graph: clustering build, nudges, recommendation
    walk, subgraph view.
- `backend/configs/` — `retrieval.toml` (versioned tunables; unknown keys are
  errors) and `simgraph_nudges.toml` (hand-written cluster nudges).
- `backend/scripts/` — `ingest`, `import_atlas`, `build_simgraph`,
  `eval_retrieval`, `serve` (run with `python -m scripts.<name>` from
  `backend/`). They use `DATABASE_URL`, or an embedded `pgserver` database
  under `DATA_DIR/pgserver` when it is unset. `smoke_*.py` are manual checks
  that download model weights; never add them to the test suite.
- `backend/tests/` — fast tests. They never load models or touch the network
  (use `tests/fakes.py`). Tests marked `db` run against a real Postgres:
  a temporary `pgserver` instance, or `TEST_DATABASE_URL` if set. A skipped DB
  test is a failure. `DATA_DIR` is pointed at a temp dir for the session.
- `frontend/src/api/` — `types.ts` mirrors the architecture doc; `client.ts`
  is the single factory the app uses. `VITE_API_MODE` selects `mock` (default)
  or `http`; `HttpClient` is an unimplemented stub that throws on purpose.
- `data/` — gitignored: raw uploads, the dev database, atlas files
  (`data/atlas/*.jsonl`), eval set (`data/eval/retrieval.jsonl`).
- `docs/` — `architecture.md` (the one design doc).

## Hard rule: the prerequisite graph is deterministic

The prerequisite graph is parsed deterministically from Emory's course atlas.
**An LLM never writes, edits, infers, or fills in any of its nodes or edges**:
not at ingest, not as a fallback when parsing fails, not as a suggestion. A
prerequisite the parser can't handle is a parser fix or a manual fix, never a
model call. `tests/test_no_llm_in_prereq.py` fails if `app/atlas/` or the
prerequisite seam imports a model or provider.

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
../.venv/Scripts/python -m scripts.import_atlas --collection NAME FILE
../.venv/Scripts/python -m scripts.build_simgraph --collection NAME [--nudges FILE]
../.venv/Scripts/python -m scripts.eval_retrieval [--cases FILE]
../.venv/Scripts/python -m scripts.serve                 # HTTP API on :8000
../.venv/Scripts/python -m scripts.smoke_local_model    # slow, downloads weights
../.venv/Scripts/python -m scripts.smoke_embeddings     # ~250 MB download
```

`ruff` (line-length 100) is the linter; `pytest` config sets `testpaths=["tests"]`.

Frontend (from `frontend/`):

```bash
npm run dev         # Vite dev server
npm run typecheck   # tsc --noEmit
npm run build       # tsc --noEmit && vite build — type errors fail the build
```

## Constraints worth not re-learning

- `transformers==4.38.2` is pinned exactly: MiniCPM-2B-128k ships remote code
  against the 4.38 API. `sentence-transformers==3.3.1` is pinned as the newest
  line compatible with that transform. Do not bump either without re-validating.
- All env config is in `backend/app/config.py`; nothing model-specific is
  hardcoded. Defaults: `LLM_PROVIDER=hf-local` (MiniCPM-2B-128k),
  `EMBEDDING_PROVIDER=granite` (`granite-embedding-125m-english`, 512-token
  ceiling), `DATABASE_URL` unset (embedded `pgserver`), `DATA_DIR=data/`.
  Embedders default to CPU and L2-normalize output.
- Generation is greedy deliberately so eval runs are reproducible. Don't add
  sampling defaults.
- IDs are content-addressed `uuid5` (see architecture D3) so re-ingest keeps
  citations stable. Don't switch them to random IDs.
- Frontend has no test runner or linter configured; `typecheck`/`build` is the
  only verification.
