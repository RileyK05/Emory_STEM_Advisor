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

`transformers==4.38.2` and `sentence-transformers==3.0.1` are pinned on purpose (see AGENTS.md); a fresh Docker build and Linux runtime check are still pending.

## Run without Docker

From the repository root, with the project venv active, `python dev.py` starts the
worker, the Vite dev server and the API (embedded Postgres when `DATABASE_URL` is
unset), and points the frontend at the API by writing `frontend/.env.local`.
Ctrl-C stops all three and shuts Postgres down cleanly.

The image is intended to install CPU-only PyTorch without downloading model weights during build; the first model-backed request may download them. Configure provider settings through a root `.env` copied from [.env.example](.env.example). Never commit keys or credentials.

With `ADVISING_RESEARCH_ENABLED=true`, `TOOLS_ENABLED=true`, and a permitted
tools-capable connection, the advisor can maintain a private plan and notebook,
revisit earlier passages, and search older conversation turns. Published work
carries between turns in the same chat; source selection and fresh citation checks
still apply. Long research chats use paged history instead of the legacy 12 KB
transcript limit. API and worker processes must both load the same configuration;
restart them after changing it. See the [working-space contract](docs/architecture.md).

## Run backend checks against real Postgres

Tests use a disposable Postgres service, separate from the development database:

```sh
docker compose --profile test up -d --wait postgres-test
docker compose --profile test run --build --rm backend python -m pytest
```

For host Python 3.12 checks, install `backend[dev]`, start the same test service,
then from `backend/` run (pytest intentionally ignores `.env`):

```powershell
$env:TEST_DATABASE_URL="postgresql://advisor:advisor@localhost:5433/advisor_test"
..\.venv\Scripts\python -m pytest
..\.venv\Scripts\python -m ruff check .
```

An external `TEST_DATABASE_URL` must explicitly name a disposable database ending
in `_test`, different from `DATABASE_URL`. Tests clear its application data.
Without it, the fixture creates an isolated embedded `pgserver`; it never selects
the development database. The Docker path avoids embedded startup limitations
on Windows. Password authentication works without `PGPASSWORD`.

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
[migrations](backend/app/db/migrations/) (`033` onward).
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
python -m scripts.migrate                                   # applies every pending migration
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

Private OIDC generation requires evaluated connection routing. Configure an
encrypted connection profile with every required data scope (including
`private_workspace`), set `ROUTING_ENABLED=true` and
`ROUTING_EVAL_SUITE_HASH`, run that evaluation, then record its actual outcome:
`python -m scripts.record_profile_evaluation PROFILE_ID --suite-hash HASH --status passed --metrics RESULTS.json`.
The command records an operator attestation; it does not run or verify the
evaluation. Editing the profile requires evaluating its new revision. An
environment API key alone cannot serve a private workspace.

Deleting a workspace creates a permanent deletion tombstone and revokes its
sessions. Signing in again with the same identity does not reopen that workspace;
writes return 410. Re-enrollment would require a separate account-lifecycle policy.
