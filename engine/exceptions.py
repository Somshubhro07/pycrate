"""
PyCrate Exception Hierarchy
============================

All engine exceptions inherit from PyCrateError, making it easy for the API layer
to catch engine-level failures without importing individual exception types.

Hierarchy:
    PyCrateError
    ├── ContainerError           ← lifecycle failures (create, start, stop)
    │   ├── ContainerNotFound
    │   ├── ContainerAlreadyRunning
    │   ├── ContainerAlreadyStopped
    │   └── ContainerLimitReached
    ├── NamespaceError           ← clone/unshare syscall failures
    ├── CgroupError              ← cgroup v2 read/write failures
    │   └── OOMKilledError       ← container exceeded memory limit
    ├── RootfsError              ← rootfs extraction/pivot_root failures
    │   └── ImageNotFoundError   ← base image tarball missing
    └── NetworkError             ← veth/bridge setup failures
"""


class PyCrateError(Exception):
    """Base exception for all PyCrate engine errors.

    Carries a human-readable message and an optional error code
    that the API layer can forward to clients.
    """

    def __init__(self, message: str, code: str = "ENGINE_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(self.message)


# ── Container Lifecycle ───────────────────────────────────────────


class ContainerError(PyCrateError):
    """Raised when a container lifecycle operation fails."""

    def __init__(self, message: str, container_id: str | None = None) -> None:
        self.container_id = container_id
        super().__init__(message, code="CONTAINER_ERROR")


class ContainerNotFoundError(ContainerError):
    """Raised when referencing a container ID that doesn't exist."""

    def __init__(self, container_id: str) -> None:
        super().__init__(
            f"Container '{container_id}' not found",
            container_id=container_id,
        )
        self.code = "CONTAINER_NOT_FOUND"


class ContainerAlreadyRunningError(ContainerError):
    """Raised when trying to start a container that's already running."""

    def __init__(self, container_id: str) -> None:
        super().__init__(
            f"Container '{container_id}' is already running",
            container_id=container_id,
        )
        self.code = "CONTAINER_ALREADY_RUNNING"


class ContainerAlreadyStoppedError(ContainerError):
    """Raised when trying to stop a container that's not running."""

    def __init__(self, container_id: str) -> None:
        super().__init__(
            f"Container '{container_id}' is already stopped",
            container_id=container_id,
        )
        self.code = "CONTAINER_ALREADY_STOPPED"


class ContainerLimitReachedError(ContainerError):
    """Raised when the maximum number of concurrent containers is reached."""

    def __init__(self, max_containers: int) -> None:
        super().__init__(
            f"Maximum container limit reached ({max_containers}). "
            "Stop or remove an existing container first.",
        )
        self.code = "CONTAINER_LIMIT_REACHED"


# ── Namespace Operations ──────────────────────────────────────────


class NamespaceError(PyCrateError):
    """Raised when a namespace syscall (clone, unshare, setns) fails."""

    def __init__(self, message: str, syscall: str = "", errno: int = 0) -> None:
        self.syscall = syscall
        self.errno = errno
        detail = f" (syscall={syscall}, errno={errno})" if syscall else ""
        super().__init__(f"Namespace operation failed: {message}{detail}", code="NAMESPACE_ERROR")


# ── cgroup Operations ─────────────────────────────────────────────


class CgroupError(PyCrateError):
    """Raised when a cgroup v2 operation fails (read, write, cleanup)."""

    def __init__(self, message: str, cgroup_path: str = "") -> None:
        self.cgroup_path = cgroup_path
        detail = f" (path={cgroup_path})" if cgroup_path else ""
        super().__init__(f"cgroup error: {message}{detail}", code="CGROUP_ERROR")


class OOMKilledError(CgroupError):
    """Raised when a container process is killed by the kernel OOM killer."""

    def __init__(self, container_id: str, memory_limit_bytes: int) -> None:
        self.container_id = container_id
        self.memory_limit_bytes = memory_limit_bytes
        limit_mb = memory_limit_bytes / (1024 * 1024)
        super().__init__(
            f"Container '{container_id}' was OOM-killed (limit: {limit_mb:.0f}MB)",
        )
        self.code = "OOM_KILLED"


# ── Rootfs Operations ─────────────────────────────────────────────


class RootfsError(PyCrateError):
    """Raised when rootfs setup fails (extraction, pivot_root, mount)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Rootfs error: {message}", code="ROOTFS_ERROR")


class ImageNotFoundError(RootfsError):
    """Raised when the base image tarball is not found locally."""

    def __init__(self, image_name: str) -> None:
        self.image_name = image_name
        super().__init__(
            f"Image '{image_name}' not found. Run the image pull command first.",
        )
        self.code = "IMAGE_NOT_FOUND"


# ── Network Operations ────────────────────────────────────────────


class NetworkError(PyCrateError):
    """Raised when container networking setup fails (veth, bridge, IP assignment)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Network error: {message}", code="NETWORK_ERROR")
