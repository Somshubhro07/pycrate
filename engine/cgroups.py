"""
cgroup v2 Controller
=====================

Manages Linux cgroups v2 for resource limiting. cgroups (control groups) are
a kernel feature that limits, accounts for, and isolates resource usage of
process groups.

PyCrate creates a cgroup hierarchy under /sys/fs/cgroup/pycrate/ and assigns
each container process to its own sub-cgroup:

    /sys/fs/cgroup/pycrate/
        crate-a7f3b2/
            cpu.max          <- CPU quota (e.g., "50000 100000" = 50%)
            memory.max       <- Memory limit in bytes
            memory.current   <- Current memory usage (read-only)
            cpu.stat         <- CPU usage statistics (read-only)
            cgroup.procs     <- PIDs assigned to this cgroup
            cgroup.events    <- Populated/frozen status

This is the same mechanism Docker uses. When you run
`docker run --memory=64m --cpus=0.5`, Docker writes the same files.

Requires:
    - cgroups v2 (unified hierarchy) — standard on Ubuntu 22.04+
    - Root privileges for cgroup directory creation
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from engine.exceptions import CgroupError, OOMKilledError

logger = logging.getLogger(__name__)

# Base path for the PyCrate cgroup hierarchy
CGROUP_BASE_PATH = Path("/sys/fs/cgroup/pycrate")


@dataclass
class CgroupLimits:
    """Resource limits to apply to a container's cgroup."""

    cpu_quota_us: int       # CPU time quota in microseconds per period
    cpu_period_us: int      # CPU period in microseconds (typically 100000 = 100ms)
    memory_limit_bytes: int  # Hard memory limit in bytes


class CgroupController:
    """Manages a single container's cgroup v2 resources.

    Lifecycle:
        1. create()  - Create the cgroup directory and set limits
        2. assign()  - Move the container process into the cgroup
        3. read_*()  - Read resource usage metrics
        4. cleanup() - Remove the cgroup directory

    Each instance manages one cgroup, identified by container_id.
    """

    def __init__(self, container_id: str, limits: CgroupLimits) -> None:
        self.container_id = container_id
        self.limits = limits
        self.cgroup_path = CGROUP_BASE_PATH / container_id

    def create(self) -> None:
        """Create the cgroup directory and apply resource limits.

        Creates:
            /sys/fs/cgroup/pycrate/{container_id}/
        Writes:
            cpu.max       <- "{quota} {period}" format
            memory.max    <- limit in bytes

        Raises:
            CgroupError: If cgroup creation or limit writes fail.
        """
        try:
            # Ensure the parent pycrate cgroup exists
            CGROUP_BASE_PATH.mkdir(parents=True, exist_ok=True)

            # Create this container's cgroup
            self.cgroup_path.mkdir(exist_ok=True)
            logger.info(
                "Created cgroup at %s (cpu=%dus/%dus, mem=%dMB)",
                self.cgroup_path,
                self.limits.cpu_quota_us,
                self.limits.cpu_period_us,
                self.limits.memory_limit_bytes // (1024 * 1024),
            )
        except OSError as e:
            raise CgroupError(
                f"Failed to create cgroup directory: {e}",
                cgroup_path=str(self.cgroup_path),
            ) from e

        # Apply CPU limit
        # cpu.max format: "$QUOTA $PERIOD" in microseconds
        # "50000 100000" means 50% of one CPU core (50ms every 100ms)
        self._write_file(
            "cpu.max",
            f"{self.limits.cpu_quota_us} {self.limits.cpu_period_us}",
        )

        # Apply memory limit
        # memory.max: hard limit in bytes. Exceeding triggers OOM kill.
        self._write_file(
            "memory.max",
            str(self.limits.memory_limit_bytes),
        )

        # Disable swap to prevent the container from using swap when it
        # hits the memory limit. We want a clean OOM kill, not degraded
        # performance from swapping.
        self._write_file("memory.swap.max", "0")

    def assign(self, pid: int) -> None:
        """Move a process into this cgroup.

        Writing a PID to cgroup.procs moves that process (and all its
        threads) into this cgroup. All resource limits immediately apply.

        Args:
            pid: Process ID to assign to this cgroup.

        Raises:
            CgroupError: If the PID write fails.
        """
        self._write_file("cgroup.procs", str(pid))
        logger.info("Assigned PID %d to cgroup %s", pid, self.container_id)

    def read_memory_usage(self) -> int:
        """Read current memory usage in bytes.

        Reads from memory.current, which reports the total memory usage
        of all processes in this cgroup.

        Returns:
            Current memory usage in bytes.
        """
        try:
            value = self._read_file("memory.current")
            return int(value.strip())
        except (OSError, ValueError):
            return 0

    def read_memory_limit(self) -> int:
        """Read the configured memory limit in bytes."""
        try:
            value = self._read_file("memory.max")
            if value.strip() == "max":
                return 0  # No limit set
            return int(value.strip())
        except (OSError, ValueError):
            return self.limits.memory_limit_bytes

    def read_cpu_usage(self) -> dict[str, int]:
        """Read CPU usage statistics.

        Parses cpu.stat which contains:
            usage_usec    - Total CPU time consumed (microseconds)
            user_usec     - CPU time in user mode
            system_usec   - CPU time in kernel mode
            nr_periods    - Number of enforcement periods elapsed
            nr_throttled  - Number of times the group was throttled
            throttled_usec - Total throttled time (microseconds)

        Returns:
            Dictionary of cpu.stat key-value pairs.
        """
        stats = {}
        try:
            content = self._read_file("cpu.stat")
            for line in content.strip().split("\n"):
                parts = line.split()
                if len(parts) == 2:
                    stats[parts[0]] = int(parts[1])
        except (OSError, ValueError):
            pass
        return stats

    def check_oom(self) -> bool:
        """Check if the container was OOM-killed.

        Reads memory.events and checks the oom_kill counter. The kernel
        increments this each time it kills a process in this cgroup due
        to memory limit exceedance.

        Returns:
            True if one or more OOM kills have occurred.
        """
        try:
            content = self._read_file("memory.events")
            for line in content.strip().split("\n"):
                parts = line.split()
                if len(parts) == 2 and parts[0] == "oom_kill":
                    return int(parts[1]) > 0
        except (OSError, ValueError):
            pass
        return False

    def cleanup(self) -> None:
        """Remove the cgroup directory.

        A cgroup can only be removed when it has no processes assigned.
        The container must be stopped before calling this.

        Raises:
            CgroupError: If the directory cannot be removed (processes
                still assigned, or permission error).
        """
        if not self.cgroup_path.exists():
            return

        try:
            # rmdir only works if the cgroup is empty (no processes)
            os.rmdir(self.cgroup_path)
            logger.info("Cleaned up cgroup %s", self.container_id)
        except OSError as e:
            raise CgroupError(
                f"Failed to remove cgroup (processes may still be assigned): {e}",
                cgroup_path=str(self.cgroup_path),
            ) from e

    def _write_file(self, filename: str, content: str) -> None:
        """Write a value to a cgroup control file.

        Args:
            filename: Name of the cgroup file (e.g., "cpu.max").
            content: Value to write.

        Raises:
            CgroupError: If the write fails.
        """
        filepath = self.cgroup_path / filename
        try:
            filepath.write_text(content)
        except OSError as e:
            raise CgroupError(
                f"Failed to write '{content}' to {filepath}: {e}",
                cgroup_path=str(filepath),
            ) from e

    def _read_file(self, filename: str) -> str:
        """Read a value from a cgroup control file.

        Args:
            filename: Name of the cgroup file (e.g., "memory.current").

        Returns:
            File contents as a string.
        """
        filepath = self.cgroup_path / filename
        return filepath.read_text()


