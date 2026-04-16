"""
Namespace Management
=====================

High-level abstraction over Linux namespaces. This module doesn't call
ctypes directly — it uses the syscall bindings from syscalls.py.

A namespace is a kernel feature that partitions a system resource so that
processes in different namespaces see independent copies of it. This is
the fundamental mechanism behind container isolation.

Namespaces used by PyCrate:
    PID  - Process IDs. Container sees itself as PID 1.
    MNT  - Mount table. Container has its own filesystem mounts.
    UTS  - Hostname. Container has its own hostname.
    NET  - Network stack. Container has its own interfaces, IPs, routes.
    IPC  - Inter-process communication. Isolated message queues, semaphores.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path

from engine.exceptions import NamespaceError
from engine.syscalls import (
    CLONE_CONTAINER_FLAGS,
    CLONE_NEWIPC,
    CLONE_NEWNET,
    CLONE_NEWNS,
    CLONE_NEWPID,
    CLONE_NEWUTS,
    setns,
    sethostname,
    unshare,
)

logger = logging.getLogger(__name__)


class NamespaceType(IntFlag):
    """Namespace types mapped to their clone flags.

    Using IntFlag so multiple types can be combined with bitwise OR:
        ns_flags = NamespaceType.PID | NamespaceType.NET
    """

    PID = CLONE_NEWPID
    MOUNT = CLONE_NEWNS
    UTS = CLONE_NEWUTS
    NET = CLONE_NEWNET
    IPC = CLONE_NEWIPC

    @property
    def proc_name(self) -> str:
        """Map to the /proc/[pid]/ns/ file name for this namespace type."""
        mapping = {
            NamespaceType.PID: "pid",
            NamespaceType.MOUNT: "mnt",
            NamespaceType.UTS: "uts",
            NamespaceType.NET: "net",
            NamespaceType.IPC: "ipc",
        }
        return mapping.get(self, "unknown")


# Default namespace set for full container isolation
DEFAULT_NAMESPACES = (
    NamespaceType.PID
    | NamespaceType.MOUNT
    | NamespaceType.UTS
    | NamespaceType.NET
    | NamespaceType.IPC
)


@dataclass
class NamespaceSet:
    """Represents the set of namespaces a container is isolated into.

    Tracks the namespace flags used during clone() and provides methods
    for post-clone setup (hostname, entering existing namespaces, etc.).
    """

    flags: int = CLONE_CONTAINER_FLAGS
    hostname: str | None = None

    def setup_child(self) -> None:
        """Called inside the child process after clone().

        Sets hostname if a UTS namespace was created. This runs in the
        child's context, so it only affects the container.
        """
        if self.flags & CLONE_NEWUTS and self.hostname:
            logger.debug("Setting container hostname to '%s'", self.hostname)
            sethostname(self.hostname)

        # Make all mounts in the new mount namespace private.
        # Without this, mount events would propagate back to the host.
        if self.flags & CLONE_NEWNS:
            logger.debug("Setting mount propagation to private")
            unshare(CLONE_NEWNS)

    @staticmethod
    def enter_namespace(pid: int, ns_type: NamespaceType) -> None:
        """Enter a running container's namespace.

        Opens the namespace file descriptor from /proc and calls setns()
        to move the current thread into that namespace. Used for operations
        like setting up networking from the host side while targeting the
        container's network namespace.

        Args:
            pid: Host PID of the container's init process.
            ns_type: Which namespace to enter.

        Raises:
            NamespaceError: If the namespace file doesn't exist or setns fails.
        """
        ns_path = Path(f"/proc/{pid}/ns/{ns_type.proc_name}")

        if not ns_path.exists():
            raise NamespaceError(
                message=f"Namespace file not found: {ns_path}",
                syscall="open",
            )

        fd = os.open(str(ns_path), os.O_RDONLY)
        try:
            logger.debug(
                "Entering %s namespace of PID %d via %s",
                ns_type.proc_name,
                pid,
                ns_path,
            )
            setns(fd, ns_type.value)
        finally:
            os.close(fd)

    @staticmethod
    def get_namespace_inode(pid: int, ns_type: NamespaceType) -> int:
        """Get the inode number of a process's namespace.

        Two processes are in the same namespace if and only if their
        namespace inodes match. Useful for verifying isolation.

        Args:
            pid: Process ID (use "self" for the current process).
            ns_type: Which namespace to check.

        Returns:
            Inode number of the namespace.
        """
        ns_path = Path(f"/proc/{pid}/ns/{ns_type.proc_name}")
        stat = ns_path.stat()
        return stat.st_ino
