#!/usr/bin/env python3
"""Write an ARM parameters file for the two-phase advisor deployment.

Usage: write-params.py platform|apps OUTPUT

Each Bicep parameter in SETTINGS is read from its environment variable. An
unset or empty variable is left out, so the parameter's default in
infra/main.bicep applies: that file is the only place deployment defaults live.
The output holds secrets, so it is written with mode 0600; the caller removes it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path


def _bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("expected true or false")
    return value == "true"


def _url(raw: str) -> str:
    return raw.rstrip("/")


# Environment variable -> (Bicep parameter, conversion).
SETTINGS: dict[str, tuple[str, Callable[[str], object]]] = {
    "ENVIRONMENT_NAME": ("environmentName", str),
    "LOCATION": ("location", str),
    "NAME_PREFIX": ("namePrefix", str),
    "PUBLIC_BASE_URL": ("publicBaseUrl", _url),
    # Network ranges, for when the school assigns its own address space
    "VNET_ADDRESS_PREFIX": ("vnetAddressPrefix", str),
    "APPS_SUBNET_PREFIX": ("appsSubnetPrefix", str),
    "PRIVATE_ENDPOINTS_SUBNET_PREFIX": ("privateEndpointsSubnetPrefix", str),
    # Database
    "POSTGRES_ADMIN_LOGIN": ("postgresAdminLogin", str),
    "POSTGRES_ADMIN_PASSWORD": ("postgresAdminPassword", str),
    "POSTGRES_SKU_NAME": ("postgresSkuName", str),
    "POSTGRES_TIER": ("postgresTier", str),
    "POSTGRES_STORAGE_GB": ("postgresStorageGb", int),
    "POSTGRES_BACKUP_RETENTION_DAYS": ("postgresBackupRetentionDays", int),
    "POSTGRES_HA_MODE": ("postgresHighAvailability", str),
    "POSTGRES_POOLER": ("postgresPooler", _bool),
    "PGBOUNCER_POOL_SIZE": ("pgbouncerPoolSize", int),
    "PGBOUNCER_MAX_CLIENT_CONNECTIONS": ("pgbouncerMaxClientConnections", int),
    # Storage, backups and alerts
    "DATA_SHARE_QUOTA_GB": ("dataShareQuotaGb", int),
    "WORKSPACE_STORAGE_MAX_BYTES": ("workspaceStorageMaxBytes", int),
    "BACKUP_SCHEDULE": ("backupSchedule", str),
    "ALERT_EMAIL": ("alertEmail", str),
    # Models
    "LLM_PROVIDER": ("llmProvider", str),
    "LLM_MODEL_ID": ("llmModelId", str),
    "LLM_BASE_URL": ("llmBaseUrl", str),
    "EMBEDDING_MODEL_ID": ("embeddingModelId", str),
    # Sign-in and administration
    "AUTH_MODE": ("authMode", str),
    "OIDC_ISSUER": ("oidcIssuer", str),
    "OIDC_CLIENT_ID": ("oidcClientId", str),
    "ADMIN_BOOTSTRAP_EMAILS": ("adminBootstrapEmails", str),
    "ADMIN_BOOTSTRAP_SUBJECTS": ("adminBootstrapSubjects", str),
    "ADMIN_PANEL_ENABLED": ("adminPanelEnabled", _bool),
    "DEBUG_PANEL_ENABLED": ("debugPanelEnabled", _bool),
    "DEBUG_SESSION_TTL_SECONDS": ("debugSessionTtlSeconds", int),
    # Scaling
    "API_MIN_REPLICAS": ("apiMinReplicas", int),
    "API_MAX_REPLICAS": ("apiMaxReplicas", int),
    "WORKER_MIN_REPLICAS": ("workerMinReplicas", int),
    "WORKER_MAX_REPLICAS": ("workerMaxReplicas", int),
    "WORKER_CONCURRENCY": ("workerConcurrency", int),
    "WORKER_DRAIN_SECONDS": ("workerDrainSeconds", int),
    "DB_POOL_MAX_SIZE": ("dbPoolMaxSize", int),
    "HTTP_THREADPOOL_TOKENS": ("httpThreadpoolTokens", int),
    "ADMISSION_MAX_PENDING_WORKSPACE": ("admissionMaxPendingWorkspace", int),
    "ADMISSION_MAX_PENDING_GLOBAL": ("admissionMaxPendingGlobal", int),
    # Images (apps phase only); an empty WORKER_IMAGE reuses API_IMAGE.
    "API_IMAGE": ("apiImage", str),
    "WEB_IMAGE": ("webImage", str),
    "WORKER_IMAGE": ("workerImage", str),
}

IMAGES = {"API_IMAGE", "WEB_IMAGE", "WORKER_IMAGE"}
REQUIRED = {
    "platform": {"POSTGRES_ADMIN_PASSWORD", "LLM_MODEL_ID"},
    "apps": {"POSTGRES_ADMIN_PASSWORD", "LLM_MODEL_ID", "API_IMAGE", "WEB_IMAGE"},
}


def parameters(mode: str, environ: dict[str, str]) -> dict[str, object]:
    missing = sorted(name for name in REQUIRED[mode] if not environ.get(name))
    if missing:
        raise SystemExit(f"Set {', '.join(missing)}.")
    params: dict[str, object] = {"deployApps": mode == "apps"}
    for name, (parameter, convert) in SETTINGS.items():
        raw = environ.get(name, "")
        if raw == "" or (mode == "platform" and name in IMAGES):
            continue
        try:
            params[parameter] = convert(raw)
        except ValueError as exc:
            raise SystemExit(f"{name}={raw!r}: {exc}") from None
    return params


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in REQUIRED:
        raise SystemExit(__doc__)
    mode, destination = sys.argv[1], Path(sys.argv[2])
    document = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            name: {"value": value} for name, value in parameters(mode, dict(os.environ)).items()
        },
    }
    destination.write_text(json.dumps(document, indent=2), encoding="utf-8")
    destination.chmod(0o600)


if __name__ == "__main__":
    main()
