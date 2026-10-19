"""Loopback-only process metrics for monitoring (Prometheus text format).

The endpoint is disabled unless `ADMIN_API_ENABLED` is true and requires a
loopback client, matching the local administration boundary. Counters are
process-local and reset on restart; they are gauges/counters for the current
process, not cluster-wide totals.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from app.api import deps
from app.api.admin import require_local_admin
from app.db import queries
from app.db.connection import borrow_diagnostics, pool_stats

router = APIRouter()


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", include_in_schema=False)
def metrics(
    request: Request,
    _: Annotated[None, Depends(require_local_admin)],
    conn: Annotated[object, Depends(deps.get_conn)],
) -> PlainTextResponse:
    lines = [
        "# TYPE advisor_http_threadpool_tokens gauge",
        f"advisor_http_threadpool_tokens {request.app.state.settings.http_threadpool_tokens}",
        "# TYPE advisor_db_pool_connections gauge",
        "# TYPE advisor_db_pool_max_connections gauge",
        "# TYPE advisor_db_pool_available_connections gauge",
        "# TYPE advisor_db_pool_waiting_requests gauge",
    ]
    for item in pool_stats():
        pool = _label(str(item["pool"]))
        lines.extend((
            f'advisor_db_pool_connections{{pool="{pool}"}} {item["size"]}',
            f'advisor_db_pool_max_connections{{pool="{pool}"}} {item["maxSize"]}',
            f'advisor_db_pool_available_connections{{pool="{pool}"}} {item["available"]}',
            f'advisor_db_pool_waiting_requests{{pool="{pool}"}} {item["waiting"]}',
        ))

    counts = {
        row["status"]: int(row["jobs"])
        for row in conn.execute(queries.get("metrics_job_counts")).fetchall()
    }
    oldest = conn.execute(queries.get("metrics_oldest_queued_seconds")).fetchone()
    pending = conn.execute(queries.get("metrics_pending_conversation_requests")).fetchone()
    conn.commit()
    lines.extend((
        "# TYPE advisor_jobs gauge",
        f'advisor_jobs{{status="queued"}} {counts.get("queued", 0)}',
        f'advisor_jobs{{status="running"}} {counts.get("running", 0)}',
        "# TYPE advisor_job_oldest_queued_seconds gauge",
        f"advisor_job_oldest_queued_seconds {float(oldest['seconds']) if oldest else 0.0:.3f}",
        "# TYPE advisor_conversation_requests_pending gauge",
        f"advisor_conversation_requests_pending {int(pending['requests']) if pending else 0}",
    ))
    diagnostics = borrow_diagnostics()
    lines.extend((
        "# TYPE advisor_borrow_releases_total counter",
        f"advisor_borrow_releases_total {diagnostics['release']}",
        "# TYPE advisor_borrow_reacquires_total counter",
        f"advisor_borrow_reacquires_total {diagnostics['reacquire']}",
    ))
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
