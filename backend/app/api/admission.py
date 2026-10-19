"""Opt-in admission caps for outstanding work.

The check runs inside the transaction that inserts the job and takes a
transaction-scoped advisory lock, so concurrent callers on any replica observe a
consistent count. A rejected request does no expensive work.
"""

from dataclasses import dataclass

from fastapi import HTTPException

from app.db import queries

RETRY_AFTER_SECONDS = "5"


@dataclass(frozen=True)
class AdmissionLimits:
    workspace: int = 0
    global_: int = 0

    @property
    def enabled(self) -> bool:
        return self.workspace > 0 or self.global_ > 0

    def check(self, conn, workspace_key: str) -> None:
        """Raise 429 when the workspace or instance already has the maximum pending."""
        if not self.enabled:
            return
        conn.execute(queries.get("lock_admission"))
        row = conn.execute(queries.get("count_admission_pending"), {
            "workspace_key": workspace_key,
        }).fetchone()
        if self.workspace > 0 and row["workspace_pending"] >= self.workspace:
            raise HTTPException(
                status_code=429,
                detail="too many outstanding turns for this workspace; retry shortly",
                headers={"Retry-After": RETRY_AFTER_SECONDS},
            )
        if self.global_ > 0 and row["global_pending"] >= self.global_:
            raise HTTPException(
                status_code=429,
                detail="the advising queue is full; retry shortly",
                headers={"Retry-After": RETRY_AFTER_SECONDS},
            )
