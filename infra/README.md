# Azure deployment scaffold

This is the deployable base for the advisor: production images, a two-phase
Bicep deployment, and a script that builds, pushes, migrates and prints the
public URL. It is intentionally a **base**, not the final school-hosted
topology. `docs/plans/azure-hosting-requirements.md` lists the decisions still
open (custom identity, retention). PostgreSQL, Blob/File storage and Key Vault
are private-only through a VNet and private endpoints; only the web container
has public ingress.

## What is created

| Resource | Name pattern | Purpose |
| --- | --- | --- |
| Resource group | caller supplied | All resources |
| Container Registry | `<prefix><suffix>` | API/web images (no admin user) |
| Log Analytics + App Insights | `<prefix>-logs-*`, `<prefix>-appi-*` | Container logs and metrics |
| Storage account | `<prefix><suffix>` | `artifacts` blob container + `advisor-data` Azure Files share |
| PostgreSQL Flexible Server | `<prefix>-pg-*` | Database `advisor`, `vector` allowlisted, PITR |
| Key Vault | `<prefix>-kv-*` | `database-url`, `database-direct-url`, `llm-api-key`, `auth-server-key`, `profile-encryption-key`, `oidc-client-secret` |
| User-assigned identity | `<prefix>-id-*` | ACR pull, Key Vault read, Blob data access |
| Virtual network + subnets | `<prefix>-vnet-*` | Delegated Container Apps subnet and private-endpoint subnet |
| Private DNS zones + endpoints | `privatelink.*` | PostgreSQL, Blob, File and Key Vault private access |
| Container Apps environment | `<prefix>-env-*` | VNet-injected; shares Azure Files as `/workspace/data` |
| Container App `api` | `<prefix>-api-*` | FastAPI, internal ingress only, `/health` + `/ready` |
| Container App `web` | `<prefix>-web-*` | nginx + Vite build, the only public ingress, proxies `/api` |
| Container App `worker` | `<prefix>-worker-*` | `scripts.worker --concurrency N`, health/readiness port |
| Container Apps job | `<prefix>-migrate-*` | `scripts.migrate`, run by the deploy script |
| Container Apps job | `<prefix>-backup-*` | Scheduled `scripts.backup` into the data share |
| Alert rules | `<prefix>-alert-*` | API, queue, provider, database and storage alerts |

Azure Files is the interim shared data path. It lets more than one replica read
the same raw/artifact files without a code change, but it is **not** the target
storage design. The Blob adapter in
`docs/plans/azure-hosting-requirements.md` §3 is required before horizontal API
scaling is treated as correct rather than convenient. The storage account has
public network access disabled; Blob and File each have a private endpoint.

The VNet has one subnet delegated to `Microsoft.App/environments` for the
Container Apps environment and one subnet for private endpoints. Private DNS
zones for `privatelink.postgres.database.azure.com`,
`privatelink.blob.*`, `privatelink.file.*` and `privatelink.vaultcore.azure.net`
are linked to the VNet. The API, worker and migrate job reach PostgreSQL, the
storage account and Key Vault through those endpoints. The web container is the
only resource with public ingress; the API is internal to the environment.

## Prerequisites

- Azure CLI logged in (`az login`) with permission to create the resources above.
- Permission to write Key Vault secrets (the scaffold uses RBAC). The deploying
  identity needs **Key Vault Secrets Officer** on the vault. The script retries
  for role propagation; if it still fails, grant that role and rerun.
- `python3` for the parameter file helper.
- Docker is **not** required: images are built in ACR with `az acr build`.
- Build egress to Docker Hub (`python`, `node`, `nginx` base images), PyPI/PyTorch
  and Hugging Face. Mirror the base images and clear
  `PRELOAD_EMBEDDING_MODEL` if the school blocks any of them.

