"""
System Routes
==============

GET /api/system/info -> Engine and host information
GET /api/health      -> Health check (no auth required)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.dependencies import get_engine, verify_api_key
from api.schemas import HealthResponse, SystemInfoResponse
from engine import __version__, collect_system_metrics
from engine.container import ContainerManager, ContainerStatus

router = APIRouter(tags=["system"])


@router.get(
    "/api/system/info",
    response_model=SystemInfoResponse,
    dependencies=[Depends(verify_api_key)],
)
async def system_info(
    engine: ContainerManager = Depends(get_engine),
):
    """Get engine and host system information."""
    metrics = collect_system_metrics()
    containers = engine.list_containers()

    running = sum(1 for c in containers if c.status == ContainerStatus.RUNNING)
    stopped = sum(1 for c in containers if c.status == ContainerStatus.STOPPED)

    return SystemInfoResponse(
        engine_version=__version__,
        hostname=metrics.hostname,
        kernel_version=metrics.kernel_version,
        total_memory_mb=round(metrics.total_memory_bytes / (1024 * 1024), 2),
        available_memory_mb=round(metrics.available_memory_bytes / (1024 * 1024), 2),
        cpu_count=metrics.cpu_count,
        uptime_seconds=round(metrics.uptime_seconds, 2),
        total_containers=len(containers),
        running_containers=running,
        stopped_containers=stopped,
        max_containers=engine.max_containers,
    )


@router.get(
    "/api/health",
    response_model=HealthResponse,
)
async def health_check():
    """Health check endpoint. No authentication required.

    Returns the status of the engine and database connection. Useful for
    load balancers, uptime monitors, and the dashboard connection indicator.
    """
    from api.dependencies import _engine
    from api.database import _client

    engine_ok = _engine is not None and _engine._initialized
    db_ok = False
    if _client:
        try:
            await _client.admin.command("ping")
            db_ok = True
        except Exception:
            pass

    return HealthResponse(
        status="ok" if (engine_ok and db_ok) else "degraded",
        engine_initialized=engine_ok,
        database_connected=db_ok,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
