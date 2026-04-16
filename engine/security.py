"""
Container Security Hardening
==============================

Two defense layers applied inside the container process BEFORE it execs
the user command:

1. Capability Bounding Set — drops dangerous Linux capabilities via
   prctl(PR_CAPBSET_DROP). After this, the container process cannot
   regain these capabilities even if it gains root inside the container.

2. Seccomp BPF Filter — installs a syscall filter that blocks dangerous
   system calls entirely. If the container tries to call a blocked
   syscall, the kernel sends SIGSYS (process killed).

These are the same mechanisms Docker uses. Docker's default seccomp
profile blocks ~44 syscalls; our profile blocks a similar set focused
on the most dangerous ones.

Order of operations (in the child process, after pivot_root):
    1. drop_capabilities()     — reduce the capability ceiling
    2. install_seccomp_filter() — restrict available syscalls
    3. os.execvp(command)       — run the user's command

Why this order matters:
    - Capabilities must be dropped before seccomp because the seccomp
      filter itself requires CAP_SYS_ADMIN to install. Once installed,
      the filter persists even after capabilities are dropped.
    - Actually, we use prctl(PR_SET_NO_NEW_PRIVS) which allows seccomp
      installation without CAP_SYS_ADMIN. So we can drop caps first,
      then install seccomp.

All implementations use ctypes to call libc directly, consistent with
the rest of the PyCrate engine.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import struct
from typing import ClassVar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy libc loading (shared with syscalls.py pattern)
# ---------------------------------------------------------------------------

_libc: ctypes.CDLL | None = None


def _get_libc() -> ctypes.CDLL:
    """Load and cache the C standard library."""
    global _libc
    if _libc is not None:
        return _libc

    _libc_path = ctypes.util.find_library("c")
    if _libc_path is None:
        _libc_path = "libc.so.6"

    _libc = ctypes.CDLL(_libc_path, use_errno=True)
    return _libc


# ---------------------------------------------------------------------------
# prctl constants
# ---------------------------------------------------------------------------

PR_SET_NO_NEW_PRIVS = 38    # Prevent privilege escalation
PR_CAPBSET_READ = 23        # Check if cap is in bounding set
PR_CAPBSET_DROP = 24        # Drop cap from bounding set
PR_SET_SECCOMP = 22         # Set seccomp mode
SECCOMP_MODE_FILTER = 2     # BPF filter mode


# ---------------------------------------------------------------------------
# Linux Capability Constants (from linux/capability.h)
# ---------------------------------------------------------------------------

CAP_CHOWN = 0
CAP_DAC_OVERRIDE = 1
CAP_DAC_READ_SEARCH = 2
CAP_FOWNER = 3
CAP_FSETID = 4
CAP_KILL = 5
CAP_SETGID = 6
CAP_SETUID = 7
CAP_SETPCAP = 8
CAP_LINUX_IMMUTABLE = 9
CAP_NET_BIND_SERVICE = 10
CAP_NET_BROADCAST = 11
CAP_NET_ADMIN = 12
CAP_NET_RAW = 13
CAP_IPC_LOCK = 14
CAP_IPC_OWNER = 15
CAP_SYS_MODULE = 16
CAP_SYS_RAWIO = 17
CAP_SYS_CHROOT = 18
CAP_SYS_PTRACE = 19
CAP_SYS_PACCT = 20
CAP_SYS_ADMIN = 21
CAP_SYS_BOOT = 22
CAP_SYS_NICE = 23
CAP_SYS_RESOURCE = 24
CAP_SYS_TIME = 25
CAP_SYS_TTY_CONFIG = 26
CAP_MKNOD = 27
CAP_LEASE = 28
CAP_AUDIT_WRITE = 29
CAP_AUDIT_CONTROL = 30
CAP_SETFCAP = 31
CAP_MAC_OVERRIDE = 32
CAP_MAC_ADMIN = 33
CAP_SYSLOG = 34
CAP_WAKE_ALARM = 35
CAP_BLOCK_SUSPEND = 36
CAP_AUDIT_READ = 37
CAP_LAST_CAP = 37

# Capabilities the container is ALLOWED to keep.
# This matches Docker's default capability set.
ALLOWED_CAPABILITIES = frozenset({
    CAP_CHOWN,
    CAP_DAC_OVERRIDE,
    CAP_FSETID,
    CAP_FOWNER,
    CAP_MKNOD,
    CAP_NET_RAW,
    CAP_SETGID,
    CAP_SETUID,
    CAP_SETFCAP,
    CAP_SETPCAP,
    CAP_NET_BIND_SERVICE,
    CAP_SYS_CHROOT,
    CAP_KILL,
    CAP_AUDIT_WRITE,
})


def drop_capabilities() -> None:
    """Drop all Linux capabilities except those in ALLOWED_CAPABILITIES.

    Uses prctl(PR_CAPBSET_DROP) to permanently remove dangerous capabilities
    from the bounding set. Once dropped, these capabilities can never be
    re-acquired by the container process or its children.

    Must be called inside the container process (child of clone()).
    """
    libc = _get_libc()
    dropped = []

    for cap in range(CAP_LAST_CAP + 1):
        if cap in ALLOWED_CAPABILITIES:
            continue

        # Check if the capability is currently in the bounding set
        result = libc.prctl(PR_CAPBSET_READ, cap, 0, 0, 0)
        if result < 0:
            # Capability doesn't exist on this kernel, skip
            continue

        if result == 1:
            # Capability is present, drop it
            drop_result = libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0)
            if drop_result == 0:
                dropped.append(cap)
            else:
                errno = ctypes.get_errno()
                logger.warning(
                    "Failed to drop capability %d: %s (errno=%d)",
                    cap, os.strerror(errno), errno,
                )

    if dropped:
        logger.debug("Dropped %d capabilities from bounding set", len(dropped))


def set_no_new_privs() -> None:
    """Set the no_new_privs flag on the current process.

    This prevents the process from gaining privileges through execve()
    (e.g., via setuid binaries). Required before installing a seccomp
    filter without CAP_SYS_ADMIN.

    This flag is inherited by all child processes and cannot be unset.
    """
    libc = _get_libc()
    result = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if result != 0:
        errno = ctypes.get_errno()
        logger.warning(
            "Failed to set PR_SET_NO_NEW_PRIVS: %s (errno=%d)",
            os.strerror(errno), errno,
        )


# ---------------------------------------------------------------------------
# Seccomp BPF Filter
# ---------------------------------------------------------------------------
# BPF instruction format: { code (u16), jt (u8), jf (u8), k (u32) }
# Total: 8 bytes per instruction

# BPF opcodes (from linux/bpf_common.h and linux/filter.h)
BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_K = 0x00
BPF_RET = 0x06

# Seccomp return values
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000  # Return errno to caller
SECCOMP_RET_LOG = 0x7FFC0000    # Allow but log

# Architecture validation
AUDIT_ARCH_X86_64 = 0xC000003E

# seccomp_data offsets
SECCOMP_DATA_NR_OFFSET = 0       # Syscall number
SECCOMP_DATA_ARCH_OFFSET = 4     # Architecture


# Syscall numbers to BLOCK (x86_64)
# These match Docker's default seccomp profile for the most dangerous calls.
BLOCKED_SYSCALLS_X86_64 = (
    # Kernel module operations
    175,    # init_module
    313,    # finit_module
    176,    # delete_module

    # Kernel image operations
    246,    # kexec_load
    320,    # kexec_file_load

    # System control
    169,    # reboot
    163,    # acct (process accounting)

    # Swap management
    167,    # swapon
    168,    # swapoff

    # Time manipulation
    227,    # clock_settime
    164,    # settimeofday
    159,    # adjtimex

    # Kernel keyring
    248,    # add_key
    249,    # request_key
    250,    # keyctl

    # BPF (prevent container from loading kernel BPF programs)
    321,    # bpf

    # Mount operations (after pivot_root, container shouldn't mount)
    # Note: We allow mount/umount during setup, then block them
    # via seccomp after setup is complete.
    166,    # umount2 -- blocked after pivot_root
    165,    # mount   -- blocked after pivot_root

    # Namespace manipulation (container shouldn't create new namespaces)
    272,    # unshare
    308,    # setns

    # Dangerous personality flags
    135,    # personality

    # ptrace (prevent debugging host processes)
    101,    # ptrace

    # Userfaultfd (used in exploits)
    323,    # userfaultfd
)


class _SockFilter(ctypes.Structure):
    """BPF instruction structure (struct sock_filter)."""
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class _SockFprog(ctypes.Structure):
    """BPF program structure (struct sock_fprog)."""
    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    ]


def _bpf_stmt(code: int, k: int) -> _SockFilter:
    """Create a BPF statement (no jumps)."""
    return _SockFilter(code=code, jt=0, jf=0, k=k)


def _bpf_jump(code: int, k: int, jt: int, jf: int) -> _SockFilter:
    """Create a BPF jump instruction."""
    return _SockFilter(code=code, jt=jt, jf=jf, k=k)


def _build_seccomp_filter() -> list[_SockFilter]:
    """Build the BPF program that blocks dangerous syscalls.

    The filter structure:
        1. Load architecture from seccomp_data
        2. Verify it's x86_64 (kill if not)
        3. Load syscall number
        4. Check against each blocked syscall
        5. If no match, allow
        6. If match, kill the process

    Returns:
        List of BPF instructions.
    """
    instructions = []

    # Step 1: Load the architecture
    instructions.append(_bpf_stmt(BPF_LD | BPF_W | BPF_ABS, SECCOMP_DATA_ARCH_OFFSET))

    # Step 2: Verify x86_64 architecture
    # If architecture doesn't match, kill the process (prevent ABI bypass)
    instructions.append(_bpf_jump(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0))
    instructions.append(_bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS))

    # Step 3: Load the syscall number
    instructions.append(_bpf_stmt(BPF_LD | BPF_W | BPF_ABS, SECCOMP_DATA_NR_OFFSET))

    # Step 4: Check each blocked syscall
    num_blocked = len(BLOCKED_SYSCALLS_X86_64)
    for i, syscall_nr in enumerate(BLOCKED_SYSCALLS_X86_64):
        remaining = num_blocked - i - 1
        # If match: jump to KILL (which is at remaining + 1 instructions ahead)
        # If no match: continue to next check (jf=0 means next instruction)
        instructions.append(
            _bpf_jump(BPF_JMP | BPF_JEQ | BPF_K, syscall_nr, remaining + 1, 0)
        )

    # Step 5: No match found, ALLOW the syscall
    instructions.append(_bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW))

    # Step 6: Match found, KILL the process
    instructions.append(_bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS))

    return instructions


def install_seccomp_filter() -> None:
    """Install a seccomp BPF filter that blocks dangerous syscalls.

    Must be called inside the container process AFTER pivot_root and
    capability dropping, but BEFORE execvp().

    The filter persists across execve() because we set PR_SET_NO_NEW_PRIVS.
    """
    instructions = _build_seccomp_filter()

    # Create the filter array
    filter_array = (_SockFilter * len(instructions))(*instructions)

    # Create the program structure
    prog = _SockFprog()
    prog.len = len(instructions)
    prog.filter = filter_array

    # Set no_new_privs (required before seccomp filter without CAP_SYS_ADMIN)
    set_no_new_privs()

    # Install the filter
    libc = _get_libc()
    result = libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0)

    if result != 0:
        errno = ctypes.get_errno()
        logger.warning(
            "Failed to install seccomp filter: %s (errno=%d). "
            "Container will run without syscall filtering.",
            os.strerror(errno), errno,
        )
    else:
        logger.debug(
            "Seccomp filter installed: %d instructions blocking %d syscalls",
            len(instructions), len(BLOCKED_SYSCALLS_X86_64),
        )


def harden_container() -> None:
    """Apply all security hardening to the current process.

    Convenience function that applies capabilities + seccomp in the
    correct order. Called inside the container child process.

    Order:
        1. drop_capabilities() -- reduce privilege ceiling
        2. install_seccomp_filter() -- restrict syscall surface
    """
    drop_capabilities()
    install_seccomp_filter()
    logger.debug("Security hardening complete")