With private endpoints and no public PostgreSQL firewall rule, run
`deploy/azure/deploy.sh` from a host connected to the VNet (VPN, jumpbox or a
deployment agent in the same network). The script writes Key Vault secrets and
the migrate job reaches PostgreSQL over the private endpoint. Do not re-add a
`0.0.0.0` firewall rule to make a laptop work; use the private path instead.
The same goes for `.github/workflows/azure-deploy.yml`: its `runner` input must
name a self-hosted runner on the VNet, because a GitHub-hosted runner cannot
reach Key Vault.

The first institutional administrator is created on first verified login by
`ADMIN_BOOTSTRAP_EMAILS` (requires a verified email claim) or
`ADMIN_BOOTSTRAP_SUBJECTS`. It is first-insert only; later role changes are made
in the Admin surface and are not overwritten by the environment list. The Admin
bundle is served at `/admin` and the debug-override API at `/api/debug/*`; both
require an `admin` database role. Set `ADMIN_PANEL_ENABLED=false` or
`DEBUG_PANEL_ENABLED=false` to disable the corresponding API; the Admin bundle
remains served but cannot authenticate.

## Deploy

From the repository root:

```bash
export RESOURCE_GROUP=rg-advisor-dev
export LOCATION=eastus
export POSTGRES_ADMIN_PASSWORD='...'
export AUTH_SERVER_KEY="$(openssl rand -hex 32)"
export PROFILE_ENCRYPTION_KEY="$(openssl rand -hex 32)"
export AUTH_MODE=oidc
export OIDC_ISSUER='https://...'
export OIDC_CLIENT_ID='...'
export OIDC_CLIENT_SECRET='...'
export LLM_PROVIDER=openai-compatible
export LLM_MODEL_ID='gpt-4.1-mini'       # or the Azure deployment name
export LLM_BASE_URL='https://...'        # Azure OpenAI endpoint / gateway
export LLM_API_KEY='...'

bash deploy/azure/deploy.sh
```

The script:

1. creates the resource group;
2. deploys the platform (`deployApps=false`);
3. builds `advisor-api` and `advisor-web` into ACR (`--target prod`);
4. writes the six Key Vault secrets;
5. deploys the Container Apps, the migrate job and the backup job;
6. starts the migrate job and waits up to 15 minutes;
7. waits up to 5 minutes for the web app's `/health`;
8. prints the web and API URLs.

If the school has not provided an OIDC issuer yet, set `AUTH_MODE=development`
only for a private smoke deployment. The image sets `AUTH_MODE` from the Bicep
parameter; `development` on public ingress is unsafe.

### Redeploying

Every run applies the whole deployment and rewrites every secret, so export the
same variables as the first deploy; a rerun with them unchanged only rolls out
new images. To deploy images built elsewhere:

```bash
export SKIP_BUILD=true
export API_IMAGE=myregistry.azurecr.io/advisor-api:tag
export WEB_IMAGE=myregistry.azurecr.io/advisor-web:tag
bash deploy/azure/deploy.sh
```

## Configuration the script maps

