#!/usr/bin/env bash
# Deploy the advisor to Azure (infra/README.md has the walkthrough):
#   1. platform: registry, database, storage, vault, monitoring, environment
#   2. images built in the registry and Key Vault secrets written
#   3. Container Apps and jobs, then the migrate job and a health check
#
# Required: RESOURCE_GROUP, POSTGRES_ADMIN_PASSWORD, AUTH_SERVER_KEY,
#   PROFILE_ENCRYPTION_KEY, LLM_MODEL_ID, and with AUTH_MODE=oidc (the default)
#   OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET.
# Optional: LLM_API_KEY, IMAGE_TAG, SKIP_BUILD=true with API_IMAGE and
#   WEB_IMAGE, and any setting listed in deploy/azure/write-params.py. Settings
#   left unset take their defaults from infra/main.bicep.
#
# Key Vault and PostgreSQL accept only private traffic, so run this from a host
# on the deployment's VNet (VPN, jumpbox or a self-hosted runner).

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
export POSTGRES_ADMIN_PASSWORD LLM_MODEL_ID

# Used here as well as passed on: deployment names, the group's region and the
# sign-in checks below.
export ENVIRONMENT_NAME=${ENVIRONMENT_NAME:-dev}
export NAME_PREFIX=${NAME_PREFIX:-advisor}
export LOCATION=${LOCATION:-eastus}
export AUTH_MODE=${AUTH_MODE:-oidc}

case "$AUTH_MODE" in
  oidc)
    : "${OIDC_ISSUER:?Set OIDC_ISSUER for AUTH_MODE=oidc.}"
    : "${OIDC_CLIENT_ID:?Set OIDC_CLIENT_ID for AUTH_MODE=oidc.}"
    : "${OIDC_CLIENT_SECRET:?Set OIDC_CLIENT_SECRET for AUTH_MODE=oidc.}"
    ;;
  development)
    echo "WARNING: AUTH_MODE=development is only safe behind a trusted local proxy." >&2
    ;;
  *)
    echo "AUTH_MODE must be oidc or development." >&2
    exit 1
    ;;
esac

if [ "${LLM_PROVIDER:-}" != "hf-local" ] && [ -z "${LLM_API_KEY:-}" ]; then
  echo "WARNING: LLM_API_KEY is empty; cloud generation will fail until it is set." >&2
fi

az account show >/dev/null 2>&1 || { echo "Run az login first." >&2; exit 1; }

PLATFORM_DEPLOYMENT="${NAME_PREFIX}-${ENVIRONMENT_NAME}-platform"
APPS_DEPLOYMENT="${NAME_PREFIX}-${ENVIRONMENT_NAME}-apps"
PARAMS=$(mktemp)
trap 'rm -f "$PARAMS"' EXIT

deploy() {  # phase deployment-name
  "$PYTHON" "$ROOT/deploy/azure/write-params.py" "$1" "$PARAMS"
  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$2" \
    --template-file "$ROOT/infra/main.bicep" \
    --parameters "@${PARAMS}" \
    --output none
}

output() {  # deployment-name output-name
  az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$1" \
    --query "properties.outputs.$2.value" \
    --output tsv
}

echo "==> Resource group $RESOURCE_GROUP ($LOCATION)"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Platform resources"
deploy platform "$PLATFORM_DEPLOYMENT"

ACR=$(output "$PLATFORM_DEPLOYMENT" acrLoginServer)
KEY_VAULT=$(output "$PLATFORM_DEPLOYMENT" keyVaultName)
POSTGRES_FQDN=$(output "$PLATFORM_DEPLOYMENT" postgresFqdn)
POSTGRES_LOGIN=$(output "$PLATFORM_DEPLOYMENT" postgresAdminLogin)
POOLER_ON=$(output "$PLATFORM_DEPLOYMENT" postgresPoolerEnabled | tr '[:upper:]' '[:lower:]')
echo "    registry $ACR, vault $KEY_VAULT, database $POSTGRES_FQDN"

build_image() {  # repository dockerfile
  echo "==> Building ${ACR}/$1:${IMAGE_TAG}"
  (cd "$ROOT" && az acr build \
    --registry "${ACR%%.*}" \
    --image "$1:${IMAGE_TAG}" \
    --file "$2" \
    --target prod \
    .)
}

if [ "${SKIP_BUILD:-false}" = "true" ]; then
  : "${API_IMAGE:?Set API_IMAGE when SKIP_BUILD=true.}"
  : "${WEB_IMAGE:?Set WEB_IMAGE when SKIP_BUILD=true.}"
else
  IMAGE_TAG=${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}
  build_image advisor-api deploy/docker/Dockerfile.backend
  build_image advisor-web deploy/docker/Dockerfile.web
  API_IMAGE="${ACR}/advisor-api:${IMAGE_TAG}"
  WEB_IMAGE="${ACR}/advisor-web:${IMAGE_TAG}"
