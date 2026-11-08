"""Refuse to start when the configured pools would exhaust the server.

`DB_POOL_MAX_SIZE` is a per-process, per-role cap. The API process uses two
roles (default plus tool execution); a worker process uses three (default,
conversation execution, tool execution). Conversation workers therefore carry
twice the setting on their own, matching the deployment note in AGENTS.md.
"""

from __future__ import annotations

from app.config import Settings
from app.db import queries

# Superuser, migration and maintenance connections that must remain possible
# while every application pool is full.
CONNECTION_RESERVE = 10

API_POOL_ROLES = 2
WORKER_POOL_ROLES = 3


class ConnectionBudgetError(RuntimeError):
    """The configured pools cannot fit in the server's max_connections."""


def connection_budget(settings: Settings, *, roles: int) -> int:
    if settings.db_pool_max_size <= 0:
        return 0
    return settings.db_pool_max_size * roles * settings.db_expected_replicas


def check_connection_budget(conn, settings: Settings, *, roles: int) -> None:
    """Raise before startup when this deployment cannot fit its own pools."""
    required = connection_budget(settings, roles=roles)
    if required == 0:
        return
    row = conn.execute(queries.get("server_max_connections")).fetchone()
    max_connections = int(row["max_connections"])
    available = max_connections - CONNECTION_RESERVE
    if required > available:
        raise ConnectionBudgetError(
            "database connection budget does not fit: "
            f"DB_POOL_MAX_SIZE={settings.db_pool_max_size} x {roles} roles x "
            f"{settings.db_expected_replicas} replicas = {required} connections, "
            f"but max_connections={max_connections} leaves {available} after a "
            f"reserve of {CONNECTION_RESERVE}. Lower DB_POOL_MAX_SIZE or "
            "DB_EXPECTED_REPLICAS, or raise the server's max_connections."
        )
