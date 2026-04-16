"""
Container Lifecycle Manager
=============================

This is the central module of the engine. It orchestrates all the pieces —
namespaces, cgroups, rootfs, networking — into a coherent container lifecycle:

    create() -> start() -> [running] -> stop() -> destroy()

The Container class represents a single isolated process. It tracks state
transitions, manages child process monitoring, and coordinates cleanup.

Process model:
    - The engine (this process) is the parent.
    - Each container is a child process created via clone().
    - The child runs in new namespaces with its own rootfs and cgroup limits.
    - The parent monitors the child via waitpid() in a background thread.
    - When the child exits, the parent detects it and updates state.

State machine:
    CREATED  -> start() -> RUNNING
    RUNNING  -> stop()  -> STOPPED
    RUNNING  -> (crash) -> STOPPED (with error field set)
    STOPPED  -> start() -> RUNNING (restart)
    STOPPED  -> destroy() -> (removed)
    CREATED  -> destroy() -> (removed)
"""

from __future__ import annotations

import enum
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.cgroups import CgroupController, CgroupLimits, ensure_pycrate_cgroup
from engine.config import ContainerConfig
from engine.exceptions import (
    ContainerAlreadyRunningError,
    ContainerAlreadyStoppedError,
    ContainerError,
    ContainerLimitReachedError,
    ContainerNotFoundError,
    OOMKilledError,
)
from engine.images import get_image_spec, parse_image_ref, pull_image
from engine.metrics import MetricsCollector, MetricsSnapshot
from engine.networking import NetworkConfig, cleanup_networking, create_veth_pair, setup_bridge
from engine.overlay import cleanup_overlay, setup_overlay
from engine.rootfs import do_pivot_root, setup_mounts
from engine.namespaces import NamespaceSet
from engine.security import harden_container
from engine.syscalls import CLONE_CONTAINER_FLAGS, clone

logger = logging.getLogger(__name__)


class ContainerStatus(str, enum.Enum):
    """Container lifecycle states."""

    CREATED = "created"   # Config validated, rootfs prepared, not yet running
    RUNNING = "running"   # Child process is alive in namespaces
    STOPPED = "stopped"   # Child process exited (clean or error)
    ERROR = "error"       # Failed to start or crashed with unrecoverable error


