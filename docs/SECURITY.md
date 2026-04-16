# PyCrate Security Model

How PyCrate isolates containers and hardens them against escape attempts.

---

## Defense Layers

PyCrate implements 4 defense layers, applied in order during container startup:

```
Layer 1: Namespaces          (kernel-enforced process isolation)
Layer 2: cgroups v2          (resource limits, OOM protection)
Layer 3: Capability dropping (reduce root privilege surface)
Layer 4: Seccomp BPF         (syscall filtering)
```

Each layer is independent. If one fails to apply (e.g., seccomp on an older
kernel), the other layers still provide isolation.

---

## Layer 1: Linux Namespaces

Created via `clone()` with the following flags:

| Namespace | Flag | What It Isolates |
|---|---|---|
| PID | `CLONE_NEWPID` | Process table. Container PID 1 can't see host processes. |
| Mount | `CLONE_NEWNS` | Filesystem mounts. Container has its own mount table. |
| UTS | `CLONE_NEWUTS` | Hostname. Container gets its own hostname. |
| Network | `CLONE_NEWNET` | Network stack. Container gets its own interfaces, IPs. |
| IPC | `CLONE_NEWIPC` | Inter-process communication. Isolated semaphores, queues. |

After `pivot_root()`, the container's root filesystem is switched to an
Alpine/Ubuntu/Debian rootfs, and the host filesystem is unmounted. The
container process cannot access any host files.

**Implementation**: `engine/syscalls.py` (clone, pivot_root) + `engine/namespaces.py`

---

## Layer 2: cgroups v2 Resource Limits

Each container gets its own cgroup at `/sys/fs/cgroup/pycrate/{container_id}/`:

| Control File | What It Does |
|---|---|
| `cpu.max` | CPU quota in microseconds per period. `"50000 100000"` = 50% of one core. |
| `memory.max` | Hard memory limit in bytes. Exceeding triggers OOM kill. |
| `memory.swap.max` | Set to `"0"` to disable swap (clean OOM kill, no degradation). |

If a container exceeds its memory limit, the kernel OOM killer terminates the
container process. PyCrate detects this via `memory.events` (oom_kill counter)
and reports it in the container status.

**Implementation**: `engine/cgroups.py`

---

## Layer 3: Capability Bounding Set

Linux capabilities break root's powers into ~38 discrete privileges. PyCrate
drops all dangerous capabilities from the container process before it execs
the user command.

### Capabilities KEPT (container needs these to function)

| Capability | Why It's Needed |
|---|---|
| `CAP_CHOWN` | Change file ownership inside container |
| `CAP_DAC_OVERRIDE` | Bypass file read/write/execute permission checks |
| `CAP_FSETID` | Set SUID/SGID bits |
| `CAP_FOWNER` | Bypass ownership checks |
| `CAP_MKNOD` | Create device nodes |
| `CAP_NET_RAW` | Raw sockets (ping) |
| `CAP_SETGID` | Set GID |
| `CAP_SETUID` | Set UID |
| `CAP_SETFCAP` | Set file capabilities |
| `CAP_SETPCAP` | Modify capability sets |
| `CAP_NET_BIND_SERVICE` | Bind to ports < 1024 |
| `CAP_SYS_CHROOT` | Use chroot |
| `CAP_KILL` | Send signals |
| `CAP_AUDIT_WRITE` | Write to audit log |

### Capabilities DROPPED

| Capability | What It Prevents |
|---|---|
| `CAP_SYS_ADMIN` | Mount, namespace manipulation, many others |
| `CAP_SYS_PTRACE` | Tracing/debugging other processes |
| `CAP_SYS_MODULE` | Loading kernel modules |
| `CAP_SYS_RAWIO` | Raw I/O port access |
| `CAP_SYS_BOOT` | Rebooting the system |
| `CAP_SYS_TIME` | Setting the system clock |
| `CAP_NET_ADMIN` | Network configuration (iptables, routes) |
| `CAP_SYS_RESOURCE` | Overriding resource limits |
| `CAP_MAC_ADMIN/OVERRIDE` | Mandatory access control |
| `CAP_SYSLOG` | Kernel syslog access |

This matches Docker's default capability set.

**Implementation**: `engine/security.py` using `prctl(PR_CAPBSET_DROP)` via ctypes.