`deploy/azure/write-params.py` maps environment variables to Bicep
parameters. A variable left unset or empty is not sent, so the parameter's
default in `infra/main.bicep` applies; that file is the only home of the
defaults below. The important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT_NAME` | `dev` | Resource name suffix |
| `NAME_PREFIX` | `advisor` | Lowercase, 3–12 chars |
| `API_MIN_REPLICAS` / `API_MAX_REPLICAS` | `1` / `10` | HTTP concurrency rule at 50 |
| `WORKER_MIN_REPLICAS` / `WORKER_MAX_REPLICAS` | `2` / `14` | Scales on queued plus running jobs (see below) |
| `WORKER_CONCURRENCY` | `24` | Jobs per worker process; also the scaler's per-replica target |
| `WORKER_DRAIN_SECONDS` | `570` | SIGTERM drain before requeueing; grace is 30 s more (570 max, above the 540 s advice timeout) |
| `DB_POOL_MAX_SIZE` | `30` | Per process and role; pooler clients when PgBouncer is on |
| `POSTGRES_POOLER` | `true` | Built-in PgBouncer, transaction mode (ignored on Burstable) |
| `PGBOUNCER_POOL_SIZE` | `60` | Server connections PgBouncer holds for `advisor` |
| `PGBOUNCER_MAX_CLIENT_CONNECTIONS` | `5000` | Sum of every replica's pools must fit |
| `HTTP_THREADPOOL_TOKENS` | `80` | Sync dependency/handler pool |
| `ADMISSION_MAX_PENDING_WORKSPACE` | `4` | Outstanding turns per workspace |
| `ADMISSION_MAX_PENDING_GLOBAL` | `600` | Outstanding turns per instance; keep above max workers x concurrency |
| `POSTGRES_SKU_NAME` | `Standard_D2ds_v5` | Size for expected student load |
| `POSTGRES_TIER` | `GeneralPurpose` | Burstable for a smoke deploy |
| `POSTGRES_STORAGE_GB` | `128` | Auto-grow is enabled by the service |
| `POSTGRES_HA_MODE` | `Disabled` | Set `ZoneRedundant` for production |
| `DATA_SHARE_QUOTA_GB` | `100` | Azure Files share for `DATA_DIR` |
| `LLM_PROVIDER` | `openai-compatible` | OpenAI or an OpenAI-compatible gateway |
| `ADMIN_BOOTSTRAP_EMAILS` | empty | First-login admin promotion only |
| `ADMIN_BOOTSTRAP_SUBJECTS` | empty | First-login admin promotion only |
| `ADMIN_PANEL_ENABLED` | `true` | Admin bundle/API; role still required |
| `DEBUG_PANEL_ENABLED` | `true` | Debug bundle/API; role still required |
| `DEBUG_SESSION_TTL_SECONDS` | `28800` | Scoped override lifetime |
| `PUBLIC_BASE_URL` | empty | Empty derives `https://<web-fqdn>` |
| `ALERT_EMAIL` | empty | Alert receiver; empty still creates the rules |
| `BACKUP_SCHEDULE` | `0 2 * * *` | UTC cron for the backup job |
| `WORKSPACE_STORAGE_MAX_BYTES` | `104857600` | Per-workspace ceiling, and the storage alert's 90% line |
| `VNET_ADDRESS_PREFIX`, `APPS_SUBNET_PREFIX`, `PRIVATE_ENDPOINTS_SUBNET_PREFIX` | `10.42.0.0/16`, `/23`, `10.42.2.0/24` | When the school assigns the address space |

The apps receive `DATABASE_URL`, `DATABASE_DIRECT_URL`, `LLM_*`, `AUTH_*`,
`OIDC_*`, pooling and admission settings, and `DATA_DIR=/workspace/data`.
`DB_EXPECTED_REPLICAS` is derived (the larger of the two maximum replica
counts) and only matters without the pooler. `ADMIN_API_ENABLED` stays false,
so `/metrics` remains loopback-only. `modules/apps.bicep` lists every variable
in two tables, `appSettings` and `appSecretEnv`.

### Scaling

- **Connections.**
  - With `POSTGRES_POOLER=true`, `database-url` points at Flexible Server's
    built-in PgBouncer on port 6432 in transaction mode, and
    `database-direct-url` points at 5432.
  - The API and workers send request traffic through the pooler. Those
    connections skip server-side prepared statements and startup `options`.
  - The worker's `LISTEN` connection, readiness probes, the migrate and backup
    jobs, and the worker scaler use the direct URL.
  - `statement_timeout` (30 s) and `idle_in_transaction_session_timeout`
    (60 s) become server parameters, since the pooler can't carry them per
    connection. Direct sessions (scripts, migrations, backups) clear both for
    themselves.
  - The startup connection-budget check is skipped behind the pooler: there,
    `PGBOUNCER_POOL_SIZE` caps the server connections.
