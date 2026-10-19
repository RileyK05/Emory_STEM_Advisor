# Azure deployment scaffold

This is the deployable base for the advisor: production images, a two-phase
Bicep deployment, and a script that builds, pushes, migrates and prints the
public URL. It is intentionally a **base**, not the final school-hosted
topology. `docs/plans/azure-hosting-requirements.md` lists the decisions still
open (custom identity, private networking, retention).

## What is created

| Resource | Name pattern | Purpose |
| --- | --- | --- |
| Resource group | caller supplied | All resources |
| Container Registry | `<prefix><suffix>` | API/web images (no admin user) |
| Log Analytics + App Insights | `<prefix>-logs-*`, `<prefix>-appi-*` | Container logs and metrics |
| Storage account | `<prefix><suffix>` | `artifacts` blob container + `advisor-data` Azure Files share |
| PostgreSQL Flexible Server | `<prefix>-pg-*` | Database `advisor`, `vector` allowlisted, PITR |
| Key Vault | `<prefix>-kv-*` | `database-url`, `llm-api-key`, `auth-server-key`, `profile-encryption-key`, `oidc-client-secret` |
| User-assigned identity | `<prefix>-id-*` | ACR pull, Key Vault read, Blob data access |
| Container Apps environment | `<prefix>-env-*` | Shares Azure Files as `/workspace/data` |
| Container App `api` | `<prefix>-api-*` | FastAPI, external HTTPS, `/health` + `/ready` |
| Container App `web` | `<prefix>-web-*` | nginx + Vite build, proxies `/api` |
| Container App `worker` | `<prefix>-worker-*` | `scripts.worker --concurrency N` |
| Container Apps job | `<prefix>-migrate-*` | `scripts.migrate`, run by the deploy script |

Azure Files is the interim shared data path. It lets more than one replica read
the same raw/artifact files without a code change, but it is **not** the target
storage design. The Blob adapter in
`docs/plans/azure-hosting-requirements.md` §3 is required before horizontal API
scaling is treated as correct rather than convenient.

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

The first institutional administrator is created on first verified login by
`ADMIN_BOOTSTRAP_EMAILS` (requires a verified email claim) or
`ADMIN_BOOTSTRAP_SUBJECTS`. It is first-insert only; later role changes are made
in the Admin surface and are not overwritten by the environment list. The Admin
and Debug bundles are served at `/admin` and `/debug`; both still require an
`admin` database role. Set `ADMIN_PANEL_ENABLED=false` or
`DEBUG_PANEL_ENABLED=false` to disable the corresponding API; the static bundle
remains served but cannot authenticate or apply overrides.

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
3. builds `advisor-api` and `advisor-web` into ACR;
4. writes the five Key Vault secrets;
5. deploys the Container Apps and migrate job;
6. starts the migrate job and waits up to 15 minutes;
7. prints the web and API URLs.

If the school has not provided an OIDC issuer yet, set `AUTH_MODE=development`
only for a private smoke deployment. The image sets `AUTH_MODE` from the Bicep
parameter; `development` on public ingress is unsafe.

### Updating images only

```bash
export RESOURCE_GROUP=...
export POSTGRES_ADMIN_PASSWORD=...       # still used to render parameters
export LLM_MODEL_ID=...
export SKIP_BUILD=false
bash deploy/azure/deploy.sh
```

To deploy images built elsewhere:

```bash
export SKIP_BUILD=true
export API_IMAGE=myregistry.azurecr.io/advisor-api:tag
export WEB_IMAGE=myregistry.azurecr.io/advisor-web:tag
bash deploy/azure/deploy.sh
```

## Configuration the script maps

`deploy/azure/write-params.py` turns environment variables into Bicep
parameters. The important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT_NAME` | `dev` | Resource name suffix |
| `NAME_PREFIX` | `advisor` | Lowercase, 3–12 chars |
| `API_MIN_REPLICAS` / `API_MAX_REPLICAS` | `1` / `10` | HTTP concurrency rule at 50 |
| `WORKER_REPLICAS` | `1` | Keep at one until throughput is measured |
| `WORKER_CONCURRENCY` | `4` | Jobs per worker process |
| `DB_POOL_MAX_SIZE` | `20` | Per process and role |
| `HTTP_THREADPOOL_TOKENS` | `80` | Sync dependency/handler pool |
| `ADMISSION_MAX_PENDING_WORKSPACE` | `4` | Outstanding turns per workspace |
| `ADMISSION_MAX_PENDING_GLOBAL` | `200` | Outstanding turns per instance |
| `POSTGRES_SKU_NAME` | `Standard_D2ds_v5` | Size for expected student load |
| `POSTGRES_TIER` | `GeneralPurpose` | Burstable for a smoke deploy |
| `POSTGRES_STORAGE_GB` | `128` | Auto-grow is enabled by the service |
| `POSTGRES_HA_MODE` | `Disabled` | Set `ZoneRedundant` for production |
| `DATA_SHARE_QUOTA_GB` | `100` | Azure Files share for `DATA_DIR` |
| `LLM_PROVIDER` | `openai-compatible` | OpenAI or an OpenAI-compatible gateway |
| `LLM_CONTEXT_WINDOW_TOKENS` | `128000` | Drives auto-compaction at 50% |
| `CONVERSATION_COMPACTION_ENABLED` | `true` | Cloud default; hf-local defaults off |
| `CONVERSATION_COMPACTION_THRESHOLD` | `0.5` | Fraction of the context window |
| `CONVERSATION_COMPACTION_KEEP_TURNS` | `4` | Raw recent turns kept verbatim |
| `CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS` | `2048` | One handoff |
| `CONVERSATION_TOKEN_ESTIMATE_BYTES` | `4` | Context accounting heuristic |
| `ADMIN_BOOTSTRAP_EMAILS` | empty | First-login admin promotion only |
| `ADMIN_BOOTSTRAP_SUBJECTS` | empty | First-login admin promotion only |
| `ADMIN_PANEL_ENABLED` | `true` | Admin bundle/API; role still required |
| `DEBUG_PANEL_ENABLED` | `true` | Debug bundle/API; role still required |
| `DEBUG_SESSION_TTL_SECONDS` | `28800` | Scoped override lifetime |
| `PUBLIC_BASE_URL` | empty | Empty derives `https://<web-fqdn>` |

