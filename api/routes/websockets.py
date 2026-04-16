"""
WebSocket Routes
==================

Real-time streaming endpoints for the dashboard.

WS /ws/logs/{id}  -> Stream live container logs
WS /ws/metrics    -> Stream live resource metrics for all running containers

Both endpoints validate the API key from the query string (WebSocket
connections can't set custom headers in browser APIs, so we pass
the key as ?api_key=...).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.config import get_settings
from api.dependencies import get_engine

logger = logging.getLogger(__name__)

router = APIRouter()


async def _verify_ws_api_key(websocket: WebSocket, api_key: str | None) -> bool:
    """Validate API key for WebSocket connections.

    WebSocket connections can't use custom HTTP headers from browser JS,
    so the API key is passed as a query parameter.

    Returns:
        True if the key is valid, False otherwise.
    """
    import hmac
    settings = get_settings()
    if not api_key or not hmac.compare_digest(api_key, settings.api_key):
        await websocket.close(code=4001, reason="Invalid API key")
        return False
    return True


@router.websocket("/ws/logs/{container_id}")
async def stream_logs(
    websocket: WebSocket,
    container_id: str,
    api_key: str | None = Query(None),
):
    """Stream live logs from a container.

    Sends log lines as JSON messages:
        {"type": "log", "container_id": "...", "line": "...", "timestamp": "..."}

    The client receives new log lines as they appear. On disconnect,
    the stream is cleaned up automatically.
    """
    await websocket.accept()

    if not await _verify_ws_api_key(websocket, api_key):
        return

    engine = get_engine()

    try:
        container = engine.get_container(container_id)
    except Exception:
        await websocket.send_json({
            "type": "error",
            "message": f"Container '{container_id}' not found",
        })
        await websocket.close()
        return

    logger.info("WebSocket log stream opened for %s", container_id)

    # Track which lines we've already sent
    sent_count = 0

    try:
        while True:
            # Get any new log lines since last check
            all_logs = container.get_logs()
            new_logs = all_logs[sent_count:]

            for line in new_logs:
                await websocket.send_json({
                    "type": "log",
                    "container_id": container_id,
                    "line": line,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                sent_count += 1

            # Check if container is still running
            if not container.is_running and sent_count >= len(all_logs):
                await websocket.send_json({
                    "type": "status",
                    "container_id": container_id,
                    "status": container.status.value,
                    "message": "Container is no longer running",
                })
                break

            # Poll interval: 500ms for responsive log streaming
            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info("WebSocket log stream closed for %s", container_id)
    except Exception as e:
        logger.error("WebSocket log stream error for %s: %s", container_id, e)
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception:
            pass


@router.websocket("/ws/metrics")
async def stream_metrics(
    websocket: WebSocket,
    api_key: str | None = Query(None),
):
    """Stream live resource metrics for all running containers.

    Sends metrics snapshots as JSON messages every 2 seconds:
        {
            "type": "metrics",
            "timestamp": "...",
            "containers": [
                {
                    "container_id": "...",
                    "cpu": {"usage_percent": 12.5, ...},
                    "memory": {"usage_bytes": ..., ...},
                    ...
                }
            ]
        }

    The dashboard subscribes to this once and receives data for all
    containers, updating charts and gauges in real time.
    """
    await websocket.accept()

    if not await _verify_ws_api_key(websocket, api_key):
        return

    engine = get_engine()
    logger.info("WebSocket metrics stream opened")

    try:
        while True:
            # Collect metrics from all running containers
            snapshots = engine.collect_all_metrics()

            message = {
                "type": "metrics",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "containers": [s.to_dict() for s in snapshots],
            }

            await websocket.send_json(message)

            # Refresh interval: 2 seconds (balances responsiveness vs CPU cost)
            await asyncio.sleep(2)

    except WebSocketDisconnect:
        logger.info("WebSocket metrics stream closed")
    except Exception as e:
        logger.error("WebSocket metrics stream error: %s", e)
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception:
            pass
