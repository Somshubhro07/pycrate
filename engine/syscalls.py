"""
Linux Syscall Bindings via ctypes
==================================

Low-level interface to the Linux kernel. Every function here maps 1:1 to a
syscall or libc wrapper. This module is the only place in the codebase that
touches ctypes — everything else in the engine works with Python-level
abstractions built on top of these bindings.

These syscalls are the same ones that Docker's runc (written in Go) calls.
The difference is that Go has `syscall.Clone()` in its standard library;
Python does not, so we load libc directly.

Reference:
    - clone(2):       https://man7.org/linux/man-pages/man2/clone.2.html
    - unshare(2):     https://man7.org/linux/man-pages/man2/unshare.2.html
    - mount(2):       https://man7.org/linux/man-pages/man2/mount.2.html
    - pivot_root(2):  https://man7.org/linux/man-pages/man2/pivot_root.2.html
    - sethostname(2): https://man7.org/linux/man-pages/man2/sethostname.2.html
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import Callable

from engine.exceptions import NamespaceError

# ---------------------------------------------------------------------------
# Load libc
# ---------------------------------------------------------------------------

_libc_path = ctypes.util.find_library("c")
if _libc_path is None:
    # Fallback for systems where find_library doesn't resolve (some containers)
    _libc_path = "libc.so.6"

libc = ctypes.CDLL(_libc_path, use_errno=True)

# ---------------------------------------------------------------------------
# Clone flags (from linux/sched.h)
# ---------------------------------------------------------------------------
# Each flag creates a NEW instance of the specified namespace for the child
# process, isolating it from the parent's view of that resource.

CLONE_NEWNS = 0x00020000    # New mount namespace (filesystem isolation)
CLONE_NEWUTS = 0x04000000   # New UTS namespace (hostname isolation)
CLONE_NEWIPC = 0x08000000   # New IPC namespace (System V IPC isolation)
CLONE_NEWPID = 0x20000000   # New PID namespace (process ID isolation)
CLONE_NEWNET = 0x40000000   # New network namespace (network stack isolation)
CLONE_NEWUSER = 0x10000000  # New user namespace (UID/GID mapping)
CLONE_NEWCGROUP = 0x02000000  # New cgroup namespace

# Combined flag for full container isolation (without user namespace for now,
# since user namespaces add complexity with UID mapping that we handle separately)
CLONE_CONTAINER_FLAGS = (
    CLONE_NEWPID
    | CLONE_NEWNS
    | CLONE_NEWUTS
    | CLONE_NEWNET
    | CLONE_NEWIPC
)

# ---------------------------------------------------------------------------
# Mount flags (from linux/mount.h)
# ---------------------------------------------------------------------------

MS_BIND = 4096              # Bind mount — mount a directory at another location
MS_REC = 16384              # Recursive — apply to all submounts
MS_PRIVATE = 1 << 18        # Private mount — events don't propagate to/from parent
MS_NOSUID = 2               # Don't honor set-user-ID and set-group-ID bits
MS_NODEV = 4                # Don't allow device special files
MS_NOEXEC = 8               # Don't allow program execution
MS_RDONLY = 1                # Mount read-only
MS_REMOUNT = 32             # Remount with different flags

# ---------------------------------------------------------------------------
# Unmount flags
# ---------------------------------------------------------------------------

MNT_DETACH = 2              # Lazy unmount — detach now, cleanup when last ref drops

# ---------------------------------------------------------------------------
# Stack size for clone()
# ---------------------------------------------------------------------------

STACK_SIZE = 1024 * 1024    # 1MB — standard child process stack size


def _check_errno(result: int, syscall_name: str) -> int:
    """Check if a syscall returned an error and raise NamespaceError if so.

    Linux syscalls return -1 on failure and set errno. ctypes.get_errno()
    reads the thread-local errno value.
    """
    if result == -1:
        errno = ctypes.get_errno()
        raise NamespaceError(
            message=os.strerror(errno),
            syscall=syscall_name,
            errno=errno,
        )
    return result


# ---------------------------------------------------------------------------
# Syscall wrappers
# ---------------------------------------------------------------------------

# Type alias for the clone child function signature.
# clone() expects: int (*fn)(void *arg)
CLONE_FUNC_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)


def clone(child_func: Callable[[], int], flags: int) -> int:
    """Create a child process in new namespaces.

    This is the core syscall that creates container isolation. The child
    process starts in new namespace(s) specified by `flags`, meaning it
    has its own view of PIDs, mounts, hostname, etc.

    Unlike fork(), clone() lets us specify exactly which resources to share
    or isolate between parent and child.

    Args:
        child_func: Function to execute in the child process. Must return an int.
        flags: Bitwise OR of CLONE_NEW* flags specifying which namespaces to create.

    Returns:
        PID of the child process (from the parent's PID namespace).

    Raises:
        NamespaceError: If the clone syscall fails (e.g., permission denied,
            resource limits exceeded).
    """
    # Allocate a stack for the child process. clone() requires an explicit
    # stack because the child may be in a new PID namespace and can't share
    # the parent's stack.
    child_stack = ctypes.create_string_buffer(STACK_SIZE)

    # Stack grows downward on x86_64 — pass the TOP of the stack
    stack_top = ctypes.cast(
        ctypes.addressof(child_stack) + STACK_SIZE,
        ctypes.c_void_p,
    )

    # Wrap the Python callable in a C function pointer
    @CLONE_FUNC_TYPE
    def _child_wrapper(_arg):
        try:
            return child_func()
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
        except Exception:
            return 1

    # SIGCHLD tells the kernel to send SIGCHLD to the parent when the child
    # exits, which is required for waitpid() to work correctly.
    import signal
    clone_flags = flags | signal.SIGCHLD

    result = libc.clone(
        _child_wrapper,
        stack_top,
        ctypes.c_int(clone_flags),
        ctypes.c_void_p(0),  # No argument to child function
    )

    return _check_errno(result, "clone")


def unshare(flags: int) -> None:
    """Disassociate the calling process from specified namespaces.

    Unlike clone() which creates a new process, unshare() modifies the
    calling process's namespace memberships. Useful for moving the current
    process into new namespaces after it's already running.

    This is what we call inside the child process after clone() to finalize
    namespace setup (e.g., for mount namespace propagation).

    Args:
        flags: Bitwise OR of CLONE_NEW* flags.

    Raises:
        NamespaceError: If the unshare syscall fails.
    """
    result = libc.unshare(ctypes.c_int(flags))
    _check_errno(result, "unshare")


def sethostname(hostname: str) -> None:
    """Set the hostname in the current UTS namespace.

    Only affects the container's UTS namespace — the host hostname is unchanged.
    This is what makes `hostname` inside the container return the container ID
    instead of the EC2 instance's hostname.

    Args:
        hostname: The hostname to set (typically the container ID).

    Raises:
        NamespaceError: If sethostname fails.
    """
    name_bytes = hostname.encode("utf-8")
    result = libc.sethostname(name_bytes, len(name_bytes))
    _check_errno(result, "sethostname")


def mount(
    source: str,
    target: str,
    fstype: str | None = None,
    flags: int = 0,
    data: str | None = None,
) -> None:
    """Mount a filesystem.

    Wraps the mount(2) syscall. Used to set up /proc, /sys, /dev inside
    the container's mount namespace, and to perform bind mounts for the rootfs.

    Args:
        source: Source device or directory (e.g., "proc", "sysfs", or a path).
        target: Mount point path.
        fstype: Filesystem type (e.g., "proc", "sysfs", "tmpfs"). None for bind mounts.
        flags: Mount flags (MS_BIND, MS_NOSUID, etc.).
        data: Filesystem-specific mount options.

    Raises:
        NamespaceError: If the mount syscall fails.
    """
    result = libc.mount(
        source.encode("utf-8"),
        target.encode("utf-8"),
        fstype.encode("utf-8") if fstype else None,
        ctypes.c_ulong(flags),
        data.encode("utf-8") if data else None,
    )
    _check_errno(result, "mount")


def umount2(target: str, flags: int = 0) -> None:
    """Unmount a filesystem, optionally with MNT_DETACH for lazy unmount.

    Args:
        target: Mount point to unmount.
        flags: Unmount flags (e.g., MNT_DETACH).

    Raises:
        NamespaceError: If umount2 fails.
    """
    result = libc.umount2(target.encode("utf-8"), ctypes.c_int(flags))
    _check_errno(result, "umount2")


def pivot_root(new_root: str, put_old: str) -> None:
    """Change the root filesystem.

    pivot_root moves the current root to `put_old` and makes `new_root`
    the new root filesystem. This is the syscall that gives each container
    its own isolated filesystem view.

    After pivot_root, the container process sees `new_root` as `/` and
    cannot access the host filesystem (once we unmount `put_old`).

    This is preferred over chroot because:
    1. pivot_root actually changes the mount namespace root, not just the
       process's root directory reference.
    2. It's harder to escape than chroot (no chroot-escape attacks).
    3. It's what real container runtimes (runc, crun) use.

    Args:
        new_root: Path that will become the new root filesystem.
        put_old: Directory under new_root where the old root will be moved.

    Raises:
        NamespaceError: If pivot_root fails.
    """
    # pivot_root is a syscall (not a libc wrapper on most systems),
    # so we call it via syscall() with the syscall number.
    # x86_64 syscall number for pivot_root is 155.
    SYS_PIVOT_ROOT = 155

    result = libc.syscall(
        ctypes.c_long(SYS_PIVOT_ROOT),
        new_root.encode("utf-8"),
        put_old.encode("utf-8"),
    )
    _check_errno(result, "pivot_root")


def setns(fd: int, nstype: int) -> None:
    """Join an existing namespace.

    Used to enter a running container's namespace (e.g., for exec or
    network setup from the host side).

    Args:
        fd: File descriptor for the namespace (from /proc/[pid]/ns/*).
        nstype: Namespace type flag (CLONE_NEWPID, CLONE_NEWNET, etc.).
            Pass 0 to join whatever namespace the fd refers to.

    Raises:
        NamespaceError: If setns fails.
    """
    result = libc.setns(ctypes.c_int(fd), ctypes.c_int(nstype))
    _check_errno(result, "setns")
