# PyCrate Architecture

This document describes the internal architecture of PyCrate for developers
who want to understand or extend the codebase.

---

## System Overview

PyCrate has four independent components:

1. **Engine** (`engine/`) -- Python package that manages Linux containers via syscalls
2. **CLI** (`cli/`) -- Typer-based command-line interface wrapping the engine directly
3. **API** (`api/`) -- FastAPI daemon that exposes the engine over HTTP and WebSocket
4. **Dashboard** (`dashboard/`) -- Next.js web UI for visual container management

The engine has zero knowledge of HTTP, databases, or UIs. The CLI uses the
engine as a Python library directly (no network calls). The API layer imports
the engine and adapts it to HTTP semantics. The dashboard communicates
exclusively through the API.

```
User
  |
  +-- CLI (pycrate run/ps/stop/...) ---> Engine (direct Python calls)
  |
  +-- Dashboard (Next.js :3000) ---> API (FastAPI :8000) ---> Engine
```

---

## Engine Internals

### Layered Architecture

```
container.py (lifecycle orchestration)
    |
    +-- namespaces.py (namespace abstraction)
    |       |
    |       +-- syscalls.py (ctypes bindings to Linux)
    |
    +-- cgroups.py (cgroups v2 resource limits)
    |
    +-- images.py (multi-image registry + pull)
    |
    +-- overlay.py (OverlayFS copy-on-write storage)
    |
    +-- rootfs.py (filesystem isolation, pivot_root)
    |       |
    |       +-- syscalls.py (mount, pivot_root)
    |
    +-- security.py (seccomp BPF + capability dropping)
    |
    +-- networking.py (veth pairs + bridge)
    |
    +-- metrics.py (resource usage collection)
```

### Container Lifecycle

```
                create()                  start()
    [Config] ----------> [CREATED] ----------> [RUNNING]
                              |                    |
                              |                    | stop() or crash
                              |                    v
                              |               [STOPPED]
                              |                    |
                              +-------- destroy() -+-------> (removed)
```

### Syscall Flow During Container Start

1. `clone()` with `CLONE_NEWPID | CLONE_NEWNS | CLONE_NEWUTS | CLONE_NEWNET | CLONE_NEWIPC`
   - Creates child process in new namespaces
   - Returns child PID to parent

2. **In the child process:**
   - `mount()` -- bind mount rootfs, mount /proc, /sys, /dev
   - `pivot_root()` -- swap root filesystem to container rootfs
   - `umount2()` -- detach old root (host filesystem now inaccessible)
   - `sethostname()` -- set container hostname in UTS namespace
   - `prctl(PR_CAPBSET_DROP)` -- drop dangerous capabilities
   - `prctl(PR_SET_SECCOMP)` -- install syscall filter
   - `execvp()` -- replace process image with the container's command

3. **In the parent process:**
   - Write child PID to cgroup `cgroup.procs` (applies resource limits)
   - Create veth pair, move one end to child's network namespace
   - Configure IP address and routes via `nsenter` + `ip` command
   - Start monitor thread: `waitpid()` blocking until child exits

### Image Management

PyCrate supports three base images:

| Image | Pull Method | Size |
|---|---|---|
| Alpine 3.19/3.20 | HTTP tarball download | ~3MB |
| Ubuntu 22.04/24.04 | debootstrap --variant=minbase | ~150MB |
| Debian bookworm/bullseye | debootstrap --variant=minbase | ~130MB |

Images are cached at `/var/lib/pycrate/images/{name}-{version}/` and shared
across all containers using that image via OverlayFS.

### OverlayFS Storage

Instead of copying the entire image for each container, PyCrate uses
OverlayFS to layer a writable directory on top of the shared image:

```
/var/lib/pycrate/
    images/
        alpine-3.20/              <-- Shared read-only base (lowerdir)
    containers/
        crate-a7f3b2/
            overlay/
                lower -> alpine-3.20  <-- Symlink to base image
                upper/                <-- Container's changes (upperdir)
                work/                 <-- OverlayFS internal (workdir)
                merged/               <-- Unified view (visible to container)
```

Benefits:
- 10 Alpine containers share one 3MB base image
- Container creation is instant (mkdir + mount, no extraction)
- The upperdir shows exactly what the container changed

### Security Hardening

Applied in order after pivot_root, before execvp:

1. **Capability dropping**: `prctl(PR_CAPBSET_DROP)` removes 20+ dangerous
   capabilities. Container keeps 14 needed for basic operation.

