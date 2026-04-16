"""
Resource Metrics Collection
============================

Reads container resource usage from cgroup v2 filesystem and host /proc.
Produces MetricsSnapshot objects that the API layer streams to the dashboard
via WebSocket.

Data sources:
    CPU usage   <- /sys/fs/cgroup/pycrate/{id}/cpu.stat (usage_usec)
    Memory      <- /sys/fs/cgroup/pycrate/{id}/memory.current
    Memory max  <- /sys/fs/cgroup/pycrate/{id}/memory.max
    Host stats  <- /proc/stat, /proc/meminfo, psutil (optional)

CPU percentage is calculated by comparing two cpu.stat snapshots taken
at a known time interval apart.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from engine.cgroups import CgroupController

logger = logging.getLogger(__name__)


@dataclass
class MetricsSnapshot:
    """Point-in-time resource usage for a single container.

    This is what gets serialized to JSON and sent over the WebSocket
    to the dashboard.
    """

    container_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Memory
    memory_usage_bytes: int = 0
    memory_limit_bytes: int = 0

    # CPU
    cpu_usage_percent: float = 0.0
    cpu_total_usec: int = 0
    cpu_throttled_usec: int = 0
    cpu_nr_throttled: int = 0

    # OOM
    oom_killed: bool = False

    @property
    def memory_usage_mb(self) -> float:
        """Memory usage in megabytes for display purposes."""
        return self.memory_usage_bytes / (1024 * 1024)

    @property
    def memory_limit_mb(self) -> float:
        """Memory limit in megabytes for display purposes."""
        return self.memory_limit_bytes / (1024 * 1024)

    @property
    def memory_usage_percent(self) -> float:
        """Memory usage as a percentage of the limit."""
        if self.memory_limit_bytes <= 0:
            return 0.0
        return (self.memory_usage_bytes / self.memory_limit_bytes) * 100

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON/WebSocket transmission."""
        return {
            "container_id": self.container_id,
            "timestamp": self.timestamp,
            "memory": {
                "usage_bytes": self.memory_usage_bytes,
                "limit_bytes": self.memory_limit_bytes,
                "usage_mb": round(self.memory_usage_mb, 2),
                "limit_mb": round(self.memory_limit_mb, 2),
                "usage_percent": round(self.memory_usage_percent, 2),
            },
            "cpu": {
                "usage_percent": round(self.cpu_usage_percent, 2),
                "total_usec": self.cpu_total_usec,
                "throttled_usec": self.cpu_throttled_usec,
                "nr_throttled": self.cpu_nr_throttled,
            },
            "oom_killed": self.oom_killed,
        }


class MetricsCollector:
    """Collects resource metrics for a container by reading cgroup files.

    Maintains the previous CPU usage snapshot to compute CPU percentage
    between two readings.

    Usage:
        collector = MetricsCollector("crate-a7f3b2", cgroup_controller)
        snapshot = collector.collect()
        next_snapshot = collector.collect()  # CPU% is now meaningful
    """

    def __init__(self, container_id: str, cgroup: CgroupController) -> None:
        self.container_id = container_id
        self.cgroup = cgroup

        # Previous CPU reading for delta calculation
        self._prev_cpu_usec: int = 0
        self._prev_timestamp: float = 0.0

    def collect(self) -> MetricsSnapshot:
        """Collect a point-in-time snapshot of resource usage.

        CPU percentage is calculated as:
            cpu% = (delta_cpu_usec / delta_wall_time_usec) * 100

        The first call will always show 0% CPU because there's no previous
        reading to compare against.

        Returns:
            MetricsSnapshot with current resource usage.
        """
        now = time.monotonic()

        # Read memory usage
        memory_current = self.cgroup.read_memory_usage()
        memory_limit = self.cgroup.read_memory_limit()

        # Read CPU statistics
        cpu_stats = self.cgroup.read_cpu_usage()
        cpu_total_usec = cpu_stats.get("usage_usec", 0)
        cpu_throttled_usec = cpu_stats.get("throttled_usec", 0)
        cpu_nr_throttled = cpu_stats.get("nr_throttled", 0)

        # Calculate CPU percentage since last reading
        cpu_percent = 0.0
        if self._prev_timestamp > 0:
            delta_cpu_usec = cpu_total_usec - self._prev_cpu_usec
            delta_wall_usec = (now - self._prev_timestamp) * 1_000_000  # seconds to microseconds

            if delta_wall_usec > 0:
                cpu_percent = (delta_cpu_usec / delta_wall_usec) * 100
                # Clamp to 0-100 range (can exceed 100% on multi-core briefly)
                cpu_percent = max(0.0, min(cpu_percent, 100.0))

        # Store current reading for next delta calculation
        self._prev_cpu_usec = cpu_total_usec
        self._prev_timestamp = now

        # Check for OOM kills
        oom_killed = self.cgroup.check_oom()

        return MetricsSnapshot(
            container_id=self.container_id,
            memory_usage_bytes=memory_current,
            memory_limit_bytes=memory_limit,
            cpu_usage_percent=cpu_percent,
            cpu_total_usec=cpu_total_usec,
            cpu_throttled_usec=cpu_throttled_usec,
            cpu_nr_throttled=cpu_nr_throttled,
            oom_killed=oom_killed,
        )


@dataclass
class SystemMetrics:
    """Host-level system metrics for the system info endpoint."""

    hostname: str = ""
    kernel_version: str = ""
    total_memory_bytes: int = 0
    available_memory_bytes: int = 0
    cpu_count: int = 0
    uptime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON response."""
        return {
            "hostname": self.hostname,
            "kernel_version": self.kernel_version,
            "total_memory_mb": round(self.total_memory_bytes / (1024 * 1024), 2),
            "available_memory_mb": round(self.available_memory_bytes / (1024 * 1024), 2),
            "cpu_count": self.cpu_count,
            "uptime_seconds": round(self.uptime_seconds, 2),
        }


def collect_system_metrics() -> SystemMetrics:
    """Collect host-level system information.

    Reads from /proc for portability rather than using psutil (which
    may not be installed).

    Returns:
        SystemMetrics with host information.
    """
    import os
    import platform
    from pathlib import Path

    metrics = SystemMetrics()
    metrics.hostname = platform.node()
    metrics.kernel_version = platform.release()
    metrics.cpu_count = os.cpu_count() or 1

    # Parse /proc/meminfo for memory stats
    try:
        meminfo = Path("/proc/meminfo").read_text()
        for line in meminfo.split("\n"):
            if line.startswith("MemTotal:"):
                metrics.total_memory_bytes = int(line.split()[1]) * 1024  # kB to bytes
            elif line.startswith("MemAvailable:"):
                metrics.available_memory_bytes = int(line.split()[1]) * 1024
    except OSError:
        pass

    # Parse /proc/uptime
    try:
        uptime_str = Path("/proc/uptime").read_text()
        metrics.uptime_seconds = float(uptime_str.split()[0])
    except (OSError, ValueError):
        pass

    return metrics
