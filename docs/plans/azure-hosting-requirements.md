# Azure hosting requirements: containers, resources, and unknowns

Status: requested handoff (2026-10-18). This is what must be built and decided
before the advisor can run on the school's Azure estate. It does not choose a
final topology; the containers are the same whether the school uses Azure
Container Apps, AKS, or App Service.

`docs/architecture.md` remains the design contract. `docs/plans/scale-6000.md`
holds the capacity gates. This file is the deployment handoff.

## Base scaffold (added)

The deployable base is now in the repository:

- `infra/` — Bicep for the registry, PostgreSQL + pgvector allowlist, storage,
  Key Vault, monitoring, managed identity, Container Apps environment, API,
  web, worker and migrate job. `infra/README.md` is the operator guide.
- `deploy/docker/` — production API/worker/migrate image and the nginx web
  image.
- `deploy/azure/deploy.sh` — two-phase deploy: platform, images/secrets, apps,
  migration.
- `.github/workflows/azure-deploy.yml` — optional manual OIDC deployment.

PostgreSQL, Blob/File storage and Key Vault are private through a VNet,
delegated subnet, private DNS and private endpoints; only the web container has
public ingress. The worker drains on SIGTERM and serves health/readiness. Azure
Files remains the interim `DATA_DIR`, and the storage seam stores object keys
while the real Azure Blob SDK adapter and custom identity shape stay open.

## 1. Containers to build

| Container | Purpose | Port / probe | Notes |
| --- | --- | --- | --- |
| `api` | FastAPI + uvicorn | 8000, HTTP `/health`, `/ready` | No `--reload`; `--workers` or multiple replicas; non-root |
| `worker` | Job queue (`scripts.worker`) | `WORKER_HEALTH_PORT` (`/health`, `/ready`) | Handles conversation turns and artifact runs; drains on SIGTERM without claiming new jobs |
| `migrate` | One-shot schema migration | none | Runs `scripts.migrate` / `prepare_database` as a release job, then exits |
| `web` | Built frontend + nginx | 80/443 | Optional if Front Door/Static Web Apps serves the build |
| `pgbouncer` | Connection pooler | 6432 | Only if the replica × pool budget exceeds Flexible Server limits |
| `backup` | Scheduled logical backup | none | Existing `scripts.backup`; runs as a scheduled Container Apps job writing to the mounted data share |

Not containerized: PostgreSQL (Flexible Server), Blob Storage, Key Vault,
Front Door/App Gateway, Log Analytics. The Office/sandbox runner is a separate
host or Azure Container Instances job; it already has its own image contract.

## 2. Azure resources to request

- **Azure Container Registry** for the images above.
- **Container Apps environment** (or AKS namespace) with:
  - `api` app with ingress enabled and a stable hostname;
  - `worker` app with ingress disabled, fixed or KEDA-scaled replicas;
  - `migrate`/`backup` as Container Apps jobs or Kubernetes Jobs.
- **Azure Database for PostgreSQL Flexible Server**:
  - `vector` extension enabled in `azure.extensions`;
  - private endpoint/VNet integration;
  - PITR and a tested restore;
  - a known `max_connections` value for the connection budget.
- **Storage account + Blob container** for artifact originals and raw uploads,
  with managed-identity access. **Azure Files** is the interim option (see §3).
- **Key Vault** for database URL, provider keys, session key, profile encryption
  key, OIDC client secret, and any internal-auth service credential.
- **Front Door or App Gateway** for TLS, WAF, and a custom hostname. If the
  school terminates auth there, it must pass a signed identity header.
- **Log Analytics + Application Insights** for logs, metrics, and alerts.
- **Managed identity** for `api`/`worker` to read Key Vault and Blob. No secrets
  baked into images.
- **VNet / private DNS** if Postgres, Blob, or Key Vault are private-only.
- **Egress rules** for the chosen model endpoint, the school identity service,
  and image pulls.

## 3. Storage: the one prerequisite for more than one replica