def verify_cgroup_v2() -> bool:
    """Check if the system is running cgroups v2 (unified hierarchy).

    cgroups v2 is identified by the presence of a cgroup2 mount at
    /sys/fs/cgroup. On cgroups v1 systems, this path contains multiple
    controller-specific directories (cpu, memory, etc.) instead.

    Returns:
        True if cgroups v2 is available and mounted.
    """
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/sys/fs/cgroup" and parts[2] == "cgroup2":
                    return True
    except OSError:
        pass
    return False


def ensure_pycrate_cgroup() -> None:
    """Create the top-level PyCrate cgroup directory if it doesn't exist.

    Also enables the required controllers (cpu, memory) by writing to
    the parent's cgroup.subtree_control file.

    Raises:
        CgroupError: If cgroup v2 is not available or setup fails.
    """
    if not verify_cgroup_v2():
        raise CgroupError(
            "cgroups v2 not available. PyCrate requires a Linux kernel with "
            "cgroups v2 (unified hierarchy). Ubuntu 22.04+ has this by default."
        )

    # Enable CPU and memory controllers in the root cgroup so our
    # sub-cgroups can use them
    subtree_control = Path("/sys/fs/cgroup/cgroup.subtree_control")
    try:
        current = subtree_control.read_text()
        controllers_needed = []
        if "cpu" not in current:
            controllers_needed.append("+cpu")
        if "memory" not in current:
            controllers_needed.append("+memory")

        if controllers_needed:
            subtree_control.write_text(" ".join(controllers_needed))
            logger.info("Enabled cgroup controllers: %s", controllers_needed)
    except OSError as e:
        logger.warning(
            "Could not enable cgroup controllers (may already be enabled): %s", e
        )

    # Create the pycrate parent cgroup
    CGROUP_BASE_PATH.mkdir(parents=True, exist_ok=True)

    # Enable controllers in our parent cgroup too
    pycrate_subtree = CGROUP_BASE_PATH / "cgroup.subtree_control"
    try:
        pycrate_subtree.write_text("+cpu +memory")
    except OSError as e:
        logger.warning(
            "Could not enable controllers in pycrate cgroup: %s", e
        )
