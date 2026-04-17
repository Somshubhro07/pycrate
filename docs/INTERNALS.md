# How Containers Actually Work: A Deep Dive into Linux Internals

*Everything I learned about Linux kernel primitives by building a container runtime from scratch in Python.*

---

## The Big Idea

A container is not a virtual machine. It's not even a real thing. There is
no "container" data structure in the Linux kernel. A container is just a
regular Linux process with three restrictions applied:

1. **It can't see things** (namespaces)
2. **It can't use too much** (cgroups)
3. **It can't do dangerous things** (seccomp + capabilities)

That's it. When you run `docker run alpine /bin/sh`, Docker creates a
normal process with `clone()`, applies those three restrictions, and calls
`exec()`. The process thinks it's alone on the machine because the kernel
hides everything else from it.

PyCrate does the same thing, but in Python, so you can read every line.

---

## Part 1: clone() and Namespaces — Making a Process Blind

### What clone() actually does

Every process in Linux is created by either `fork()` or `clone()`. They're
almost identical -- `clone()` just lets you specify flags that control what
the child shares with the parent.

Normally, a child process inherits everything: same filesystem, same
network, same view of processes. But if you pass `CLONE_NEWPID`, the kernel
creates a new PID namespace, and suddenly the child can't see the parent's
processes. The child's first process becomes PID 1 -- it thinks it's the
init process of its own tiny Linux system.

Here's what that looks like in code:

```python
# This is actual PyCrate code from engine/syscalls.py

# These flags are defined in linux/sched.h
CLONE_NEWPID = 0x20000000   # New PID namespace
CLONE_NEWNS  = 0x00020000   # New mount namespace
CLONE_NEWUTS = 0x04000000   # New UTS namespace (hostname)
CLONE_NEWNET = 0x40000000   # New network namespace
CLONE_NEWIPC = 0x08000000   # New IPC namespace

def clone(child_func, flags):
    # Allocate 1MB stack for the child (clone needs an explicit stack)
    child_stack = ctypes.create_string_buffer(1024 * 1024)

    # Stack grows downward on x86_64, so pass the TOP of the buffer
    stack_top = ctypes.addressof(child_stack) + len(child_stack)

    # Call the actual syscall. The child starts executing child_func
    # in a brand new set of namespaces.
    pid = libc.clone(child_func, stack_top, flags | SIGCHLD, None)
    return pid
```

When we call `clone()` with all five flags ORed together, the child process
gets five new namespaces. Let me explain what each one actually does:

### PID Namespace (CLONE_NEWPID)

The kernel maintains a mapping of PID numbers per namespace. When a process
in the new namespace calls `getpid()`, the kernel translates:

```
Host PID namespace:    PID 48291 (the container process)
Container namespace:   PID 1     (what the process sees)
```

The mapping is stored in struct `pid` in the kernel, which has one entry
per namespace level. So the same process has TWO PIDs -- its "real" PID and
its namespace PID. When you run `ps` inside the container, the kernel only
shows processes in the same namespace. The container literally cannot see
your Chrome browser, your SSH daemon, or anything else.

**Why PID 1 matters**: In Linux, PID 1 is special. If PID 1 dies, the
kernel kills everything in that namespace. This is exactly what we want --
if the container's main process exits, all its children get cleaned up
automatically. No zombie processes.

### Mount Namespace (CLONE_NEWNS)

This was the first namespace ever added to Linux (2002), which is why its
flag is just `CLONE_NEWNS` (new namespace) instead of something specific.

A mount namespace gives the process its own mount table. When the container
calls `mount()`, it only affects the container's view. The host's filesystem
is untouched.

This is what makes `pivot_root()` possible -- we mount a new root filesystem
and the container sees that as `/`, while the host's `/` continues to be
the real root.

### Network Namespace (CLONE_NEWNET)

A new network namespace starts completely empty. No `eth0`, no `lo`, no
IP addresses. The process can't do anything network-related until we set
up virtual ethernet pairs.

Here's what PyCrate does to give the container networking:

```
1. Create a "veth pair" -- two virtual ethernet interfaces connected
   like a cable. One end stays in the host namespace, one goes
   into the container.

2. Connect the host end to a bridge (like a virtual switch).

3. Assign an IP address to the container end.

4. Set up NAT (iptables MASQUERADE) so the container can reach
   the internet through the host.
```

The result looks like this:

```
Host network namespace:
    eth0 (real NIC) ← 10.0.1.5
    pycrate0 (bridge) ← 10.0.0.1/24
        └── veth-a7f3b2 (one end of the cable)

Container network namespace:
    eth0 (other end of the cable) ← 10.0.0.42/24
    default route → 10.0.0.1 (through the bridge)
```

The container thinks it has its own network card called `eth0`. It doesn't.
It's a virtual pipe that terminates at the host's bridge.

