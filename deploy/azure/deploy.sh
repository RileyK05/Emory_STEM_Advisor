#!/usr/bin/env bash
# Deploy the Azure scaffold in two phases:
#   1. platform resources (registry, database, storage, vault, monitoring, env)
#   2. images built into ACR, Key Vault secrets set, Container Apps + job
#
# Required environment:
#   RESOURCE_GROUP, POSTGRES_ADMIN_PASSWORD, AUTH_SERVER_KEY,
#   PROFILE_ENCRYPTION_KEY, LLM_MODEL_ID
#   and, when AUTH_MODE=oidc (the default): OIDC_ISSUER, OIDC_CLIENT_ID,
#   OIDC_CLIENT_SECRET.
#
# Optional: ENVIRONMENT_NAME, LOCATION, NAME_PREFIX, LLM_PROVIDER, LLM_BASE_URL,
#   LLM_API_KEY, EMBEDDING_MODEL_ID, PUBLIC_BASE_URL, IMAGE_TAG, SKIP_BUILD,
#   POSTGRES_SKU_NAME, POSTGRES_TIER, POSTGRES_STORAGE_GB,
#   POSTGRES_BACKUP_RETENTION_DAYS, DATA_SHARE_QUOTA_GB, API_MIN_REPLICAS,
#   API_MAX_REPLICAS, WORKER_REPLICAS, WORKER_CONCURRENCY, DB_POOL_MAX_SIZE,
#   HTTP_THREADPOOL_TOKENS, ADMISSION_MAX_PENDING_WORKSPACE,
#   ADMISSION_MAX_PENDING_GLOBAL.
#
# This base uses public ingress and a PostgreSQL rule for Azure services. See
# infra/README.md before loading student data.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON=${PYTHON:-python3}
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
command -v az >/dev/null 2>&1 || { echo "Azure CLI (az) is required." >&2; exit 1; }

: "${RESOURCE_GROUP:?Set RESOURCE_GROUP.}"
: "${POSTGRES_ADMIN_PASSWORD:?Set POSTGRES_ADMIN_PASSWORD.}"
: "${AUTH_SERVER_KEY:?Set AUTH_SERVER_KEY (32+ random bytes).}"
: "${PROFILE_ENCRYPTION_KEY:?Set PROFILE_ENCRYPTION_KEY (32+ random bytes).}"
: "${LLM_MODEL_ID:?Set LLM_MODEL_ID.}"

export ENVIRONMENT_NAME=${ENVIRONMENT_NAME:-dev}
export LOCATION=${LOCATION:-eastus}
export NAME_PREFIX=${NAME_PREFIX:-advisor}
export POSTGRES_ADMIN_LOGIN=${POSTGRES_ADMIN_LOGIN:-advisoradmin}
export POSTGRES_ADMIN_PASSWORD
export POSTGRES_SKU_NAME=${POSTGRES_SKU_NAME:-Standard_D2ds_v5}
export POSTGRES_TIER=${POSTGRES_TIER:-GeneralPurpose}
export POSTGRES_STORAGE_GB=${POSTGRES_STORAGE_GB:-128}
export POSTGRES_BACKUP_RETENTION_DAYS=${POSTGRES_BACKUP_RETENTION_DAYS:-14}
export POSTGRES_HA_MODE=${POSTGRES_HA_MODE:-Disabled}
export DATA_SHARE_QUOTA_GB=${DATA_SHARE_QUOTA_GB:-100}
export LLM_PROVIDER=${LLM_PROVIDER:-openai-compatible}
export LLM_MODEL_ID
export LLM_BASE_URL=${LLM_BASE_URL:-}
export LLM_CONTEXT_WINDOW_TOKENS=${LLM_CONTEXT_WINDOW_TOKENS:-128000}
export CONVERSATION_COMPACTION_ENABLED=${CONVERSATION_COMPACTION_ENABLED:-true}
export CONVERSATION_COMPACTION_THRESHOLD=${CONVERSATION_COMPACTION_THRESHOLD:-0.5}
export CONVERSATION_COMPACTION_KEEP_TURNS=${CONVERSATION_COMPACTION_KEEP_TURNS:-4}
export CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS=${CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS:-2048}
export CONVERSATION_TOKEN_ESTIMATE_BYTES=${CONVERSATION_TOKEN_ESTIMATE_BYTES:-4}
export EMBEDDING_MODEL_ID=${EMBEDDING_MODEL_ID:-ibm-granite/granite-embedding-125m-english}
export AUTH_MODE=${AUTH_MODE:-oidc}
export ADMIN_BOOTSTRAP_EMAILS=${ADMIN_BOOTSTRAP_EMAILS:-}
export ADMIN_BOOTSTRAP_SUBJECTS=${ADMIN_BOOTSTRAP_SUBJECTS:-}
export ADMIN_PANEL_ENABLED=${ADMIN_PANEL_ENABLED:-true}
export DEBUG_PANEL_ENABLED=${DEBUG_PANEL_ENABLED:-true}
export DEBUG_SESSION_TTL_SECONDS=${DEBUG_SESSION_TTL_SECONDS:-28800}
export OIDC_ISSUER=${OIDC_ISSUER:-}
export OIDC_CLIENT_ID=${OIDC_CLIENT_ID:-}
export PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}
export API_MIN_REPLICAS=${API_MIN_REPLICAS:-1}
export API_MAX_REPLICAS=${API_MAX_REPLICAS:-10}
export WORKER_REPLICAS=${WORKER_REPLICAS:-1}
export WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-4}
export DB_POOL_MAX_SIZE=${DB_POOL_MAX_SIZE:-20}
export HTTP_THREADPOOL_TOKENS=${HTTP_THREADPOOL_TOKENS:-80}
export ADMISSION_MAX_PENDING_WORKSPACE=${ADMISSION_MAX_PENDING_WORKSPACE:-4}
export ADMISSION_MAX_PENDING_GLOBAL=${ADMISSION_MAX_PENDING_GLOBAL:-200}