class Container:
    """A single isolated Linux process with its own namespaces, cgroup, and rootfs.

    Thread safety:
        - State mutations are protected by _state_lock.
        - The monitor thread and stop() coordinate via _stop_event so only
          one of them calls waitpid() for the child process.
        - Read operations (status, metrics, logs) use their own locks and
          are safe to call concurrently.
    """

    def __init__(self, config: ContainerConfig) -> None:
        self.config = config
        self.container_id = config.container_id
        self.name = config.name
        self.status = ContainerStatus.CREATED

        # Process tracking
        self._pid: int | None = None           # Host PID of the container process
        self._exit_code: int | None = None     # Exit code after process ends
        self._error: str | None = None         # Error message if something failed
        self._monitor_thread: threading.Thread | None = None

        # Synchronization primitives
        self._state_lock = threading.Lock()    # Protects status transitions
        self._stop_event = threading.Event()   # Signals monitor thread to exit
        self._finalized = False                # Prevents double finalization

        # Subsystem controllers (initialized during create/start)
        self._cgroup: CgroupController | None = None
        self._metrics_collector: MetricsCollector | None = None
        self._network_config: NetworkConfig | None = None
        self._namespaces: NamespaceSet | None = None

        # Log capture
        self._log_buffer: list[str] = []
        self._log_lock = threading.Lock()

        # Timestamps
        self.created_at = datetime.now(timezone.utc)
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None

    @property
    def pid(self) -> int | None:
        """Host PID of the container's init process, or None if not running."""
        return self._pid

    @property
    def exit_code(self) -> int | None:
        """Exit code of the container process, or None if still running or never started."""
        return self._exit_code

    @property
    def error(self) -> str | None:
        """Error message if the container is in an error state."""
        return self._error

    @property
    def is_running(self) -> bool:
        """Check if the container process is currently alive."""
        return self.status == ContainerStatus.RUNNING and self._pid is not None

    def create(self) -> None:
        """Phase 1: Prepare the container's resources without starting it.

        Prepares:
            - Root filesystem (extract Alpine tarball)
            - cgroup directory with limits
            - Namespace configuration

        After create(), the container is in CREATED state and ready to start.
        This is separated from start() so the API can return the container
        configuration before the process actually launches.

        Raises:
            ContainerError: If resource preparation fails.
        """
        logger.info(
            "Creating container %s (name=%s, cpu=%d%%, mem=%dMB)",
            self.container_id,
            self.name,
            self.config.cpu_limit_percent,
            self.config.memory_limit_mb,
        )

        try:
            # Pull the base image (cached after first pull)
            image_name, image_version = parse_image_ref(self.config.image)
            spec = get_image_spec(image_name, image_version)
            image_path = pull_image(spec)

            # Set up OverlayFS (copy-on-write layer on top of shared image)
            self._rootfs_path = setup_overlay(self.container_id, image_path)

            # Set up cgroup with resource limits
            limits = CgroupLimits(
                cpu_quota_us=self.config.cpu_quota_us,
                cpu_period_us=self.config.CGROUP_CPU_PERIOD,
                memory_limit_bytes=self.config.memory_limit_bytes,
            )
            self._cgroup = CgroupController(self.container_id, limits)
            self._cgroup.create()

            # Configure namespaces
            self._namespaces = NamespaceSet(
                flags=CLONE_CONTAINER_FLAGS,
                hostname=self.config.hostname,
            )

            self.status = ContainerStatus.CREATED
            logger.info("Container %s created successfully", self.container_id)

        except Exception as e:
            self.status = ContainerStatus.ERROR
            self._error = str(e)
            logger.error("Failed to create container %s: %s", self.container_id, e)
            raise ContainerError(str(e), container_id=self.container_id) from e

    def start(self) -> None:
        """Phase 2: Start the container process in isolated namespaces.

        Forks a child process via clone() with namespace flags. The child:
            1. Sets up mount namespace (mounts /proc, /sys, /dev)
            2. Executes pivot_root to switch to the container's rootfs
            3. Sets hostname in the UTS namespace
            4. Execs the configured command (e.g., /bin/sh)

        The parent:
            1. Assigns the child to the cgroup
            2. Sets up networking (veth pair, bridge, IP)
            3. Starts a monitor thread to detect when the child exits

        Raises:
            ContainerAlreadyRunningError: If the container is already running.
            ContainerError: If the start sequence fails.
        """
        if self.status == ContainerStatus.RUNNING:
            raise ContainerAlreadyRunningError(self.container_id)

        if self.status == ContainerStatus.ERROR and self._error:
            raise ContainerError(
                f"Container is in error state: {self._error}",
                container_id=self.container_id,
            )

        logger.info("Starting container %s", self.container_id)
        self.started_at = datetime.now(timezone.utc)
        self.stopped_at = None
        self._exit_code = None
        self._error = None

        try:
            # Use the overlay merged path as rootfs
            rootfs = self._rootfs_path

            # The function that runs inside the child process after clone().
            # At this point, the child is already in new namespaces.
            config = self.config  # Capture for closure

            def child_function() -> int:
                try:
                    # Set up mount namespace: mount /proc, /sys, /dev
                    setup_mounts(rootfs)

                    # Pivot into the container's rootfs
                    do_pivot_root(rootfs)

                    # Set hostname in UTS namespace
                    if config.hostname:
                        from engine.syscalls import sethostname
                        sethostname(config.hostname)

                    # Set environment variables
                    for key, value in config.env.items():
                        os.environ[key] = value

                    # Apply security hardening (capability drop + seccomp)
                    if config.security_enabled:
                        harden_container()

                    # Execute the container's command
                    # execvp replaces the current process image
                    os.execvp(config.command[0], config.command)

                except Exception as e:
                    # Write error to stderr (will be captured in logs)
                    import sys
                    print(f"Container init failed: {e}", file=sys.stderr)
                    return 1

                return 0  # Never reached if execvp succeeds

            # Fork the child process with namespace isolation
            child_pid = clone(child_function, CLONE_CONTAINER_FLAGS)
            self._pid = child_pid
            logger.info("Container %s started with PID %d", self.container_id, child_pid)

            # Assign the child to its cgroup (applies resource limits)
            if self._cgroup:
                self._cgroup.assign(child_pid)

            # Set up networking (veth pair + bridge)
            try:
                self._network_config = create_veth_pair(self.container_id, child_pid)
                logger.info(
                    "Container %s networking: IP=%s",
                    self.container_id,
                    self._network_config.container_ip,
                )
            except Exception as e:
                logger.warning(
                    "Networking setup failed for %s (container will run without network): %s",
                    self.container_id, e,
                )

            # Initialize metrics collector
            if self._cgroup:
                self._metrics_collector = MetricsCollector(self.container_id, self._cgroup)

            # Set status BEFORE starting monitor thread.
            # The monitor thread may fire _finalize_stop() immediately
            # if the child exits fast, so status must already be RUNNING.
            self.status = ContainerStatus.RUNNING
            self._finalized = False
            self._stop_event.clear()

            # Start background thread to monitor child process
            self._monitor_thread = threading.Thread(
                target=self._monitor_process,
                name=f"monitor-{self.container_id}",
                daemon=True,
            )
            self._monitor_thread.start()

        except Exception as e:
            self.status = ContainerStatus.ERROR
            self._error = str(e)
            logger.error("Failed to start container %s: %s", self.container_id, e)
            raise ContainerError(str(e), container_id=self.container_id) from e

    def stop(self, timeout: int = 10) -> None:
        """Stop a running container.

        Sends SIGTERM to the container process, waits up to `timeout` seconds,
        then sends SIGKILL if the process hasn't exited.

        This mirrors Docker's stop behavior:
            1. SIGTERM (graceful shutdown signal)
            2. Wait for timeout
            3. SIGKILL (force kill)

        Coordination with _monitor_process():
            stop() signals _stop_event so the monitor thread knows we are
            handling the waitpid. The monitor thread checks _stop_event and
            skips its own waitpid/finalize if set. This prevents the double-
            waitpid race condition.

        Args:
            timeout: Seconds to wait after SIGTERM before sending SIGKILL.

        Raises:
            ContainerAlreadyStoppedError: If not running.
        """
        if not self.is_running:
            raise ContainerAlreadyStoppedError(self.container_id)

        logger.info("Stopping container %s (timeout=%ds)", self.container_id, timeout)

        # Signal the monitor thread that we are handling the stop
        self._stop_event.set()

        pid = self._pid
        if pid is None:
            # Already reaped by the monitor thread
            self._finalize_stop()
            return

        try:
            # Send SIGTERM for graceful shutdown
            os.kill(pid, signal.SIGTERM)

            # Wait for the process to exit
            for _ in range(timeout * 10):  # Check every 100ms
                try:
                    waited_pid, wait_status = os.waitpid(pid, os.WNOHANG)
                    if waited_pid != 0:
                        self._exit_code = os.WEXITSTATUS(wait_status) if os.WIFEXITED(wait_status) else -1
                        break
                except ChildProcessError:
                    # Already reaped by monitor thread
                    break
                time.sleep(0.1)
            else:
                # Timeout: force kill
                logger.warning("Container %s did not stop gracefully, sending SIGKILL", self.container_id)
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ChildProcessError, ProcessLookupError):
                    pass
                self._exit_code = -1

        except ProcessLookupError:
            # Process already exited
            self._exit_code = 0

        self._finalize_stop()

        # Wait for the monitor thread to finish so it doesn't access
        # stale state after we return
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2)

    def destroy(self) -> None:
        """Remove the container and all its resources.

        If the container is running, it will be stopped first.
        Releases: cgroup, rootfs, networking.

        After destroy(), this Container object should be discarded.
        """
        logger.info("Destroying container %s", self.container_id)

        # Stop if running
        if self.is_running:
            try:
                self.stop(timeout=5)
            except Exception:
                # Force kill on error
                pid = self._pid
                if pid:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

        # Ensure monitor thread is dead before cleaning up resources
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._stop_event.set()
            self._monitor_thread.join(timeout=3)

        # Clean up cgroup
        if self._cgroup:
            try:
                self._cgroup.cleanup()
            except Exception as e:
                logger.warning("cgroup cleanup failed for %s: %s", self.container_id, e)

        # Clean up networking
        if self._network_config and self._network_config.veth_host:
            cleanup_networking(self.container_id, self._network_config.veth_host)

        # Clean up OverlayFS and rootfs
        cleanup_overlay(self.container_id)

        logger.info("Container %s destroyed", self.container_id)

    def collect_metrics(self) -> MetricsSnapshot | None:
        """Collect current resource usage metrics.

        Returns None if metrics collection is not available (container
        not started, or cgroup not initialized).
        """
        if self._metrics_collector and self.is_running:
            return self._metrics_collector.collect()
        return None

    def get_logs(self, tail: int | None = None) -> list[str]:
        """Retrieve captured log lines.

        Args:
            tail: If set, return only the last N lines.

        Returns:
            List of log line strings.
        """
        with self._log_lock:
            if tail and tail > 0:
                return self._log_buffer[-tail:]
            return list(self._log_buffer)

    def append_log(self, line: str) -> None:
        """Append a log line to the container's log buffer.

        Thread-safe. Called from the log capture thread.
        """
        with self._log_lock:
            self._log_buffer.append(line)
            # Cap at 10000 lines to prevent memory issues
            if len(self._log_buffer) > 10000:
                self._log_buffer = self._log_buffer[-5000:]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the container state for API responses and MongoDB storage."""
        result = {
            "container_id": self.container_id,
            "name": self.name,
            "status": self.status.value,
            "image": self.config.image,
            "config": self.config.to_dict(),
            "pid": self._pid,
            "exit_code": self._exit_code,
            "error": self._error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
        }

        if self._network_config:
            result["network"] = {
                "ip_address": self._network_config.container_ip,
                "veth_host": self._network_config.veth_host,
                "veth_container": self._network_config.veth_container,
            }

        return result

    def _monitor_process(self) -> None:
        """Background thread that waits for the child process to exit.

        Uses waitpid() in a polling loop instead of blocking, so the thread
        can detect when stop() has taken over via _stop_event.

        This runs as a daemon thread and is the primary mechanism for
        detecting container crashes, OOM kills, and normal exits.
        """
        pid = self._pid
        if pid is None:
            return

        try:
            while not self._stop_event.is_set():
                try:
                    waited_pid, wait_status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    # Already reaped (by stop() or externally)
                    logger.debug("Container %s: child already reaped", self.container_id)
                    return

                if waited_pid != 0:
                    # Child exited. The monitor thread owns the reap.
                    if os.WIFEXITED(wait_status):
                        self._exit_code = os.WEXITSTATUS(wait_status)
                        logger.info(
                            "Container %s exited with code %d",
                            self.container_id,
                            self._exit_code,
                        )
                    elif os.WIFSIGNALED(wait_status):
                        sig = os.WTERMSIG(wait_status)
                        self._exit_code = 128 + sig
                        logger.info(
                            "Container %s killed by signal %d",
                            self.container_id,
                            sig,
                        )

                    # Check if OOM killed
                    if self._cgroup and self._cgroup.check_oom():
                        self._error = (
                            f"Container exceeded memory limit "
                            f"({self.config.memory_limit_mb}MB) and was killed by the kernel OOM killer"
                        )
                        logger.warning("Container %s was OOM-killed", self.container_id)

                    self._finalize_stop()
                    return

                # Poll every 200ms
                time.sleep(0.2)

        except Exception as e:
            logger.error("Monitor thread error for %s: %s", self.container_id, e)

        # If we reach here, _stop_event was set by stop(). stop() handles
        # waitpid and finalization, so we do nothing.

    def _finalize_stop(self) -> None:
        """Common cleanup after a container process exits.

        Thread-safe: uses _state_lock and _finalized flag to ensure this
        runs exactly once, even if called from both stop() and _monitor_process().
        """
        with self._state_lock:
            if self._finalized:
                return
            self._finalized = True
            self.status = ContainerStatus.STOPPED
            self.stopped_at = datetime.now(timezone.utc)
            self._pid = None


class ContainerManager:
    """Manages the lifecycle of all containers on this host.

    This is the top-level interface that the API layer uses. It maintains
    the registry of containers and enforces invariants like the max
    container limit.

    Thread-safe for concurrent API requests.
    """

    def __init__(self, max_containers: int = 4) -> None:
        self.max_containers = max_containers
        self._containers: dict[str, Container] = {}
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the engine.

        Call once at startup. Sets up the cgroup hierarchy and network bridge.

        Raises:
            CgroupError: If cgroups v2 is not available.
            NetworkError: If bridge creation fails.
        """
        if self._initialized:
            return

        logger.info("Initializing PyCrate engine (max_containers=%d)", self.max_containers)
        ensure_pycrate_cgroup()
        setup_bridge()
        self._initialized = True
        logger.info("PyCrate engine initialized")

    def create_container(self, config: ContainerConfig) -> Container:
        """Create a new container with the given configuration.

        Args:
            config: Container configuration.

        Returns:
            The created Container instance (in CREATED state).

        Raises:
            ContainerLimitReachedError: If max containers limit is reached.
        """
        # Check capacity under the lock, but do the heavy I/O (rootfs
        # extraction, cgroup creation) outside the lock to avoid blocking
        # all other operations during the download/extraction.
        with self._lock:
            active = sum(
                1 for c in self._containers.values()
                if c.status in (ContainerStatus.CREATED, ContainerStatus.RUNNING)
            )
            if active >= self.max_containers:
                raise ContainerLimitReachedError(self.max_containers)

        # Heavy I/O happens here, OUTSIDE the lock
        container = Container(config)
        container.create()

        with self._lock:
            # Re-check in case another thread created a container
            # while we were doing I/O (double-check pattern)
            active = sum(
                1 for c in self._containers.values()
                if c.status in (ContainerStatus.CREATED, ContainerStatus.RUNNING)
            )
            if active >= self.max_containers:
                # Clean up the container we just created
                container.destroy()
                raise ContainerLimitReachedError(self.max_containers)
            self._containers[container.container_id] = container

        return container

    def start_container(self, container_id: str) -> Container:
        """Start a created or stopped container.

        Args:
            container_id: ID of the container to start.

        Returns:
            The started Container instance.

        Raises:
            ContainerNotFoundError: If the container doesn't exist.
        """
        container = self.get_container(container_id)
        container.start()
        return container

    def stop_container(self, container_id: str, timeout: int = 10) -> Container:
        """Stop a running container.

        Args:
            container_id: ID of the container to stop.
            timeout: Seconds to wait for graceful shutdown before SIGKILL.

        Returns:
            The stopped Container instance.

        Raises:
            ContainerNotFoundError: If the container doesn't exist.
        """
        container = self.get_container(container_id)
        container.stop(timeout=timeout)
        return container

    def remove_container(self, container_id: str) -> None:
        """Remove a container and all its resources.

        Args:
            container_id: ID of the container to remove.

        Raises:
            ContainerNotFoundError: If the container doesn't exist.
        """
        container = self.get_container(container_id)
        container.destroy()

        with self._lock:
            del self._containers[container_id]

    def get_container(self, container_id: str) -> Container:
        """Look up a container by ID.

        Raises:
            ContainerNotFoundError: If not found.
        """
        container = self._containers.get(container_id)
        if container is None:
            raise ContainerNotFoundError(container_id)
        return container

    def list_containers(self, status_filter: ContainerStatus | None = None) -> list[Container]:
        """List all containers, optionally filtered by status.

        Args:
            status_filter: If set, only return containers with this status.

        Returns:
            List of Container instances.
        """
        with self._lock:
            containers = list(self._containers.values())

        if status_filter:
            containers = [c for c in containers if c.status == status_filter]

        return containers

    def collect_all_metrics(self) -> list[MetricsSnapshot]:
        """Collect metrics from all running containers.

        Returns:
            List of MetricsSnapshot objects.
        """
        snapshots = []
        for container in self.list_containers(status_filter=ContainerStatus.RUNNING):
            snapshot = container.collect_metrics()
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    def shutdown(self) -> None:
        """Gracefully stop all running containers and clean up.

        Called during engine shutdown.
        """
        logger.info("Shutting down PyCrate engine...")
        for container in self.list_containers():
            try:
                if container.is_running:
                    container.stop(timeout=5)
            except Exception as e:
                logger.warning("Error stopping container %s during shutdown: %s", container.container_id, e)
        logger.info("PyCrate engine shut down")