- **Workers.**
  - The worker app scales with KEDA's `postgresql` scaler on
    `count(*)` of running jobs plus queued jobs that are due. The target is
    `WORKER_CONCURRENCY` per replica, between `WORKER_MIN_REPLICAS` and
    `WORKER_MAX_REPLICAS`.
  - The scaler polls every 30 s and waits 300 s before scaling in (the
    Container Apps defaults).
  - A worker that receives SIGTERM stops claiming jobs, finishes in-flight
    answers for up to `WORKER_DRAIN_SECONDS`, then requeues what is left. The
    termination grace period is 30 s longer.
- **Verify on the first deploy:**
  1. The scaler can reach the private 5432 endpoint
     (`az containerapp logs show --type system`). The rule shows in
     `az containerapp show --query properties.template.scale`.
  2. `az postgres flexible-server parameter show --name pgbouncer.enabled` is
     `true`.
  3. `/ready` is green on both the API and the worker.

**Azure OpenAI note:** `openai-compatible` with `LLM_BASE_URL` works for OpenAI
and OpenAI-compatible gateways, including Azure OpenAI's v1-style endpoint if
the school exposes one. The classic Azure deployment URL
(`/openai/deployments/<name>/chat/completions?api-version=...`) needs an
`AzureOpenAI` adapter (not in this base) or a gateway such as LiteLLM/APIM.
Confirm which endpoint the school provides before setting `LLM_BASE_URL`.

## Images

- `deploy/docker/Dockerfile.backend` — Python 3.12 and CPU torch in a shared
  base with two targets. `prod`, the default, has no dev dependencies and
  preloads the Granite embedding model under `/opt/hf`; one image runs the API,
  worker, migrate and backup jobs. `dev` adds the tests and dev extras and runs
  a reloading API for `compose.yaml`. It defaults to `APP_UID=0` because the
  Azure Files SMB mount is root-owned; build with
  `--build-arg APP_UID=10001` after the Blob adapter replaces the local
  `DATA_DIR` path. The Granite weights are preloaded at build time; pass
  `--build-arg PRELOAD_EMBEDDING_MODEL=` if build egress to Hugging Face is
  blocked and let the first request download them.
- `deploy/docker/Dockerfile.web` — Node 20 build, unprivileged nginx on 8080.
  `API_UPSTREAM` is injected by Bicep as the API's HTTPS origin.
- `deploy/docker/nginx.conf.template` — SPA fallback, `/api` and `/health`
  proxying, 21 MiB body limit, long read timeout. A `map` sets Cache-Control
  per path (immutable assets, uncached HTML), so every response also carries
  the security headers.
- `.dockerignore` is an allowlist: `az acr build` uploads the whole context, and
  `uploads/`, `data/` and `docs/` would otherwise go up with every build.

Build locally if Docker is available:

```bash
docker build -f deploy/docker/Dockerfile.backend -t advisor-api:local .
docker build -f deploy/docker/Dockerfile.web -t advisor-web:local .
```

## Validation without Azure

`.github/workflows/deploy-checks.yml` runs on any change to these files. It
compiles the templates, runs `shellcheck` on `deploy.sh`, checks that every
parameter `write-params.py` sends exists in `main.bicep`, builds both images
(and the `dev` target), and checks the web image's caching and security
headers. Locally:

```bash
az bicep build --file infra/main.bicep --stdout >/dev/null
bash -n deploy/azure/deploy.sh
python -m py_compile deploy/azure/write-params.py
```

The templates use user-defined functions and one-line objects, which need Bicep
0.27 or later (`az bicep upgrade`). `bicep build` validates the resource schemas and parameter wiring. It does not
prove the deployment succeeds: quotas, Key Vault role propagation, PostgreSQL
extension availability and ACR build minutes are account-specific.

## Deliberately not in this base