---

## Part 2: pivot_root() — Giving the Container Its Own Filesystem

This is the most important security boundary. After `clone()`, the container
process still sees the host's filesystem (it inherited the mount table).
We need to swap its root.

### Why not just chroot?

`chroot()` changes the process's root directory, but it's easy to escape.
A root process inside a chroot can just do:

```c
mkdir("escape"); chroot("escape"); chdir("../../../../../../");
// You're now at the real root
```

`pivot_root()` is stronger because it actually changes the mount namespace's
root mount point. The old root isn't just "above" the new root -- it's moved
to a subdirectory, and then we unmount it entirely.

### How pivot_root works step by step

```python
# This is the actual sequence in engine/rootfs.py

def do_pivot_root(rootfs: Path):
    # Step 1: Bind-mount the rootfs onto itself.
    # pivot_root requires the new root to be a mount point.
    mount(str(rootfs), str(rootfs), flags=MS_BIND | MS_REC)

    # Step 2: Create a directory inside the rootfs for the old root
    old_root = rootfs / ".old_root"
    old_root.mkdir(exist_ok=True)

    # Step 3: pivot_root -- swap the roots
    # After this: rootfs becomes "/" and old root moves to "/.old_root"
    os.chdir(str(rootfs))
    pivot_root(".", ".old_root")

    # Step 4: Update our working directory to the new root
    os.chroot(".")
    os.chdir("/")

    # Step 5: Unmount the old root -- this is the critical step.
    # After this, the container process CANNOT access the host
    # filesystem. The host's /home, /etc, /var -- all gone.
    umount2("/.old_root", MNT_DETACH)

    # Step 6: Remove the mount point directory
    os.rmdir("/.old_root")
```

After this sequence, if the container process tries to access `../../../etc/passwd`,
it gets its own `/etc/passwd` (from the Alpine/Ubuntu rootfs), not the host's.
There is no path traversal that can reach the host filesystem because the
mount isn't there anymore.

---

## Part 3: OverlayFS — How Images Work Without Copying Gigabytes

When you run 10 containers from the same Alpine image, you don't want 10
copies of the same 3MB filesystem. You want ONE copy shared by all 10, with
each container getting its own writable layer on top.

This is exactly what OverlayFS does.

### The layer structure on disk

```
/var/lib/pycrate/
    images/
        alpine-3.20/         ← LOWER: shared, read-only base image
            bin/
            etc/
            usr/
            ...

    containers/
        crate-a7f3b2/
            overlay/
                upper/       ← UPPER: this container's changes (empty at start)
                work/        ← WORK: OverlayFS internal bookkeeping
                merged/      ← MERGED: what the container actually sees
```

### How OverlayFS reads and writes