---

## Layer 4: Seccomp BPF Filter

A Berkeley Packet Filter (BPF) program is loaded into the kernel via
`prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER)`. This filter inspects every
syscall the container makes and blocks dangerous ones.

### Blocked Syscalls (x86_64)

| Syscall | Nr | Why It's Blocked |
|---|---|---|
| `init_module` | 175 | Load kernel modules |
| `finit_module` | 313 | Load kernel modules from file |
| `delete_module` | 176 | Unload kernel modules |
| `kexec_load` | 246 | Load a new kernel for execution |
| `kexec_file_load` | 320 | Load a new kernel from file |
| `reboot` | 169 | Reboot the system |
| `acct` | 163 | Process accounting |
| `swapon` / `swapoff` | 167/168 | Swap management |
| `clock_settime` | 227 | Set system clock |
| `settimeofday` | 164 | Set system time |
| `adjtimex` | 159 | Tune system clock |
| `add_key` / `request_key` / `keyctl` | 248-250 | Kernel keyring |
| `bpf` | 321 | Load kernel BPF programs |
| `mount` / `umount2` | 165/166 | Filesystem operations (blocked after pivot_root) |
| `unshare` | 272 | Create new namespaces (prevent nested containers) |
| `setns` | 308 | Enter other namespaces |
| `personality` | 135 | Dangerous personality flags |
| `ptrace` | 101 | Debug/trace processes |
| `userfaultfd` | 323 | Used in exploits |

### Architecture Validation

The BPF filter first checks that the architecture is x86_64 (`AUDIT_ARCH_X86_64`).
If the architecture doesn't match (e.g., a 32-bit compatibility call), the
process is killed. This prevents ABI bypass attacks where an attacker invokes
a syscall number that maps to a different syscall on a different architecture.

### Filter Construction

The filter is built as a BPF program in pure Python using ctypes structures
(`sock_filter`, `sock_fprog`). No external libraries required. The filter
structure is:

```
1. BPF_LD: Load architecture from seccomp_data
2. BPF_JEQ: Verify x86_64 (kill if not)
3. BPF_LD: Load syscall number
4. For each blocked syscall:
   BPF_JEQ: If match, jump to KILL
5. BPF_RET ALLOW: No match, allow the syscall
6. BPF_RET KILL: Match found, kill the process
```

**Implementation**: `engine/security.py`

---

## Application Order

Inside the container child process, after `clone()` returns:

```python
# 1. Set up filesystem
setup_mounts(rootfs)         # Mount /proc, /sys, /dev
do_pivot_root(rootfs)        # Swap root to container rootfs

# 2. Configure environment
sethostname(hostname)        # Set container hostname
os.environ.update(env)       # Set env vars

# 3. Apply security hardening
drop_capabilities()          # Layer 3: Remove dangerous caps
install_seccomp_filter()     # Layer 4: Block dangerous syscalls

# 4. Execute user command
os.execvp(command[0], command)  # Replace process image
```

The order matters:
- Mount setup must happen BEFORE pivot_root (needs host filesystem access)
- Capabilities must be dropped BEFORE seccomp (seccomp install needs some caps)
- Both must happen BEFORE execvp (hardening applies to the executed program)

---

## Disabling Security

For debugging, security hardening can be disabled:

```bash
pycrate run alpine /bin/sh --no-security
```

Or via the engine API:

```python
config = ContainerConfig(
    name="debug",
    security_enabled=False,  # Disables layers 3 and 4
)
```

Layers 1 (namespaces) and 2 (cgroups) are always active and cannot be disabled.

---

## Comparison with Docker

| Feature | Docker | PyCrate |
|---|---|---|
| Namespaces | All 7 types | PID, MNT, UTS, NET, IPC (5 types) |
| cgroups | v1 and v2 | v2 only |
| Capabilities | Configurable per container | Default Docker set |
| Seccomp | ~44 blocked syscalls (JSON profile) | ~22 blocked syscalls (BPF bytecode) |
| AppArmor/SELinux | Supported | Not implemented |
| User namespaces | Optional (rootless mode) | Not implemented |
| Read-only rootfs | Supported | Not implemented |

PyCrate's security is not as comprehensive as Docker's, but it covers the
most critical attack vectors and demonstrates the same kernel mechanisms.