- **Custom identity.** `AUTH_MODE=oidc` uses the existing OIDC adapter behind the
  `AuthProvider` seam. The school's internal sign-in still needs an
  introspection or trusted-gateway-header adapter; its shape is U7.
- **Real Azure Blob SDK adapter.** The storage seam, object keys and backfill
  exist and are wired, but `BlobStorageAdapter` is the in-process fake used by
  tests. Azure Files remains the interim `DATA_DIR` mount until a real SDK
  adapter replaces it. See §3 of the hosting requirements.
- **Office/sandbox host.** The sandbox worker is a separate hardened host, not a
  Container App (`sandbox/WORKER.md`).
- **Custom domain/TLS.** Container Apps provides a managed `*.azurecontainerapps.io`
  hostname. Front Door/custom domain is a follow-up and should own WAF.
- **Backup retention, offsite copies and monitoring.** The scheduled logical
  backup and restore drill exist; they write to the same storage account, so
  separate retention storage, offsite copies and failure alerting are deployment
  acceptance work.
- **Provider cache warm-up.** See `docs/plans/azure-hosting-requirements.md` and
  the cache discussion: the deployment does not assume a warm provider cache.

## Alerts

`modules/alerts.bicep` creates one action group and six rules. Set
`alertEmail` to receive mail; an empty value still creates the rules for the
Azure Monitor alert list.

| Alert | Signal and query | Threshold |
| --- | --- | --- |
| API 5xx rate | `AppRequests` 5xx share over 5 minutes | > 5% |
| Worker queue depth | `AppMetrics` `advisor_jobs` with `status="queued"` | > 50 |
| Oldest queued job | `AppMetrics` `advisor_job_oldest_queued_seconds` | > 60 s |
| Provider 429 rate | `AppMetrics` `advisor_provider_rate_limited_total`, fed from `model_calls` | > 10 per 5 min |
| PostgreSQL connections | `Microsoft.DBforPostgreSQL/flexibleServers` `active_connections` | > PgBouncer pool + worker and API max replicas + 10 (94 by default); 80 without the pooler |
| Workspace storage | `AppMetrics` `advisor_workspace_storage_bytes` | > 90% of `WORKSPACE_STORAGE_MAX_BYTES` |

The API already exposes the queue, oldest-job and pool gauges at `/metrics`;
the alert probe exposes provider failures and storage use. The deployment must
export those values to Application Insights as the custom metrics named above
for the `AppMetrics` rules to evaluate. The queries and thresholds live here so
the deployment owns the definitions; telemetry export and on-call routing are
operator steps.

## Backup and restore drill

The `backup` Container Apps job runs `python -m scripts.backup create` on the
UTC cron in `backupSchedule` (default `0 2 * * *`). It writes
`/workspace/data/backups/advisor-<UTC timestamp>.zip` on the mounted Azure Files
share. The archive holds the logical database dump plus every referenced raw
upload and artifact original, with checksums. It refuses while live jobs or
pending requests exist, so a scheduled run can fail during busy periods; that
refusal is reported in the job execution log. The share is the same storage
account as the live data, so copy archives to separate retention storage or a
private backup account for disaster recovery. Retention, offsite copies and
monitoring are deployment acceptance work.

Restore drill, using the same image and a fresh directory:

```bash
python -m scripts.restore_consolidated /workspace/data/backups/advisor-<stamp>.zip \
  --data-dir /workspace/restore-drill
```

The command builds a separate embedded database, applies the real migration
path, verifies row fingerprints and foreign keys, rebases every file reference,
and prints a report. A passing drill has `row_fingerprints_match: true`,
`foreign_keys_valid: true`, and a nonzero `referenced_files` count. Discard the
drill directory afterwards; never restore over a running installation and never
put an archive in git or a public release.

## Teardown

```bash
az group delete --name "$RESOURCE_GROUP" --yes
```

Key Vault has purge protection enabled, so a deleted vault cannot be recreated
with the same name until the retention window expires.