if [ "$AUTH_MODE" = "oidc" ]; then
  : "${OIDC_ISSUER:?Set OIDC_ISSUER for AUTH_MODE=oidc.}"
  : "${OIDC_CLIENT_ID:?Set OIDC_CLIENT_ID for AUTH_MODE=oidc.}"
  : "${OIDC_CLIENT_SECRET:?Set OIDC_CLIENT_SECRET for AUTH_MODE=oidc.}"
elif [ "$AUTH_MODE" != "development" ]; then
  echo "AUTH_MODE must be oidc or development." >&2
  exit 1
fi

if [ "$AUTH_MODE" = "development" ]; then
  echo "WARNING: AUTH_MODE=development is only safe behind a trusted local proxy." >&2
fi

if [ "$LLM_PROVIDER" != "hf-local" ] && [ -z "${LLM_API_KEY:-}" ]; then
  echo "WARNING: LLM_API_KEY is empty; cloud generation will fail until it is set." >&2
fi

az account show >/dev/null 2>&1 || { echo "Run az login first." >&2; exit 1; }

PLATFORM_DEPLOYMENT="${NAME_PREFIX}-${ENVIRONMENT_NAME}-platform"
APPS_DEPLOYMENT="${NAME_PREFIX}-${ENVIRONMENT_NAME}-apps"
PARAMS=$(mktemp)
trap 'rm -f "$PARAMS"' EXIT

echo "==> Resource group $RESOURCE_GROUP ($LOCATION)"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Platform resources"
"$PYTHON" "$ROOT/deploy/azure/write-params.py" platform "$PARAMS"
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$PLATFORM_DEPLOYMENT" \
  --template-file "$ROOT/infra/main.bicep" \
  --parameters "@${PARAMS}" \
  --output none

output() {
  az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$1" \
    --query "properties.outputs.$2.value" \
    --output tsv
}

ACR=$(output "$PLATFORM_DEPLOYMENT" acrLoginServer)
ACR_NAME=${ACR%%.*}
KEY_VAULT=$(output "$PLATFORM_DEPLOYMENT" keyVaultName)
POSTGRES_FQDN=$(output "$PLATFORM_DEPLOYMENT" postgresFqdn)

echo "==> Registry $ACR"
echo "==> Key Vault $KEY_VAULT"
echo "==> PostgreSQL $POSTGRES_FQDN"

IMAGE_TAG=${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}

if [ "${SKIP_BUILD:-false}" != "true" ]; then
  export API_IMAGE="${ACR}/advisor-api:${IMAGE_TAG}"
  export WEB_IMAGE="${ACR}/advisor-web:${IMAGE_TAG}"
  export WORKER_IMAGE="$API_IMAGE"
  echo "==> Building $API_IMAGE"
  (cd "$ROOT" && az acr build \
    --registry "$ACR_NAME" \
    --image "advisor-api:${IMAGE_TAG}" \
    --file deploy/docker/Dockerfile.backend \
    .)
  echo "==> Building $WEB_IMAGE"
  (cd "$ROOT" && az acr build \
    --registry "$ACR_NAME" \
    --image "advisor-web:${IMAGE_TAG}" \
    --file deploy/docker/Dockerfile.web \
    .)
