# PyCrate Roadmap

PyCrate is a container runtime and orchestrator built from scratch in Python.
This document outlines the full development roadmap from single-container
isolation to multi-node orchestration.

---

## Vision

A Python-native container runtime that implements the same kernel primitives
as Docker (namespaces, cgroups, pivot_root) and the same orchestration concepts
as Kubernetes (scheduling, reconciliation, health checks), in a codebase that
is readable, extensible, and educational.

Not a toy. Not a wrapper. A ground-up implementation using Linux syscalls
called directly via ctypes.

---

## Phase 1: Container Engine (Complete)

The foundation. Single-container isolation using Linux kernel primitives.

| Feature | Status | Module |
|---|---|---|
| Process isolation via namespaces | Done | `engine/namespaces.py`, `engine/syscalls.py` |
| PID, MNT, UTS, NET, IPC namespaces | Done | `engine/syscalls.py` |
| cgroups v2 resource limits (CPU + memory) | Done | `engine/cgroups.py` |
| OOM kill detection | Done | `engine/cgroups.py` |
| Alpine rootfs with pivot_root | Done | `engine/rootfs.py` |
| Container networking (veth + bridge) | Done | `engine/networking.py` |
| NAT via iptables | Done | `engine/networking.py` |
| Real-time metrics collection | Done | `engine/metrics.py` |
| Container lifecycle (create/start/stop/destroy) | Done | `engine/container.py` |
| Thread-safe state management | Done | `engine/container.py` |
| FastAPI REST + WebSocket API | Done | `api/` |
| Next.js monitoring dashboard | Done | `dashboard/` |

---

## Phase 2: Production Hardening (Current)

Making the runtime genuinely usable and secure.

| Feature | Status | Module |
|---|---|---|
| Multi-image support (Alpine, Ubuntu, Debian) | Done | `engine/images.py` |
| OverlayFS copy-on-write storage | Done | `engine/overlay.py` |
| Seccomp BPF syscall filtering | Done | `engine/security.py` |
| Linux capability bounding set | Done | `engine/security.py` |
| CLI tool (`pycrate` command) | Done | `cli/` |
| Dashboard auto-launch | Done | `cli/commands/dashboard.py` |
| Install script | Done | `install.sh` |
| WSL2 development setup | Done | `scripts/setup-wsl.sh` |

---

## Phase 3: Single-Node Orchestration (Next)

Multi-container applications on a single host. The "Docker Compose" equivalent.

| Feature | Status | Module |
|---|---|---|
| `pycrate.yml` manifest format | Planned | `orchestrator/manifest.py` |
| `pycrate up` / `pycrate down` | Planned | `cli/commands/compose.py` |
| Service dependency ordering | Planned | `orchestrator/scheduler.py` |
| Container restart policies (always, on-failure, never) | Planned | `engine/container.py` |
| Port forwarding (`-p 8080:80`) | Planned | `engine/networking.py` |
| Inter-container DNS resolution | Planned | `orchestrator/dns.py` |
| Health checks (HTTP, TCP, exec) | Planned | `orchestrator/health.py` |
| `pycrate exec` (enter running container) | Planned | `cli/commands/exec.py` |

### Manifest Format (Draft)

```yaml
# pycrate.yml
version: 1

services:
  web:
    image: alpine:3.20
    command: ["python3", "-m", "http.server", "8080"]
    cpu: 25
    memory: 128
    ports:
      - "8080:8080"
    restart: always
    health_check:
      http: "http://localhost:8080/health"
      interval: 10
      retries: 3

  worker:
    image: ubuntu:22.04
    command: ["/app/worker.sh"]
    cpu: 50
    memory: 256
    depends_on:
      - web
    restart: on-failure
    env:
      QUEUE_URL: "redis://redis:6379"
```

---

## Phase 4: Multi-Node Orchestration (Future)

Distributed container scheduling across multiple Linux machines.
The core concepts of Kubernetes, implemented transparently in Python.

