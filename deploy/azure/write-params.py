#!/usr/bin/env python3
"""Write an ARM parameters file for the two-phase advisor deployment.

Usage: write-params.py platform|apps OUTPUT

Reads deployment settings from environment variables. Secrets are written to a
temporary file with mode 0600; the caller removes it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)


def _params(mode: str) -> dict[str, object]:
    params: dict[str, object] = {
        "environmentName": os.environ.get("ENVIRONMENT_NAME", "dev"),
        "location": os.environ.get("LOCATION", "eastus"),
        "namePrefix": os.environ.get("NAME_PREFIX", "advisor"),
        "deployApps": mode == "apps",
        "postgresAdminLogin": os.environ.get("POSTGRES_ADMIN_LOGIN", "advisoradmin"),
        "postgresAdminPassword": os.environ["POSTGRES_ADMIN_PASSWORD"],
        "postgresSkuName": os.environ.get("POSTGRES_SKU_NAME", "Standard_D2ds_v5"),
        "postgresTier": os.environ.get("POSTGRES_TIER", "GeneralPurpose"),
        "postgresStorageGb": _int("POSTGRES_STORAGE_GB", 128),
        "postgresBackupRetentionDays": _int("POSTGRES_BACKUP_RETENTION_DAYS", 14),
        "postgresHighAvailability": os.environ.get("POSTGRES_HA_MODE", "Disabled"),
        "dataShareQuotaGb": _int("DATA_SHARE_QUOTA_GB", 100),
        "llmProvider": os.environ.get("LLM_PROVIDER", "openai-compatible"),
        "llmModelId": os.environ["LLM_MODEL_ID"],
        "llmBaseUrl": os.environ.get("LLM_BASE_URL", ""),
        "llmContextWindowTokens": _int("LLM_CONTEXT_WINDOW_TOKENS", 128_000),
        "conversationCompactionEnabled": (
            os.environ.get("CONVERSATION_COMPACTION_ENABLED", "true").lower() == "true"),
        "conversationCompactionThreshold": os.environ.get(
            "CONVERSATION_COMPACTION_THRESHOLD", "0.5"),
        "conversationCompactionKeepTurns": _int("CONVERSATION_COMPACTION_KEEP_TURNS", 4),
        "conversationCompactionMaxOutputTokens": _int(
            "CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS", 2048),
        "conversationTokenEstimateBytes": _int("CONVERSATION_TOKEN_ESTIMATE_BYTES", 4),
        "embeddingModelId": os.environ.get(
            "EMBEDDING_MODEL_ID", "ibm-granite/granite-embedding-125m-english"),
        "authMode": os.environ.get("AUTH_MODE", "oidc"),
        "adminBootstrapEmails": os.environ.get("ADMIN_BOOTSTRAP_EMAILS", ""),
        "adminBootstrapSubjects": os.environ.get("ADMIN_BOOTSTRAP_SUBJECTS", ""),
        "adminPanelEnabled": (
            os.environ.get("ADMIN_PANEL_ENABLED", "true").lower() == "true"),
        "debugPanelEnabled": (
            os.environ.get("DEBUG_PANEL_ENABLED", "true").lower() == "true"),
        "debugSessionTtlSeconds": _int("DEBUG_SESSION_TTL_SECONDS", 28800),
        "oidcIssuer": os.environ.get("OIDC_ISSUER", ""),
        "oidcClientId": os.environ.get("OIDC_CLIENT_ID", ""),
        "publicBaseUrl": os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        "apiMinReplicas": _int("API_MIN_REPLICAS", 1),
        "apiMaxReplicas": _int("API_MAX_REPLICAS", 10),
        "workerReplicas": _int("WORKER_REPLICAS", 1),
        "workerConcurrency": _int("WORKER_CONCURRENCY", 4),
        "dbPoolMaxSize": _int("DB_POOL_MAX_SIZE", 20),
        "httpThreadpoolTokens": _int("HTTP_THREADPOOL_TOKENS", 80),
        "admissionMaxPendingWorkspace": _int("ADMISSION_MAX_PENDING_WORKSPACE", 4),
        "admissionMaxPendingGlobal": _int("ADMISSION_MAX_PENDING_GLOBAL", 200),
    }
    if mode == "apps":
        params.update({
            "apiImage": os.environ["API_IMAGE"],
            "webImage": os.environ["WEB_IMAGE"],
            "workerImage": os.environ.get("WORKER_IMAGE") or os.environ["API_IMAGE"],
        })
    return params


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in {"platform", "apps"}:
        raise SystemExit(__doc__)
    mode, destination = sys.argv[1], Path(sys.argv[2])
    document = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in _params(mode).items()},
    }
    destination.write_text(json.dumps(document, indent=2), encoding="utf-8")
    destination.chmod(0o600)


if __name__ == "__main__":
    main()