When the container reads a file:
1. Kernel checks `upper/` first (the container's layer)
2. If not found, falls through to `lower/` (the base image)
3. Returns the first match

When the container writes a file:
1. If the file exists in `lower/`, kernel copies it to `upper/` first
   (this is "copy on write" -- the original is never modified)
2. Write happens in `upper/`
3. The base image is STILL intact for other containers

When the container deletes a file:
1. Kernel creates a "whiteout" file in `upper/` -- a special marker
   that says "this file doesn't exist"
2. The file still exists in `lower/`, but the overlay hides it

### The mount command

```python
# This is actual PyCrate code from engine/overlay.py

mount(
    source="overlay",
    target=str(merged_dir),
    fstype="overlay",
    flags=0,
    data=f"lowerdir={lower},upperdir={upper},workdir={work}",
)
```

That single `mount()` call creates the unified view. The kernel handles
all the copy-on-write logic transparently. The container process just
sees a normal filesystem at `merged/`.

### Why this matters for performance

Without OverlayFS (the naive approach):
- Pull 150MB Ubuntu image
- Container 1: copy 150MB → 150MB used
- Container 2: copy 150MB → 300MB used
- Container 10: copy 150MB → 1.5GB used

With OverlayFS:
- Pull 150MB Ubuntu image
- Container 1: mount overlay → 150MB + ~0MB = 150MB used
- Container 2: mount overlay → 150MB + ~0MB = 150MB used
- Container 10: mount overlay → 150MB + ~0MB = 150MB used

Each container only uses disk space for the files it changes. If a
container writes 5MB of logs, it uses 5MB. Not 155MB.

---

## Part 4: cgroups v2 — Preventing a Container from Eating the Machine

Namespaces hide things. cgroups limit things. If a container tries to
use all the CPU or allocate all the memory, cgroups stop it.

### How cgroups v2 works

cgroups is a pseudo-filesystem that the kernel mounts at `/sys/fs/cgroup/`.
You control resource limits by writing values to files. No API, no library --
just echo values into files and the kernel enforces them.

```python
# This is how PyCrate sets a 50% CPU limit
# engine/cgroups.py

# Create a cgroup directory for this container
cgroup_path = Path("/sys/fs/cgroup/pycrate/crate-a7f3b2")
cgroup_path.mkdir(parents=True, exist_ok=True)

# CPU limit: 50% of one core
# Format: "quota period" in microseconds
# 50000/100000 = 50% of one core
(cgroup_path / "cpu.max").write_text("50000 100000")

# Memory limit: 64MB hard cap
# The kernel will OOM-kill the process if it exceeds this
(cgroup_path / "memory.max").write_text(str(64 * 1024 * 1024))

# Disable swap -- force clean OOM kills instead of degradation
(cgroup_path / "memory.swap.max").write_text("0")

# Assign the container process to this cgroup
(cgroup_path / "cgroup.procs").write_text(str(container_pid))
```

That's it. The kernel immediately starts enforcing these limits.

### OOM Kill Detection

When a container exceeds its memory limit, the kernel's OOM (out of memory)
killer terminates the process. PyCrate detects this by reading:

```python
events = (cgroup_path / "memory.events").read_text()
# Contains: "oom_kill 1" if the process was killed
```

This is how Docker knows to report "OOMKilled: true" in container status.

### CPU Throttling

The kernel uses CFS (Completely Fair Scheduler) bandwidth control. When
`cpu.max` is set to `"50000 100000"`:

- Every 100ms (the period), the container gets at most 50ms of CPU time
- If it exceeds that, the kernel stops scheduling it for the rest of the period
- This creates a 50% CPU cap

You can verify this while a container is running:

```bash
cat /sys/fs/cgroup/pycrate/crate-a7f3b2/cpu.stat
# usage_usec 1234567    ← total CPU time consumed
# nr_periods 500        ← number of enforcement periods
# nr_throttled 42       ← number of times the container was throttled
# throttled_usec 21000  ← total time spent throttled
```

---

## Part 5: Seccomp BPF — Blocking Dangerous Syscalls

Even with namespaces, a process can still call `reboot()`. It won't reboot
the host (network namespace prevents that), but it could do other dangerous
things: load kernel modules, call `ptrace` to debug other processes, or
create new namespaces to attempt an escape.

Seccomp (secure computing) lets you install a BPF (Berkeley Packet Filter)
program that inspects every syscall before the kernel executes it.

### How BPF filters are structured

A BPF program is a list of instructions, like a tiny assembly language.
Each instruction has an opcode, a jump target, and a value. The program
inspects a `seccomp_data` struct that the kernel passes for each syscall:

```c
struct seccomp_data {
    int   nr;           // syscall number (e.g., 165 = mount)
    __u32 arch;         // architecture (e.g., AUDIT_ARCH_X86_64)
    __u64 instruction_pointer;
    __u64 args[6];      // syscall arguments
};
```

### Building the filter in Python

PyCrate builds the BPF bytecode using ctypes. Here's the conceptual structure:

```
Instruction 0: LOAD architecture field from seccomp_data
Instruction 1: IF architecture != x86_64, JUMP to KILL
Instruction 2: LOAD syscall number field
Instruction 3: IF nr == 169 (reboot),      JUMP to KILL
Instruction 4: IF nr == 175 (init_module),  JUMP to KILL
Instruction 5: IF nr == 246 (kexec_load),   JUMP to KILL
... (one instruction per blocked syscall)
Instruction N:   RETURN ALLOW  ← syscall is OK
Instruction N+1: RETURN KILL   ← kill the process (SIGSYS)
```

### Why we check architecture first

x86_64 and i386 share some syscall numbers but map to different functions.
Syscall 165 on x86_64 is `mount`; on i386 it might be something else.
An attacker could try to invoke a 32-bit syscall to bypass the filter.
By checking the architecture first and killing on mismatch, we prevent
this ABI confusion attack.

### The actual ctypes structures

```python
# From engine/security.py

class sock_filter(ctypes.Structure):
    """One BPF instruction (8 bytes)."""
    _fields_ = [
        ("code", ctypes.c_ushort),   # opcode
        ("jt", ctypes.c_ubyte),      # jump if true
        ("jf", ctypes.c_ubyte),      # jump if false
        ("k", ctypes.c_uint),        # immediate value
    ]

class sock_fprog(ctypes.Structure):
    """BPF program: array of instructions + count."""
    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(sock_filter)),
    ]
```

The filter is loaded into the kernel with:

```python
prctl(PR_SET_NO_NEW_PRIVS, 1)       # Required before seccomp
prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, prog)
```

After this call, the filter is locked in. The process can't remove it,
load a different one, or escalate privileges. Every subsequent syscall
passes through the filter.

---

## Part 6: Capabilities — Breaking Root Into Pieces

Linux capabilities decompose root's unlimited power into ~41 discrete
privileges. Instead of asking "is this process root?", the kernel asks
"does this process have CAP_NET_ADMIN?" or "does this process have
CAP_SYS_MODULE?".

### What PyCrate drops

```python
# Capabilities we DROP — the container cannot:
CAP_SYS_ADMIN      # Mount filesystems, use namespaces, many others
CAP_SYS_MODULE     # Load kernel modules (the scariest capability)
CAP_SYS_PTRACE     # Debug other processes
CAP_SYS_RAWIO      # Access hardware directly
CAP_NET_ADMIN      # Modify network configuration (iptables, routes)
CAP_SYS_BOOT       # Reboot the system
CAP_SYS_TIME       # Change the system clock
```

### How dropping works

```python
# From engine/security.py

import ctypes

PR_CAPBSET_DROP = 24  # prctl operation code

def drop_capabilities():
    libc = ctypes.CDLL("libc.so.6", use_errno=True)

    for cap_number in CAPS_TO_DROP:
        result = libc.prctl(PR_CAPBSET_DROP, cap_number, 0, 0, 0)
        if result != 0:
            # This capability isn't available on this kernel -- skip it
            pass
```

The `prctl(PR_CAPBSET_DROP)` call removes a capability from the bounding
set. Once dropped, the process can never reacquire it, even if it exec's
a setuid binary. This is a one-way door.

### The order matters

In PyCrate's child process, we do:
1. Set up filesystem mounts
2. `pivot_root()` (needs `CAP_SYS_ADMIN` -- we still have it)
3. Drop capabilities (now `CAP_SYS_ADMIN` is gone forever)
4. Install seccomp filter
5. `execvp()` the user's command

If we dropped `CAP_SYS_ADMIN` before step 2, `pivot_root` would fail.
If we installed seccomp before step 3, the capability-dropping `prctl()`
calls might be blocked by the filter. The order is deliberate.

---

## Part 7: The Reconciliation Loop — How Orchestration Actually Works

This is the concept that powers Kubernetes, Docker Swarm, and now PyCrate.
It's deceptively simple.

### Desired state vs actual state

Instead of imperative commands ("start container X on node Y"), the user
declares what they want:

```yaml
services:
  web:
    image: alpine:3.20
    replicas: 3
    restart: always
```

This means: "I want 3 instances of web running at all times."

### The loop

Every 5 seconds, the orchestrator:

```
1. Read desired state (from the manifest)
2. Read actual state (from the running containers)
3. Compute the diff
4. Apply changes to make actual match desired
```

```python
def reconcile():
    for service in manifest.services:
        running = count_running_instances(service.name)

        if running < service.replicas:
            # Not enough instances -- start more
            for i in range(service.replicas - running):
                start_new_instance(service)

        elif running > service.replicas:
            # Too many instances -- stop extras
            stop_excess_instances(service, running - service.replicas)

        # Check for unhealthy instances that need restart
        for instance in get_instances(service.name):
            if instance.health == UNHEALTHY:
                if service.restart == "always":
                    restart_instance(instance)
```

### Why this pattern is powerful

The reconciliation loop is self-healing. If a container crashes:
1. Its health check fails
2. Next reconciliation loop detects `running < desired`
3. A new container is started automatically
4. No human intervention needed

This is the same pattern at every scale:
- **PyCrate Compose**: Single-node, reconcile every 5 seconds
- **Kubernetes controller**: Cluster-wide, reconcile on events + periodic
- **AWS Auto Scaling**: Fleet-wide, reconcile on CloudWatch alarms

The concept is identical. The scope is different.

---

## What I Learned

Building a container runtime teaches you that containers are not magic.
They're a combination of kernel features that have existed since 2002-2016,
glued together with about 2000 lines of plumbing code.

The kernel does all the hard work:
- Namespaces make things invisible → kernel data structure isolation
- cgroups limit resources → kernel scheduler + memory allocator enforcement
- Seccomp blocks syscalls → kernel BPF filter at syscall entry point
- OverlayFS shares filesystems → kernel filesystem driver

"Container runtimes" like Docker, runc, containerd, and PyCrate are just
different ways of calling these kernel features. The difference is in the
interface, not the mechanism.

The most surprising thing? All of this is controlled by writing to files
and calling syscalls. No special APIs. No kernel modules. No privileged
daemons (well, you need root, but the mechanisms are built into the
standard kernel). The same kernel running on your laptop has all of these
features. They're just waiting for someone to call `clone()` with the
right flags.

---

*PyCrate is open source at [github.com/Somshubhro07/pycrate](https://github.com/Somshubhro07/pycrate).
Every concept in this document has a corresponding implementation in
readable Python. Start with `engine/syscalls.py`.*