The apps receive `DATABASE_URL`, `LLM_*`, `AUTH_*`, `OIDC_*`, pooling and
admission settings, and `DATA_DIR=/workspace/data`. `ADMIN_API_ENABLED` stays
false, so `/metrics` remains loopback-only.

**Azure OpenAI note:** `openai-compatible` with `LLM_BASE_URL` works for OpenAI
and OpenAI-compatible gateways, including Azure OpenAI's v1-style endpoint if
the school exposes one. The classic Azure deployment URL
(`/openai/deployments/<name>/chat/completions?api-version=...`) needs an
`AzureOpenAI` adapter (not in this base) or a gateway such as LiteLLM/APIM.
Confirm which endpoint the school provides before setting `LLM_BASE_URL`.

## Images

- `deploy/docker/Dockerfile.backend` — Python 3.12, CPU torch, no dev
  dependencies, preloaded Granite embedding model under `/opt/hf`. One image
  runs the API, worker and migrate job. It defaults to `APP_UID=0` because the
  Azure Files SMB mount is root-owned; build with
  `--build-arg APP_UID=10001` after the Blob adapter replaces the local
  `DATA_DIR` path. The Granite weights are preloaded at build time; pass
  `--build-arg PRELOAD_EMBEDDING_MODEL=` if build egress to Hugging Face is
  blocked and let the first request download them.
- `deploy/docker/Dockerfile.web` — Node 20 build, unprivileged nginx on 8080.
  `API_UPSTREAM` is injected by Bicep as the API's HTTPS origin.
- `deploy/docker/nginx.conf.template` — SPA fallback, `/api` and `/health`
  proxying, 21 MiB body limit, long read timeout.

Build locally if Docker is available:

```bash
docker build -f deploy/docker/Dockerfile.backend -t advisor-api:local .
docker build -f deploy/docker/Dockerfile.web -t advisor-web:local .
```

## Validation without Azure

```bash
bicep build infra/main.bicep --outfile /tmp/main.json
bicep build infra/modules/apps.bicep --outfile /tmp/apps.json
bash -n deploy/azure/deploy.sh
python -m py_compile deploy/azure/write-params.py
```

`bicep build` validates the resource schemas and parameter wiring. It does not
prove the deployment succeeds: quotas, Key Vault role propagation, PostgreSQL
extension availability and ACR build minutes are account-specific.

## Deliberately not in this base

- **Private networking.** PostgreSQL currently allows Azure service traffic via
  a `0.0.0.0` firewall rule; Container Apps use public ingress. Add a VNet,
  delegated subnet, private DNS and private endpoints before student data.
- **Custom identity.** `AUTH_MODE=oidc` uses the existing OIDC adapter. The
  school's internal sign-in needs the `AuthProvider` seam and either an
  introspection or trusted-gateway-header adapter.
- **Blob storage adapter.** Azure Files is mounted at `DATA_DIR`; the database
  still stores local absolute paths. See §3 of the hosting requirements.
- **Worker SIGTERM drain.** The worker handles `KeyboardInterrupt`; Container
  Apps sends SIGTERM on revision replacement. Add a signal handler before
  relying on in-flight work surviving deploys.
- **Office/sandbox host.** The sandbox worker is a separate hardened host, not a
  Container App (`sandbox/WORKER.md`).
- **Custom domain/TLS.** Container Apps provides a managed `*.azurecontainerapps.io`
  hostname. Front Door/custom domain is a follow-up and should own WAF.
- **Backups.** PostgreSQL PITR is enabled; a logical backup job and restore
  drill are not part of this base.
- **Provider cache warm-up.** See `docs/plans/azure-hosting-requirements.md` and
  the cache discussion: the deployment does not assume a warm provider cache.

## Teardown

```bash
az group delete --name "$RESOURCE_GROUP" --yes
```

Key Vault has purge protection enabled, so a deleted vault cannot be recreated
with the same name until the retention window expires.
