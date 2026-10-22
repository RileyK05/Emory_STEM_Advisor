# Emory STEM Advisor

A grounded advising system with a Python API, Postgres/pgvector retrieval, and a React frontend. The architecture contract and current implementation status live in [docs/architecture.md](docs/architecture.md).

The frontend ships three entries from one codebase: **Main** for students and
advisors, **Admin** at `/admin` (`/admin.html` under Vite) for institutional
management and review, and **Debug** at `/debug` (`/debug.html`) for an expiring,
scoped prompt/retrieval session. Main, Admin, and Debug share authentication but
load separate bundles. The four session roles are student, advisor, admin, and
auditor.

## Repository layout

| Location | Purpose |
| --- | --- |
| `backend/app/` | Backend services grouped by domain; HTTP handlers in `api/routes/` |
| `backend/configs/` | Versioned settings and the [course discovery directory](backend/configs/catalog-directory.md) |
| `backend/scripts/` | Routine commands; `smoke/` for live checks, `manual/` for one-off probes |
| `backend/tests/` | Offline tests, including real Postgres checks |
| `frontend/src/components/` | Feature folders containing their panels, components, and helpers |
| `frontend/tests/` | Client/browser checks, with the API test server in `fixtures/` |
| `docs/` | Architecture contract, requested handoffs in `plans/`, existing findings in `audit/` |
| `data/` | Ignored runtime data, uploads, catalog snapshots, and test scratch data |
| `sandbox/` | Isolated execution worker deployment |

## Run the local Linux development stack

Install Docker Desktop with the Linux container engine enabled, then run from the repository root:

```sh
docker compose up --build
```

The frontend is at http://localhost:5173, the API at http://localhost:8000, and Postgres is published on localhost:5432. The API applies database migrations when the first database-backed request arrives. Postgres data persists in a named volume; uploaded and ingested files live in the gitignored `data/` directory.

