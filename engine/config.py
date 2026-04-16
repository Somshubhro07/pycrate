"""
Container Configuration
========================

Immutable configuration for a container instance. Created once when a container
is defined, never mutated after. The engine reads this to know what namespaces
to create, what cgroup limits to apply, and what command to run inside the container.

Design note: This is intentionally a plain dataclass, not a Pydantic model.
Pydantic validation lives in the API layer (schemas.py). The engine operates
on validated data — it doesn't re-validate.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from typing import ClassVar


def _generate_container_id() -> str:
    """Generate a human-readable short ID like 'crate-a7f3b2'.

    Uses 6 hex characters — 16M possible IDs. Collision probability
    is negligible for a single-host runtime capped at ~4 containers.
    """
    suffix = "".join(secrets.choice(string.hexdigits[:16]) for _ in range(6))
    return f"crate-{suffix}"


@dataclass(frozen=True)
class ContainerConfig:
    """Immutable configuration for a single container.

    Attributes:
        name: Human-readable container name (e.g., "web-server").
        command: Entry command to execute inside the container.
        cpu_limit_percent: CPU limit as a percentage of one core (1-100).
            Translated to cpu.max format: (percent * 1000) / 100000 microseconds.
        memory_limit_mb: Memory cap in megabytes. Exceeding triggers OOM kill.
        env: Environment variables injected into the container process.
        hostname: UTS hostname visible inside the container. Defaults to container_id.
        image: Base image name. Currently only "alpine" is supported.
        container_id: Unique identifier. Auto-generated if not provided.
    """

    name: str
    command: list[str] = field(default_factory=lambda: ["/bin/sh"])
    cpu_limit_percent: int = 50
    memory_limit_mb: int = 64
    env: dict[str, str] = field(default_factory=lambda: {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
    hostname: str | None = None
    image: str = "alpine:3.20"
    security_enabled: bool = True
    container_id: str = field(default_factory=_generate_container_id)

    # ── Constants ──────────────────────────────────────────────

    CGROUP_CPU_PERIOD: ClassVar[int] = 100_000  # microseconds (100ms standard period)

    def __post_init__(self) -> None:
        """Validate constraints that can't be expressed in type hints."""
        if not self.name:
            raise ValueError("Container name cannot be empty")

        if not 1 <= self.cpu_limit_percent <= 100:
            raise ValueError(
                f"cpu_limit_percent must be 1-100, got {self.cpu_limit_percent}"
            )

        if self.memory_limit_mb < 4:
            raise ValueError(
                f"memory_limit_mb must be at least 4MB, got {self.memory_limit_mb}"
            )

        if not self.command:
            raise ValueError("command cannot be empty")

        # Default hostname to container_id if not explicitly set
        if self.hostname is None:
            # Workaround for frozen dataclass — use object.__setattr__
            object.__setattr__(self, "hostname", self.container_id)

    @property
    def cpu_quota_us(self) -> int:
        """CPU quota in microseconds for cgroup cpu.max.

        cpu.max format is: $QUOTA $PERIOD
        50% of one core = 50000 / 100000
        """
        return (self.cpu_limit_percent * self.CGROUP_CPU_PERIOD) // 100

    @property
    def memory_limit_bytes(self) -> int:
        """Memory limit in bytes for cgroup memory.max."""
        return self.memory_limit_mb * 1024 * 1024

    def to_dict(self) -> dict:
        """Serialize to a dictionary for MongoDB storage."""
        return {
            "container_id": self.container_id,
            "name": self.name,
            "command": self.command,
            "cpu_limit_percent": self.cpu_limit_percent,
            "memory_limit_mb": self.memory_limit_mb,
            "env": self.env,
            "hostname": self.hostname,
            "image": self.image,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContainerConfig:
        """Reconstruct from a MongoDB document."""
        return cls(
            container_id=data["container_id"],
            name=data["name"],
            command=data.get("command", ["/bin/sh"]),
            cpu_limit_percent=data.get("cpu_limit_percent", 50),
            memory_limit_mb=data.get("memory_limit_mb", 64),
            env=data.get("env", {}),
            hostname=data.get("hostname"),
            image=data.get("image", "alpine"),
        )