Artifact originals and raw uploads now go through `app/storage/` and are
addressed by backend-neutral object keys (`raw/...` and
`artifacts/originals/<workspace-hash>/<sha256>`). `storage_objects` records
each write, `scripts.backfill_storage` moves legacy absolute paths onto keys
with a checksum check, and old backups still restore because every reader
resolves either form. Local storage is the default and the only production
backend. `BlobStorageAdapter` is an in-process fake used by tests; a real Azure
Blob SDK adapter is still required before Azure Files can be removed.

For now, Azure Files is mounted at `DATA_DIR` on every replica as the interim
shared path. It works for a small number of replicas; watch latency and POSIX
semantics. Two replicas with different disks still cannot share `DATA_DIR`
until the Blob SDK adapter replaces the mount.

## 4. Authentication: build a seam, not a guess

The school uses a custom internal sign-in, not standard OIDC. There are three
plausible shapes, and the base should support all three behind one interface:

1. **OIDC/SAML endpoint** even if branded internally; implement it as the
   existing OIDC provider with issuer/claim configuration.
2. **Opaque token introspection** — the app receives a cookie or bearer token
   and calls the school's introspection endpoint per request (with a short
   cache).
3. **Gateway-authenticated request** — the school's gateway signs a header
   (student ID, email, affiliation); the app verifies the signature/issuer and
   maps it to a workspace key.

Base status:

- **Done:** an `AuthProvider` seam (`app/auth/provider.py`) behind
  `get_principal`, with the current `development` and `oidc` implementations in
  `AuthService`.
- **Done:** a fake identity provider (`app/auth/fake.py`) that mints sessions
  for many workspaces without real credentials, used by tests and load runs.
- **Not built:** a generic introspection adapter and a trusted-header adapter.
  Both depend on the school's answer below, and no guessing is allowed.

Do not implement a guessed protocol. The single most useful answer from the
school is: **does their system issue a JWT we can verify with JWKS, an opaque
token we introspect, or a signed gateway header?**

Mapping rule: one student identity → one `workspace_key`. The existing schema
already scopes conversations, uploads, artifacts, and gaps by that key.

## 5. Configuration and secrets

Every runtime setting is env-driven in `backend/app/config.py`. The Azure
deployment needs at least:

- `DATABASE_URL`, `DATA_DIR` (or the Blob settings), `DEFAULT_COLLECTION`,
  `STORAGE_BACKEND` (local is the production default)
- `AUTH_MODE`, `AUTH_SERVER_KEY`, `OIDC_*` or the internal-auth settings
- `LLM_PROVIDER`, `LLM_MODEL_ID`, `LLM_API_KEY` / managed-identity option,
  `LLM_BASE_URL` (Azure OpenAI uses the `openai-compatible` adapter)
- `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL_ID`, `EMBEDDING_DEVICE`
- `DB_POOL_MAX_SIZE`, `DB_EXPECTED_REPLICAS`, `DB_POOL_TIMEOUT_SECONDS`,
  `DB_STATEMENT_TIMEOUT_MS`, `DB_IDLE_TRANSACTION_TIMEOUT_MS`,
  `HTTP_THREADPOOL_TOKENS`
- `ADMISSION_MAX_PENDING_WORKSPACE`, `ADMISSION_MAX_PENDING_GLOBAL`
- `ADMIN_API_ENABLED` (loopback-only connection/metrics admin; the institutional
  Admin and Debug bundles are separate role-gated surfaces)
- `TOOLS_ENABLED`, `QUERY_PLANNING_ENABLED`, `ADVISING_RESEARCH_ENABLED`
- `PROFILE_ENCRYPTION_KEY` (stable; back it up separately)
- `SANDBOX_REMOTE_URL`, `SANDBOX_BEARER_TOKEN`, `OFFICE_ENABLED` if Office
  conversion is in scope

Secrets come from Key Vault through managed identity. `AUTH_SERVER_KEY` must be
stable across replicas or sessions cannot be verified. `PROFILE_ENCRYPTION_KEY`
must survive redeploys and restores.

