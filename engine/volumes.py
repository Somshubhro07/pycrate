"""
Volume Mounts
===============

Bind mount support for mapping host directories into containers.
This enables the critical use case of mounting application code into
a container for development.

Usage:
    pycrate run alpine /bin/sh -v /host/path:/container/path
    pycrate run alpine /bin/sh -v ./mycode:/app
    pycrate run alpine /bin/sh -v /data:/data:ro

Volume types:
    - Bind mounts: Map a host directory into the container (only type supported)

Bind mounts are set up AFTER the OverlayFS merge but BEFORE pivot_root,
so the mounted directories appear transparently inside the container.

Security note:
    Bind mounts bypass the OverlayFS isolation -- the container gets
    direct read/write access to the host directory. Use :ro for
    read-only mounts when possible.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from engine.exceptions import RootfsError
from engine.syscalls import MS_BIND, MS_REC, MS_RDONLY, MS_REMOUNT, mount

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolumeMount:
    """A bind mount from host to container.

    Attributes:
        host_path: Absolute path on the host filesystem.
        container_path: Path inside the container (relative to rootfs).
        read_only: If True, mount as read-only.
    """

    host_path: str
    container_path: str
    read_only: bool = False

    @classmethod
    def parse(cls, spec: str) -> VolumeMount:
        """Parse a volume mount string.

        Formats:
            /host:/container         -- read-write bind mount
            /host:/container:ro      -- read-only bind mount
            ./relative:/container    -- relative path (resolved to absolute)

        Args:
            spec: Volume mount specification string.

        Returns:
            Parsed VolumeMount.

        Raises:
            RootfsError: If the spec is invalid.
        """
        parts = spec.split(":")
        read_only = False

        if len(parts) == 3:
            host, container, mode = parts
            if mode == "ro":
                read_only = True
            elif mode != "rw":
                raise RootfsError(
                    f"Invalid volume mode '{mode}' in '{spec}'. Use 'ro' or 'rw'."
                )
        elif len(parts) == 2:
            host, container = parts
        else:
            raise RootfsError(
                f"Invalid volume mount format: '{spec}'. "
                "Expected /host/path:/container/path[:ro|rw]"
            )

        # Resolve relative paths
        host = str(Path(host).resolve())

        # Validate host path exists
        if not Path(host).exists():
            raise RootfsError(
                f"Volume mount source does not exist: {host}"
            )

        # Container path must be absolute
        if not container.startswith("/"):
            raise RootfsError(
                f"Container path must be absolute: '{container}'"
            )

        return cls(host_path=host, container_path=container, read_only=read_only)


def setup_volume_mounts(rootfs: Path, volumes: list[VolumeMount]) -> None:
    """Set up bind mounts for volumes inside the container rootfs.

    Must be called AFTER the OverlayFS merged directory is ready but
    BEFORE pivot_root is called. The mount targets are created inside
    the merged rootfs so they're visible to the container.

    Args:
        rootfs: Path to the container's merged rootfs (overlay mount point).
        volumes: List of VolumeMount specifications.
    """
    for vol in volumes:
        # Create the mount point inside the rootfs
        # Strip leading / from container_path to make it relative
        rel_path = vol.container_path.lstrip("/")
        mount_point = rootfs / rel_path

        # Create the mount point directory (or file)
        host = Path(vol.host_path)
        if host.is_file():
            # For file mounts, create parent dirs and touch the file
            mount_point.parent.mkdir(parents=True, exist_ok=True)
            mount_point.touch(exist_ok=True)
        else:
            mount_point.mkdir(parents=True, exist_ok=True)

        # Perform the bind mount
        logger.info(
            "Bind mount: %s -> %s%s",
            vol.host_path,
            mount_point,
            " (read-only)" if vol.read_only else "",
        )

        try:
            mount(
                vol.host_path,
                str(mount_point),
                fstype=None,
                flags=MS_BIND | MS_REC,
            )

            # Apply read-only remount if needed
            if vol.read_only:
                mount(
                    vol.host_path,
                    str(mount_point),
                    fstype=None,
                    flags=MS_BIND | MS_REC | MS_REMOUNT | MS_RDONLY,
                )

        except Exception as e:
            raise RootfsError(
                f"Failed to bind mount {vol.host_path} -> "
                f"{vol.container_path}: {e}"
            ) from e

    if volumes:
        logger.info("Mounted %d volume(s)", len(volumes))