else
  : "${API_IMAGE:?Set API_IMAGE when SKIP_BUILD=true.}"
  : "${WEB_IMAGE:?Set WEB_IMAGE when SKIP_BUILD=true.}"
  export WORKER_IMAGE="${WORKER_IMAGE:-$API_IMAGE}"
fi

echo "==> Key Vault secrets"
DATABASE_URL=$("$PYTHON" -c '
import sys, urllib.parse
login, password, host = sys.argv[1:4]
print("postgresql://%s:%s@%s:5432/advisor?sslmode=require" % (
    urllib.parse.quote(login, safe=""),
    urllib.parse.quote(password, safe=""),
    host,
))' "$POSTGRES_ADMIN_LOGIN" "$POSTGRES_ADMIN_PASSWORD" "$POSTGRES_FQDN")

set_secret() {
  local name=$1 value=$2
  for attempt in $(seq 1 12); do
    if az keyvault secret set --vault-name "$KEY_VAULT" --name "$name" \
        --value "$value" --output none 2>/dev/null; then
      return 0
    fi
    if [ "$attempt" -eq 1 ]; then
      echo "    waiting for Key Vault data-plane role propagation..."
    fi
    sleep 10
  done
  echo "Could not write Key Vault secret '$name'." >&2
  echo "Grant the deploying identity 'Key Vault Secrets Officer' on $KEY_VAULT." >&2
  return 1
}

set_secret database-url "$DATABASE_URL"
set_secret llm-api-key "${LLM_API_KEY:-unused}"
set_secret auth-server-key "$AUTH_SERVER_KEY"
set_secret profile-encryption-key "$PROFILE_ENCRYPTION_KEY"
set_secret oidc-client-secret "${OIDC_CLIENT_SECRET:-unused}"

echo "==> Container Apps and migrate job"
"$PYTHON" "$ROOT/deploy/azure/write-params.py" apps "$PARAMS"
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APPS_DEPLOYMENT" \
  --template-file "$ROOT/infra/main.bicep" \
  --parameters "@${PARAMS}" \
  --output none

MIGRATE_JOB=$(output "$APPS_DEPLOYMENT" migrateJobName)
echo "==> Starting migration job $MIGRATE_JOB"
EXECUTION=$(az containerapp job start \
  --resource-group "$RESOURCE_GROUP" --name "$MIGRATE_JOB" \
  --query name --output tsv)

if [ -n "$EXECUTION" ]; then
  missing=0
  for attempt in $(seq 1 90); do
    STATUS=$(az containerapp job execution show \
      --resource-group "$RESOURCE_GROUP" --name "$MIGRATE_JOB" \
      --job-execution-name "$EXECUTION" \
      --query properties.status --output tsv 2>/dev/null || true)
    if [ -z "$STATUS" ]; then
      missing=$((missing + 1))
      if [ "$missing" -ge 6 ]; then
        echo "Could not read execution '$EXECUTION'; check it manually." >&2
        break
      fi
    elif [ "$STATUS" = "Succeeded" ]; then
      echo "    migration succeeded"
      break
    elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Canceled" ]; then
      echo "Migration job finished with status $STATUS." >&2
      echo "Inspect: az containerapp job execution list -g '$RESOURCE_GROUP' -n '$MIGRATE_JOB' -o table" >&2
      exit 1
    fi
    sleep 10
  done
else
  echo "Migration job started; check its execution list." >&2
fi

WEB_URL=$(output "$APPS_DEPLOYMENT" webUrl)
if command -v curl >/dev/null 2>&1; then
  echo "==> Waiting for $WEB_URL/health"
  for attempt in $(seq 1 30); do
    if curl -fsS --max-time 5 "$WEB_URL/health" >/dev/null 2>&1; then
      echo "    health ok"
      break
    fi
    sleep 10
  done
fi

echo
echo "Deployed."
echo "  Web:     $WEB_URL"
echo "  API:     $(output "$APPS_DEPLOYMENT" apiUrl)"
echo "  Job:     $MIGRATE_JOB"
echo "  Secrets: $KEY_VAULT (database-url, llm-api-key, auth-server-key, profile-encryption-key, oidc-client-secret)"
echo
echo "Next: set the OIDC redirect to \$WEB_URL/api/auth/callback and confirm /ready."