2. **Seccomp BPF filter**: A BPF program blocks 22 dangerous syscalls
   (`kexec_load`, `reboot`, `mount`, `ptrace`, `bpf`, etc.). If the
   container tries a blocked syscall, the kernel kills it (SIGSYS).

See `docs/SECURITY.md` for the full list.

### cgroup v2 Resource Control

PyCrate creates a cgroup hierarchy at `/sys/fs/cgroup/pycrate/`:

```
/sys/fs/cgroup/
    cgroup.subtree_control       <- "+cpu +memory" (enabled at startup)
    pycrate/
        cgroup.subtree_control   <- "+cpu +memory"
        crate-a7f3b2/
            cpu.max              <- "50000 100000" (50% of one core)
            memory.max           <- "67108864" (64MB)
            memory.swap.max      <- "0" (no swap)
            cgroup.procs         <- PID of container process
            memory.current       <- current usage (read by metrics)
            cpu.stat             <- CPU time consumed (read by metrics)
            memory.events        <- OOM kill counter
```

### Network Architecture

```
Host namespace:
    pycrate0 (bridge) -- 10.0.0.1/24
        |
        +-- veth-a7f3b2 (host side of pair)

Container namespace (crate-a7f3b2):
    eth0 -- 10.0.0.42/24
    default route via 10.0.0.1

NAT: iptables MASQUERADE on 10.0.0.0/24
```

---

## CLI Architecture

The CLI uses Typer and wraps the engine directly (no HTTP intermediary).

```
cli/
    main.py              <- Typer app, entry point, command registration
    output.py            <- Rich terminal formatting utilities
    commands/
        run.py           <- pycrate run
        containers.py    <- pycrate ps/stop/rm/logs/inspect
        images.py        <- pycrate pull/images/rmi
        dashboard.py     <- pycrate dashboard
```

Commands directly instantiate `ContainerManager` and call engine methods.
No serialization, no HTTP, no database. The fastest possible path from
user intent to kernel syscall.

---

## API Layer

### Authentication

Simple API key in `X-API-Key` header. For WebSocket connections (which
cannot set custom headers from browser JavaScript), the key is passed
as a `?api_key=` query parameter.

Key comparison uses `hmac.compare_digest()` to prevent timing attacks.

### Error Mapping

| Engine Exception | HTTP Status | When |
|---|---|---|
| `ContainerNotFoundError` | 404 | Container ID doesn't exist |
| `ContainerAlreadyRunningError` | 409 | Starting a running container |
| `ContainerAlreadyStoppedError` | 409 | Stopping a stopped container |
| `ContainerLimitReachedError` | 429 | Max containers exceeded |
| `PyCrateError` (other) | 400 | General engine failure |

### WebSocket Protocol

**Log stream** (`/ws/logs/{id}`):
```json
{"type": "log", "container_id": "...", "line": "...", "timestamp": "..."}
{"type": "status", "container_id": "...", "status": "stopped", "message": "..."}
{"type": "error", "message": "..."}
```

**Metrics stream** (`/ws/metrics`):
```json
{
    "type": "metrics",
    "timestamp": "...",
    "containers": [
        {
            "container_id": "...",
            "cpu": {"usage_percent": 12.5},
            "memory": {"usage_bytes": 1234, "usage_percent": 23.4},
            "oom_killed": false
        }
    ]
}
```

---

## Data Model (MongoDB)

Used only by the API layer (not the CLI).

### containers collection

```json
{
    "container_id": "crate-a7f3b2",
    "name": "my-alpine",
    "status": "running",
    "image": "alpine:3.20",
    "config": { ... },
    "pid": 12345,
    "network": { "ip_address": "10.0.0.42" },
    "created_at": "2026-04-16T14:00:00Z",
    "started_at": "2026-04-16T14:00:05Z"
}
```

### events collection

```json
{
    "container_id": "crate-a7f3b2",
    "event_type": "container.started",
    "message": "Container started",
    "timestamp": "2026-04-16T14:00:05Z"
}
```

Events are auto-deleted after 7 days via a TTL index.

---

## Threading Model

PyCrate uses threading for concurrent operations:

| Thread | Purpose | Lifetime |
|---|---|---|
| Main thread | CLI command execution / API request handling | Process lifetime |
| Monitor thread (per container) | `waitpid()` blocking call, detects container exit | Container lifetime |
| Metrics thread | Periodic cgroup reads for resource usage | While container running |
| Dashboard browser thread | Opens browser after server starts | One-shot |

The `ContainerManager` uses a `threading.Lock` to protect concurrent access
to the container registry. Container state transitions are atomic.