Use the Vite origin (http://localhost:5173) to reach the API through its proxy. A host-side request sent straight to http://127.0.0.1:8000 arrives from the compose bridge gateway address, not loopback, so development auth returns 401; that is expected, not a credential problem.

The backend image runs as root with `--reload` on 0.0.0.0 as development scaffolding only. Do not deploy it as-is; a production image must run as a non-root user without the reloader.

The Docker stack is development scaffolding and has not been exercised on this machine. The backend dependency resolver and installed-environment `pip check` pass with `sentence-transformers==3.0.1` and the MiniCPM-required `transformers==4.38.2`. The guarded Granite loader was revalidated against the previous embedding baseline with identical CPU vectors, ranking, and token boundaries. A fresh Docker build and Linux runtime checks remain required.

The image is intended to install CPU-only PyTorch without downloading model weights during build; the first model-backed request may download them. Configure provider settings through a root `.env` copied from [.env.example](.env.example). Never commit keys or credentials.

With `ADVISING_RESEARCH_ENABLED=true`, `TOOLS_ENABLED=true`, and a permitted
tools-capable connection, the advisor can maintain a private plan and notebook,
revisit earlier passages, and search older conversation turns. Published work
carries between turns in the same chat; source selection and fresh citation checks
still apply. Long research chats use paged history instead of the legacy 12 KB
transcript limit. API and worker processes must both load the same configuration;
restart them after changing it. See the [working-space contract](docs/architecture.md).

## Run backend checks against real Postgres

Compose sets `TEST_DATABASE_URL` to its healthy Postgres service, so DB tests execute instead of being skipped:

```sh
docker compose run --build --rm backend python -m pytest
```

To run checks from a host Python 3.12 environment, install `backend[dev]` and start the database with `docker compose up -d postgres`. Copy `.env.example` to `.env`, then from `backend/` run:

```powershell
..\.venv\Scripts\python -m pytest
..\.venv\Scripts\python -m ruff check .
```

The host test fixture accepts `TEST_DATABASE_URL`; the embedded `pgserver` fallback is available where its bundled Postgres can start. The Docker path avoids that fallback on Windows.

If Windows denies access to pytest's default temporary folder, pass
`--basetemp ../data/test-runs/pytest-<unique-run-id>` from `backend/`. Use a fresh
directory for each run; pytest clears an existing basetemp, so concurrent runs
must never share it. Keep test scratch under ignored `data/test-runs/`.

## Optional HTML visual review

HTML artifacts can be rendered to a screenshot and reviewed by a
vision-capable connection profile before publication. The screenshot runs
inside the disposable sandbox host, not the API or worker process.

Build the fixed browser image on the separate sandbox host:

```sh
docker build -f sandbox/Dockerfile.browser -t emory-sandbox-browser:1.50.0-v1 .
```

Set `VISUAL_ARTIFACT_REVIEW_ENABLED=true`, `SCREENSHOT_RENDERER=sandbox`,
`SANDBOX_SCREENSHOT_IMAGE`, and the existing `SANDBOX_REMOTE_URL` /
`SANDBOX_BEARER_TOKEN`. The selected profile must declare the `vision`
capability. The renderer aborts all network routes and disables JavaScript; a
failed or unavailable review keeps the original validated artifact instead of
blocking generation. `SCREENSHOT_RENDERER=playwright` exists only as a local
development convenience and is not the deployed path.

## Schema baseline and existing data

Fresh databases apply [032_schema.sql](backend/app/db/baseline/032_schema.sql)
once, followed by the post-baseline
[migrations](backend/app/db/migrations/) (`033`–`069`).
Existing databases and backups keep their original migration history and upgrade
through the byte-preserved [legacy archive](backend/app/db/legacy_migrations/),
then the same post-baseline migrations. Neither path requires resetting the database.
From `backend/`, `python -m scripts.schema_baseline` verifies that both paths
produce the same tables, constraints, indexes, triggers, and functions.

To copy a verified backup into a separate embedded database using the baseline,
run `python -m scripts.restore_consolidated BACKUP --data-dir NEW_DIRECTORY`.
The destination must not exist. The command verifies all application rows,
foreign keys, source version state, and referenced files, then stops the new
server. It leaves the original database and configuration untouched. Use the
[backup and recovery guidance](docs/architecture.md#persistent-backup-and-recovery)
for the existing recovery commands and constraints.

The course exploration graph is fixed departments → learned topics → course
leaves. It stores containment, with no lateral course similarity links.
`backend/configs/departments.toml` controls fixed subject membership; topic
clustering runs separately inside each unit. Semantic chunk retrieval supplies
LLM evidence, and the deterministic prerequisite graph supplies requirements.

## Reproduce the corpus from an empty `data/`

The repository does not commit catalog, program, or eval data. From `backend/`,
the following rebuilds the same corpus a teammate can verify against:

```powershell
python -m scripts.migrate                                   # reaches migration 069
python -m scripts.scrape_programs --skip-departments       # catalog PDF -> data/programs/catalog/
python -m scripts.ingest --collection NAME --origin-manifest ../data/programs/ingest-manifest.json ../data/programs/catalog/*
python -m scripts.import_programs --collection NAME        # after ingest: binds programs to their sources
python -m scripts.reconcile_source_origins --collection NAME --manifest <verified-legacy-groups.json>
```

Notes:

- The origin manifest ties each canonical Markdown file to the exact scrape SHA.
  `data/programs/ingest-manifest.json` is the scraper's output, not hand-written.
- Ingest before `import_programs`: a program binds only to a current indexed
  source with the exact identity the scraper recorded, so importing first leaves
  every program `unbound`. The import prints bound / unresolved / unbound counts;
  the 2026-10-11 catalog binds all 152.
- A manifest entry may carry a `provenance` object that classifies the source
  (`institutional_catalog`, `institutional_department`, `institutional_policy`,
  `institutional_publication`, or `career_reference`) with optional `publisher`,
  `source_url`, `edition`, `geography`, `occupation_id`, `published_at`,
  `acquired_at`, `attribution`, and the publication facets `title`,
  `institution`, `issuing_unit`, `document_roles`, `applicability`, and
  `applicability_status`. The scraper writes it for catalog and department files;
  curated career references use the same `--origin-manifest` import with a
  hand-verified entry, and the reviewed advising-upload pilot is prepared by
  `scripts.prepare_advising_documents` and ingested the same way. Ordinary uploads
  cannot set it.
- `reconcile_source_origins` merges duplicated legacy program sources only for
  groups explicitly verified; keep that verification explicit rather than
  merging on a name match.
- Retire the bogus "Department of Veterans Affairs" program source with the
  existing named query. The catalog splitter fix prevents it on a fresh scrape,
  so on a clean rebuild this is a no-op.
- Rebuild the atlas and simgraph (`scripts.import_atlas`, `scripts.build_simgraph`)
  only if the atlas changed.
- Last measured on the development database: 218 current of 372 total sources,
  152 programs. A clean rebuild should reach the same counts.

## Continuous integration

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs the backend suite
against a `pgvector` Postgres service, the schema-parity check, and the frontend
`typecheck`/`build`/`format:check`/`test` on every push to `main` and every pull
request. The frontend integration job is experimental and allowed to fail until
its Chromium and Postgres wiring on CI is settled.

## Azure deployment scaffold

[infra/README.md](infra/README.md) documents the two-phase Bicep deployment:
production API/worker/migrate images, a Vite+nginx web image, Container Apps,
PostgreSQL Flexible Server with the `vector` extension, Blob + Azure Files,
Key Vault, monitoring, and a migrate job. `bash deploy/azure/deploy.sh` builds
the images in ACR, sets the Key Vault secrets, deploys the apps and runs the
migration.

This is a base, not the school's final topology: it uses public ingress, a
PostgreSQL Azure-services firewall rule, Azure Files for `DATA_DIR`, and the
existing OIDC adapter. The custom internal sign-in, private networking, Blob
adapter and retention policy remain in
[docs/plans/azure-hosting-requirements.md](docs/plans/azure-hosting-requirements.md).

## Frontend checks

From `frontend/`, run `npm ci`, then `npm test` and `npm run build`.
`npm run test:integration` also exercises the browser against the real API and a
temporary Postgres database with offline fake models. It needs the backend dev
dependencies and a locally installed Chromium browser.

Run `python -m scripts.worker` from `backend/` to process native artifact jobs.
Generation uses the configured capable provider; local HF experiments do not
support constrained artifact output or native tool calls. Manual native artifacts
remain available. See the architecture for evaluated routing, operation limits,
private OIDC preparation, and [sandbox/WORKER.md](sandbox/WORKER.md) for the
dedicated Office/code worker. Production activation and live isolation checks
remain separate from local scaffolding.
