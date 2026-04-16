"""
Pydantic Schemas
=================

Request and response models for the API. All validation happens here --
the engine operates on already-validated data.

These schemas define the contract between the dashboard and the API.
They are auto-documented in the Swagger UI at /docs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas (what the client sends)
# ---------------------------------------------------------------------------


class CreateContainerRequest(BaseModel):
    """Request body for POST /api/containers."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        description="Container name. Alphanumeric, hyphens, underscores, dots allowed.",
        examples=["web-server", "my_app", "test.01"],
    )
    command: list[str] = Field(
        default=["/bin/sh"],
        min_length=1,
        description="Command to run inside the container.",
        examples=[["/bin/sh"], ["/bin/sleep", "3600"]],
    )
    cpu_limit_percent: int = Field(
        default=50,
        ge=1,
        le=100,
        description="CPU limit as percentage of one core (1-100).",
    )
    memory_limit_mb: int = Field(
        default=64,
        ge=4,
        le=512,
        description="Memory limit in megabytes (4-512).",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set inside the container.",
    )
    hostname: str | None = Field(
        default=None,
        max_length=64,
        description="Custom hostname. Defaults to the container ID.",
    )
    image: str = Field(
        default="alpine",
        description="Base image. Currently only 'alpine' is supported.",
    )


class StopContainerRequest(BaseModel):
    """Optional request body for POST /api/containers/{id}/stop."""

    timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Seconds to wait for graceful shutdown before SIGKILL.",
    )


# ---------------------------------------------------------------------------
# Response schemas (what the API returns)
# ---------------------------------------------------------------------------


class NetworkInfo(BaseModel):
    """Network configuration of a container."""

    ip_address: str | None = None
    veth_host: str | None = None
    veth_container: str | None = None


class ContainerConfigResponse(BaseModel):
    """Container configuration as stored."""

    container_id: str
    name: str
    command: list[str]
    cpu_limit_percent: int
    memory_limit_mb: int
    env: dict[str, str]
    hostname: str | None
    image: str


class ContainerResponse(BaseModel):
    """Single container in API responses."""

    container_id: str
    name: str
    status: str
    image: str
    config: ContainerConfigResponse
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None
    network: NetworkInfo | None = None
    created_at: str
    started_at: str | None = None
    stopped_at: str | None = None


class ContainerListResponse(BaseModel):
    """Response for GET /api/containers."""

    containers: list[ContainerResponse]
    total: int


class MemoryMetrics(BaseModel):
    """Memory usage metrics."""

    usage_bytes: int
    limit_bytes: int
    usage_mb: float
    limit_mb: float
    usage_percent: float


class CpuMetrics(BaseModel):
    """CPU usage metrics."""

    usage_percent: float
    total_usec: int
    throttled_usec: int
    nr_throttled: int


class MetricsResponse(BaseModel):
    """Resource metrics for a container."""

    container_id: str
    timestamp: str
    memory: MemoryMetrics
    cpu: CpuMetrics
    oom_killed: bool


class SystemInfoResponse(BaseModel):
    """Response for GET /api/system/info."""

    engine_version: str
    hostname: str
    kernel_version: str
    total_memory_mb: float
    available_memory_mb: float
    cpu_count: int
    uptime_seconds: float
    total_containers: int
    running_containers: int
    stopped_containers: int
    max_containers: int


class HealthResponse(BaseModel):
    """Response for GET /api/health."""

    status: str = "ok"
    engine_initialized: bool
    database_connected: bool
    timestamp: str


class EventResponse(BaseModel):
    """A container lifecycle event."""

    container_id: str
    event_type: str
    message: str
    timestamp: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard error response body."""

    error: str
    code: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Suggestion schemas
# ---------------------------------------------------------------------------


class SuggestionCreate(BaseModel):
    """Request body for POST /api/suggestions.

    Includes a honeypot field (website) that must remain empty.
    Bots auto-fill it; humans never see it (hidden via CSS).
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Submitter's name.",
    )
    email: str | None = Field(
        default=None,
        max_length=254,
        description="Optional email for follow-up.",
    )
    category: str = Field(
        default="general",
        description="Suggestion category.",
    )
    message: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="The suggestion or feedback message.",
    )
    # Honeypot: must be empty. Bots fill this, humans don't see it.
    website: str = Field(
        default="",
        max_length=0,
        description="Honeypot field — must be empty.",
    )

    @classmethod
    def _sanitize(cls, v: str) -> str:
        """Strip HTML tags and excessive whitespace."""
        import re
        v = re.sub(r"<[^>]+>", "", v)  # strip HTML tags
        v = re.sub(r"\s+", " ", v).strip()  # collapse whitespace
        return v

    def model_post_init(self, __context: Any) -> None:
        """Sanitize text fields after validation."""
        object.__setattr__(self, "name", self._sanitize(self.name))
        object.__setattr__(self, "message", self._sanitize(self.message))
        if self.email:
            object.__setattr__(self, "email", self.email.strip().lower())


class SuggestionResponse(BaseModel):
    """Single suggestion in API responses."""

    id: str
    name: str
    email: str | None = None
    category: str
    message: str
    is_read: bool = False
    created_at: str


class SuggestionListResponse(BaseModel):
    """Response for GET /api/admin/suggestions."""

    suggestions: list[SuggestionResponse]
    total: int
    unread: int


class AdminLoginRequest(BaseModel):
    """Request body for POST /api/admin/login."""

    admin_key: str = Field(
        ...,
        min_length=1,
        description="The admin API key.",
    )