fi
export API_IMAGE WEB_IMAGE

database_url() {  # port
  "$PYTHON" -c '
import sys, urllib.parse
login, password, host, port = sys.argv[1:5]
print("postgresql://%s:%s@%s:%s/advisor?sslmode=require" % (
    urllib.parse.quote(login, safe=""),
    urllib.parse.quote(password, safe=""),
    host, port,
))' "$POSTGRES_LOGIN" "$POSTGRES_ADMIN_PASSWORD" "$POSTGRES_FQDN" "$1"
}

set_secret() {  # name value
  local attempt
  for attempt in $(seq 1 12); do
    if az keyvault secret set --vault-name "$KEY_VAULT" --name "$1" \
        --value "$2" --output none 2>/dev/null; then
      return 0
    fi
    if [ "$attempt" -eq 1 ]; then
      echo "    waiting for Key Vault role propagation..."
    fi
    sleep 10
  done
  echo "Could not write Key Vault secret '$1'. Check that this host is on the" >&2
  echo "VNet and that it has 'Key Vault Secrets Officer' on $KEY_VAULT." >&2
  return 1
}

echo "==> Key Vault secrets"
# The API and workers go through the built-in PgBouncer (6432) when it is on;
# LISTEN, migrations, backups, probes and the worker scaler use 5432.
DATABASE_DIRECT_URL=$(database_url 5432)
if [ "$POOLER_ON" = "true" ]; then
  DATABASE_URL=$(database_url 6432)
  echo "    PgBouncer on: app traffic on 6432, sessions on 5432"
else
  DATABASE_URL=$DATABASE_DIRECT_URL
fi
set_secret database-url "$DATABASE_URL"
set_secret database-direct-url "$DATABASE_DIRECT_URL"
set_secret llm-api-key "${LLM_API_KEY:-unused}"
set_secret auth-server-key "$AUTH_SERVER_KEY"
set_secret profile-encryption-key "$PROFILE_ENCRYPTION_KEY"
set_secret oidc-client-secret "${OIDC_CLIENT_SECRET:-unused}"

echo "==> Container Apps and jobs"
deploy apps "$APPS_DEPLOYMENT"

MIGRATE_JOB=$(output "$APPS_DEPLOYMENT" migrateJobName)
WEB_URL=$(output "$APPS_DEPLOYMENT" webUrl)

wait_for_migration() {  # execution-name; polls for up to 15 minutes
  local status unreadable=0
  for _ in $(seq 1 90); do
    status=$(az containerapp job execution show \
      --resource-group "$RESOURCE_GROUP" --name "$MIGRATE_JOB" \
      --job-execution-name "$1" \
      --query properties.status --output tsv 2>/dev/null || true)
    case "$status" in
      Succeeded)
        echo "    migration succeeded"
        return 0
        ;;
      Failed | Canceled)
        echo "Migration job finished with status $status." >&2
        echo "Inspect: az containerapp job execution list -g '$RESOURCE_GROUP' -n '$MIGRATE_JOB' -o table" >&2
        return 1
        ;;
      "")
        unreadable=$((unreadable + 1))
        if [ "$unreadable" -ge 6 ]; then
          echo "Could not read execution '$1'; check it manually." >&2
          return 0
        fi
        ;;
    esac
    sleep 10
  done
  echo "Migration still running after 15 minutes; check execution '$1'." >&2
}

echo "==> Migration job $MIGRATE_JOB"
EXECUTION=$(az containerapp job start \
  --resource-group "$RESOURCE_GROUP" --name "$MIGRATE_JOB" \
  --query name --output tsv)
if [ -n "$EXECUTION" ]; then
  wait_for_migration "$EXECUTION"
else
  echo "Migration job started; check its execution list." >&2
fi

if command -v curl >/dev/null 2>&1; then
  echo "==> Waiting for $WEB_URL/health"
  healthy=false
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 5 "$WEB_URL/health" >/dev/null 2>&1; then
      healthy=true
      echo "    health ok"
      break
    fi
    sleep 10
  done
  # Fail rather than warn: everything is already deployed, so exiting changes
  # nothing in Azure, but a red run is the only signal automation sees.
  if [ "$healthy" != "true" ]; then
    echo "The web app did not answer $WEB_URL/health within 5 minutes." >&2
    echo "Inspect: az containerapp logs show -g '$RESOURCE_GROUP' -n '$(output "$APPS_DEPLOYMENT" apiName)' --tail 50" >&2
    exit 1
  fi
fi

echo
echo "Deployed."
echo "  Web:     $WEB_URL"
echo "  API:     $(output "$APPS_DEPLOYMENT" apiUrl) (internal)"
echo "  Job:     $MIGRATE_JOB"
echo "  Secrets: $KEY_VAULT"
echo
echo "Next: set the OIDC redirect to $WEB_URL/api/auth/callback and confirm /ready."
