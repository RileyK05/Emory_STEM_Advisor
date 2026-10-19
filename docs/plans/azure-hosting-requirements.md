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

It still uses public ingress, a PostgreSQL Azure-services firewall rule and
Azure Files for `DATA_DIR`. The gaps in this document (custom identity, private
networking, Blob adapter, SIGTERM drain, retention) remain.

## 1. Containers to build

| Container | Purpose | Port / probe | Notes |
| --- | --- | --- | --- |
| `api` | FastAPI + uvicorn | 8000, HTTP `/health`, `/ready` | No `--reload`; `--workers` or multiple replicas; non-root |
| `worker` | Job queue (`scripts.worker`) | small HTTP health port (to add) | Handles conversation turns, artifact runs; needs graceful SIGTERM drain |
| `migrate` | One-shot schema migration | none | Runs `scripts.migrate` / `prepare_database` as a release job, then exits |
| `web` | Built frontend + nginx | 80/443 | Optional if Front Door/Static Web Apps serves the build |
| `pgbouncer` | Connection pooler | 6432 | Only if the replica × pool budget exceeds Flexible Server limits |
| `backup` | Scheduled logical backup | none | Existing `scripts.backup`; runs as a job with object-storage output |

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

Artifact originals and raw uploads are currently written under `DATA_DIR` and
their **absolute local paths are stored in the database**
(`backend/app/artifacts/storage.py`, `backend/app/ingest/pipeline.py`). Two
replicas with different disks cannot read each other's files.

Two options:

1. **Interim — Azure Files mounted at the same path** on every replica. No code
   change; works for a small number of replicas; watch latency and POSIX
   semantics.
2. **Target — Blob Storage adapter.** Replace the local-path store with an
   object-key store (`workspace/hash` keys), keep a local cache only for
   processing, and backfill existing paths with a checksummed, resumable job.

Horizontal API scaling is blocked on one of these. The Blob adapter is a code
change and is not implemented yet.

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

Base to build now:

- an `AuthProvider` seam with `login_redirect`, `callback`, `resolve`, and
  `logout`, keeping the current `development` and `oidc` implementations;
- a generic **introspection adapter** (`INTROSPECTION_URL`,
  `INTROSPECTION_CREDENTIAL`, token source cookie/header, claim map, cache TTL,
  timeout) behind an explicit mode;
- a **trusted-header adapter** for gateway-terminated auth, refusing unsigned or
  wrong-issuer headers;
- a fake identity provider for tests and load tests (mint sessions for many
  workspaces without real credentials).

Do not implement a guessed protocol. The single most useful answer from the
school is: **does their system issue a JWT we can verify with JWKS, an opaque
token we introspect, or a signed gateway header?**

Mapping rule: one student identity → one `workspace_key`. The existing schema
already scopes conversations, uploads, artifacts, and gaps by that key.

## 5. Configuration and secrets

Every runtime setting is env-driven in `backend/app/config.py`. The Azure
deployment needs at least:

- `DATABASE_URL`, `DATA_DIR` (or the Blob settings), `DEFAULT_COLLECTION`
- `AUTH_MODE`, `AUTH_SERVER_KEY`, `OIDC_*` or the internal-auth settings
- `LLM_PROVIDER`, `LLM_MODEL_ID`, `LLM_API_KEY` / managed-identity option,
  `LLM_BASE_URL` (Azure OpenAI uses the `openai-compatible` adapter)
- `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL_ID`, `EMBEDDING_DEVICE`
- `DB_POOL_MAX_SIZE`, `DB_POOL_TIMEOUT_SECONDS`, `DB_STATEMENT_TIMEOUT_MS`,
  `DB_IDLE_TRANSACTION_TIMEOUT_MS`, `HTTP_THREADPOOL_TOKENS`
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
- Graceful termination: uvicorn drain plus a worker SIGTERM handler that stops
  claiming, finishes in-flight work within a grace period, and exits. The worker
  currently handles `KeyboardInterrupt` only; SIGTERM handling is a code change.
- Private networking for Postgres/Blob/Key Vault if the school requires it.

## 7. Observability

`/metrics` is loopback-only today. On Azure, either scrape it through a sidecar
on the same loopback, or expose it on an internal-only port behind the platform.
Alerts to define:

- queue depth and oldest queued age;
- pool waiting/timeouts by role;
- 5xx rate and 429 admission rate;
- provider errors, rate limits, and timeouts;
- DB connections vs the configured limit;
- replica CPU/memory and worker lease-renewal failures;
- storage/backup success.

## 8. CI/CD

- Build images in ACR (or GitHub Actions) with the existing Dockerfile.
- GitHub Actions can federate to Azure with OIDC, so no long-lived Azure
  credentials are stored in the repo.
- Deploy order: migrate job → api → worker → smoke check.
- Rollout must be additive-schema compatible; never edit an applied migration.
- Keep the existing CI checks (pytest, real DB, ruff, frontend typecheck/build,
  browser tests) as the gate.

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

**Can do now:** production image, Bicep/Terraform for the resources above,
Blob adapter with local/Azurite tests, the auth-provider seam plus a fake
identity provider, worker SIGTERM drain, batch/upload admission, connection
budget preflight, alert definitions.

**Blocked on answers:** which auth shape to implement first; whether to use
Azure Files or Blob; ingress/private networking; the model endpoint and its
authentication; and the actual subscription/resource group for deployment
verification.