## 6. Networking and ingress

- Public hostname with TLS; HTTP to HTTPS redirect.
- WAF rules and a request-size limit matching `REQUEST_MAX_BYTES` (21 MiB
  default) and `UPLOAD_MAX_BYTES` (20 MiB).
- Proxy timeouts longer than the longest polling/answer path; the client polls
  job events, so a 30–60 s proxy read timeout is sufficient unless SSE is added.
- Health probes: `/health` for liveness, `/ready` for readiness (DB + auth).
- Graceful termination: uvicorn drain plus the worker's SIGTERM/SIGINT/SIGBREAK
  handler, which stops claiming, finishes in-flight work within its lease, and
  exits. The worker also serves `/health` and `/ready` while accepting jobs.
- Private networking for PostgreSQL, Blob/File and Key Vault is in the base:
  a VNet, delegated Container Apps subnet, private DNS zones and private
  endpoints. Only the web container has public ingress.

## 7. Observability

`/metrics` is loopback-only today. On Azure, either scrape it through a sidecar
on the same loopback, or expose it on an internal-only port behind the platform.
`infra/modules/alerts.bicep` now defines rules (with their queries and
thresholds) for:

- API 5xx rate;
- worker queue depth and oldest queued age;
- provider 429 rate from `model_calls`;
- PostgreSQL connections;
- workspace storage near the per-student quota.

The deployment must export the existing `/metrics` and alert-probe values to
Application Insights as the custom metrics those rules read. Replica CPU/memory,
lease-renewal failures and backup success remain operator alerts.

## 8. CI/CD

- Build images in ACR (or GitHub Actions) with the existing Dockerfile.
- GitHub Actions can federate to Azure with OIDC, so no long-lived Azure
  credentials are stored in the repo.
- Deploy order: migrate job → api → worker → smoke check.
- Rollout must be additive-schema compatible; never edit an applied migration.
- Keep the existing CI checks (pytest with a pgvector Postgres service, ruff,
  frontend typecheck/build, format:check, check:callers and browser tests) as
  the gate; `.github/workflows/ci.yml` runs them on pull requests.

## 9. Questions for the hosting team

1. Which container platform: Container Apps, AKS, or App Service? Who owns it?
2. Subscription, resource group, region, naming/tags, and who can create
   resources.
3. Is ingress public or private? Who owns the custom domain and TLS certificate?
4. Does the platform provide a WAF/Front Door, or should the app sit behind one?
5. Is PostgreSQL Flexible Server with the `vector` extension available? What
   tier and `max_connections`?
6. Is Blob Storage available for uploads/artifacts, or must the app use Azure
   Files or a mounted volume?
7. Can the app use managed identity for Key Vault and Blob?
8. What egress is allowed to the model endpoint (OpenAI or Azure OpenAI) and to
   the school identity service?
9. What is the identity mechanism: OIDC/SAML, token introspection, or a signed
   gateway header? Which claims identify a student?
10. How are sessions revoked, and what is the acceptable revocation delay?
11. What are the backup RPO/RTO and retention requirements for student data?
12. Is the Office/sandbox runner allowed as a separate host or ACI job, and
    which registry/network can it use?
13. What are the vCPU/memory, public IP, and ACR quotas?
14. Does the school require private-only networking for data services?

## 10. What I can do now vs what is blocked

**Done in the code:** production image; Bicep for the platform and apps,
including private networking and private endpoints; the storage seam with
backend-neutral keys and a checksummed backfill; the auth-provider seam and fake
identity provider; worker SIGTERM drain and health port; batch/upload
admission; connection-budget preflight; alert definitions; the scheduled backup
job and restore drill; and pull-request CI.

**Still open:** a real Azure Blob SDK adapter (the current blob adapter is an
in-process fake, so Azure Files remains the interim shared path); the school's
auth shape (U7); the model endpoint and its authentication; and the actual
subscription/resource group for deployment verification. No deployment has been
made from this repository yet.
