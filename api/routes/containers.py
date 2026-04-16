"""
Container Routes
==================

CRUD endpoints for container lifecycle management.

POST /api/containers          -> Create a new container
GET  /api/containers          -> List all containers
GET  /api/containers/{id}     -> Inspect a container
POST /api/containers/{id}/start -> Start a container
POST /api/containers/{id}/stop  -> Stop a container
DELETE /api/containers/{id}   -> Remove a container
GET  /api/containers/{id}/logs -> Get container logs
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.database import (
    delete_container_doc,
    get_events,
    insert_event,
    save_container,
)
from api.dependencies import get_engine, verify_api_key
from api.schemas import (
    ContainerListResponse,
    ContainerResponse,
    CreateContainerRequest,
    ErrorResponse,
    EventResponse,
    StopContainerRequest,
)
from engine import (
    ContainerConfig,
    ContainerError,
    ContainerLimitReachedError,
    ContainerNotFoundError,
    PyCrateError,
)
from engine.container import ContainerManager

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/containers",
    tags=["containers"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "",
    response_model=ContainerResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse, "description": "Container limit reached"},
    },
)
async def create_container(
    request: CreateContainerRequest,
    engine: ContainerManager = Depends(get_engine),
):
    """Create a new container.

    The container is created in the CREATED state with resources allocated
    (rootfs extracted, cgroup configured). It is not automatically started.
    Use the /start endpoint to launch it.
    """
    try:
        config = ContainerConfig(
            name=request.name,
            command=request.command,
            cpu_limit_percent=request.cpu_limit_percent,
            memory_limit_mb=request.memory_limit_mb,
            env=request.env or {},
            hostname=request.hostname,
            image=request.image,
        )
        container = engine.create_container(config)

        # Persist to MongoDB
        await save_container(container.to_dict())
        await _emit_event(container.container_id, "container.created", "Container created")

        return ContainerResponse(**container.to_dict())

    except ContainerLimitReachedError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=e.message)
    except PyCrateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)


@router.get(
    "",
    response_model=ContainerListResponse,
)
async def list_containers(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status: created, running, stopped, error",
    ),
    engine: ContainerManager = Depends(get_engine),
):
    """List all containers, optionally filtered by status."""
    from engine.container import ContainerStatus

    filter_enum = None
    if status_filter:
        try:
            filter_enum = ContainerStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status filter: '{status_filter}'. "
                       f"Valid values: created, running, stopped, error",
            )

    containers = engine.list_containers(status_filter=filter_enum)
    container_dicts = [ContainerResponse(**c.to_dict()) for c in containers]

    return ContainerListResponse(
        containers=container_dicts,
        total=len(container_dicts),
    )


@router.get(
    "/{container_id}",
    response_model=ContainerResponse,
    responses={404: {"model": ErrorResponse}},
)
async def inspect_container(
    container_id: str,
    engine: ContainerManager = Depends(get_engine),
):
    """Get detailed information about a specific container."""
    try:
        container = engine.get_container(container_id)
        return ContainerResponse(**container.to_dict())
    except ContainerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)


@router.post(
    "/{container_id}/start",
    response_model=ContainerResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def start_container(
    container_id: str,
    engine: ContainerManager = Depends(get_engine),
):
    """Start a created or stopped container."""
    try:
        container = engine.start_container(container_id)

        # Update MongoDB
        await save_container(container.to_dict())
        await _emit_event(container_id, "container.started", "Container started")

        return ContainerResponse(**container.to_dict())

    except ContainerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    except PyCrateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.message)


@router.post(
    "/{container_id}/stop",
    response_model=ContainerResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def stop_container(
    container_id: str,
    request: StopContainerRequest | None = None,
    engine: ContainerManager = Depends(get_engine),
):
    """Stop a running container.

    Sends SIGTERM, waits for the timeout, then sends SIGKILL if needed.
    """
    timeout = request.timeout if request else 10

    try:
        container = engine.stop_container(container_id, timeout=timeout)

        # Update MongoDB
        await save_container(container.to_dict())
        await _emit_event(container_id, "container.stopped", "Container stopped")

        return ContainerResponse(**container.to_dict())

    except ContainerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    except PyCrateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.message)


@router.delete(
    "/{container_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def remove_container(
    container_id: str,
    engine: ContainerManager = Depends(get_engine),
):
    """Remove a container and all its resources.

    If the container is running, it will be stopped first.
    """
    try:
        engine.remove_container(container_id)

        # Remove from MongoDB
        await delete_container_doc(container_id)
        await _emit_event(container_id, "container.removed", "Container removed")

    except ContainerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)


@router.get(
    "/{container_id}/logs",
    responses={404: {"model": ErrorResponse}},
)
async def get_container_logs(
    container_id: str,
    tail: int | None = Query(None, ge=1, le=10000, description="Return last N lines"),
    engine: ContainerManager = Depends(get_engine),
):
    """Retrieve container logs.

    Returns captured stdout/stderr lines from the container process.
    Use the WebSocket endpoint /ws/logs/{id} for live streaming.
    """
    try:
        container = engine.get_container(container_id)
        logs = container.get_logs(tail=tail)
        return {"container_id": container_id, "logs": logs, "total_lines": len(logs)}
    except ContainerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)


@router.get(
    "/{container_id}/events",
    response_model=list[EventResponse],
    responses={404: {"model": ErrorResponse}},
)
async def get_container_events(
    container_id: str,
    limit: int = Query(50, ge=1, le=200, description="Maximum events to return"),
    engine: ContainerManager = Depends(get_engine),
):
    """Retrieve lifecycle events for a container."""
    # Verify container exists
    try:
        engine.get_container(container_id)
    except ContainerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)

    events = await get_events(container_id, limit=limit)
    return events


async def _emit_event(container_id: str, event_type: str, message: str) -> None:
    """Insert a lifecycle event into MongoDB.

    Non-critical -- failures are logged but don't break the operation.
    """
    try:
        await insert_event({
            "container_id": container_id,
            "event_type": event_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {},
        })
    except Exception as e:
        logger.warning("Failed to emit event %s for %s: %s", event_type, container_id, e)
