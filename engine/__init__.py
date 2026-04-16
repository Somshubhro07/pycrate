"""
PyCrate Container Engine
========================

A container runtime built from scratch in Python using Linux kernel primitives.
Uses ctypes to call Linux syscalls directly -- clone(), unshare(), pivot_root(),
mount() -- to create process isolation via namespaces, resource limits via cgroups v2,
and filesystem isolation via Alpine Linux rootfs.

This is the core engine. It knows nothing about HTTP, databases, or UIs.
It only knows how to create, run, and destroy isolated Linux processes.

Usage:
    from engine import ContainerManager, ContainerConfig

    manager = ContainerManager(max_containers=4)
    manager.initialize()

    config = ContainerConfig(
        name="my-container",
        command=["/bin/sh"],
        cpu_limit_percent=50,
        memory_limit_mb=64,
    )
    container = manager.create_container(config)
    manager.start_container(container.container_id)
"""

from engine.config import ContainerConfig
from engine.container import Container, ContainerManager, ContainerStatus
from engine.exceptions import (
    CgroupError,
    ContainerAlreadyRunningError,
    ContainerAlreadyStoppedError,
    ContainerError,
    ContainerLimitReachedError,
    ContainerNotFoundError,
    ImageNotFoundError,
    NamespaceError,
    NetworkError,
    OOMKilledError,
    PyCrateError,
    RootfsError,
)
from engine.images import (
    IMAGE_REGISTRY,
    ImageSpec,
    get_image_spec,
    list_images,
    parse_image_ref,
    pull_image,
    remove_image,
)
from engine.metrics import MetricsSnapshot, SystemMetrics, collect_system_metrics
from engine.overlay import cleanup_overlay, get_overlay_diff, setup_overlay
from engine.security import (
    ALLOWED_CAPABILITIES,
    BLOCKED_SYSCALLS_X86_64,
    drop_capabilities,
    harden_container,
    install_seccomp_filter,
)

__all__ = [
    # Core
    "Container",
    "ContainerConfig",
    "ContainerManager",
    "ContainerStatus",
    # Images
    "IMAGE_REGISTRY",
    "ImageSpec",
    "get_image_spec",
    "list_images",
    "parse_image_ref",
    "pull_image",
    "remove_image",
    # Overlay
    "setup_overlay",
    "cleanup_overlay",
    "get_overlay_diff",
    # Security
    "ALLOWED_CAPABILITIES",
    "BLOCKED_SYSCALLS_X86_64",
    "drop_capabilities",
    "harden_container",
    "install_seccomp_filter",
    # Metrics
    "MetricsSnapshot",
    "SystemMetrics",
    "collect_system_metrics",
    # Exceptions
    "PyCrateError",
    "ContainerError",
    "ContainerNotFoundError",
    "ContainerAlreadyRunningError",
    "ContainerAlreadyStoppedError",
    "ContainerLimitReachedError",
    "NamespaceError",
    "CgroupError",
    "OOMKilledError",
    "RootfsError",
    "ImageNotFoundError",
    "NetworkError",
]

__version__ = "0.2.0"