| Feature | Status | Module |
|---|---|---|
| Master node (control plane) | Planned | `cluster/master.py` |
| Worker agent | Planned | `cluster/agent.py` |
| Node heartbeat and health monitoring | Planned | `cluster/heartbeat.py` |
| Resource-aware scheduling | Planned | `cluster/scheduler.py` |
| Desired state reconciliation loop | Planned | `cluster/reconciler.py` |
| Replica management | Planned | `cluster/replicas.py` |
| Rolling deployments | Planned | `cluster/deploy.py` |
| Cross-node networking (WireGuard tunnels) | Planned | `cluster/network.py` |
| Service discovery (DNS) | Planned | `cluster/dns.py` |
| Container rescheduling on node failure | Planned | `cluster/reconciler.py` |
| State store (SQLite on master) | Planned | `cluster/state.py` |
| Cluster CLI (`pycrate cluster ...`) | Planned | `cli/commands/cluster.py` |

### Architecture (Draft)

```
                    pycrate master (Node 1)
                    +------------------------------+
                    |  Scheduler                   |
                    |  Reconciliation Loop (5s)    |
                    |  State Store (SQLite)        |
                    |  API Server (:8000)          |
                    |  Health Monitor              |
                    +----------+---+---------------+
                               |   |
                  +------------+   +------------+
                  |                              |
    pycrate agent (Node 2)          pycrate agent (Node 3)
    +-------------------------+     +-------------------------+
    |  Container Engine       |     |  Container Engine       |
    |  Local cgroups + ns     |     |  Local cgroups + ns     |
    |  Agent API (:8001)      |<--->|  Agent API (:8001)      |
    |  Heartbeat (5s)         | WG  |  Heartbeat (5s)         |
    +-------------------------+     +-------------------------+
```

### Reconciliation Loop (the core algorithm)

The master runs this every 5 seconds:

```
1. Load desired state from manifest
2. Collect actual state from all agents
3. For each service:
   a. Count running replicas
   b. If running < desired: schedule new containers on least-loaded node
   c. If running > desired: stop excess containers
4. For each node:
   a. Check last heartbeat timestamp
   b. If stale (>30s): mark node unhealthy
   c. Reschedule its containers to healthy nodes
5. Persist state to SQLite
```

### Scheduling Algorithm

Simple resource-aware bin-packing:

```
1. Filter nodes: only consider healthy nodes with enough free CPU + memory
2. Score nodes: prefer nodes with the most free resources (spread strategy)
3. Select: pick the highest-scoring node
4. Place: send container creation request to that node's agent
```

---

## Phase 5: Additional Features (Backlog)

These are features that would further differentiate PyCrate but are
lower priority than the orchestration work.

| Feature | Difficulty | Impact |
|---|---|---|
| `pycrate build` (Dockerfile-like build system) | Medium | High |
| Volume mounts (persistent storage) | Easy | Medium |
| Container-to-container linking | Medium | Medium |
| Template images (snapshot a running container) | Medium | Medium |
| Resource usage history + analytics | Easy | Low |
| Plugin system for custom schedulers | Hard | Low |
| Web-based terminal (exec via browser) | Medium | Medium |
| ARM64 support | Easy | Medium |
| PyPI publishing (`pip install pycrate`) | Easy | High |

---

## Non-Goals

These are explicitly out of scope:

- **Windows/macOS native support**: Containers are a Linux kernel feature. Cross-platform support requires a VM layer, which is a separate product.
- **Docker Hub compatibility**: PyCrate uses its own image format (minirootfs tarballs and debootstrap). It does not pull from Docker registries.
- **Production Kubernetes replacement**: PyCrate's orchestration is educational and small-scale. It is not designed for production workloads at Kubernetes scale.
- **Full OCI compliance**: PyCrate does not implement the OCI runtime spec. It uses its own simpler container format.

---

## Contributing

See the individual module docstrings for implementation details.
Each file in `engine/` has extensive comments explaining the Linux kernel
concepts it implements.

Key files to start with:
- `engine/syscalls.py` -- All ctypes bindings to Linux syscalls
- `engine/container.py` -- Container lifecycle orchestration
- `engine/security.py` -- Seccomp BPF and capability management
- `docs/ARCHITECTURE.md` -- System design and data flow
