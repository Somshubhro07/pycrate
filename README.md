# PyCrate

**A container runtime built from scratch in Python using Linux kernel primitives.**

PyCrate implements container isolation the same way Docker and runc do under the hood — namespaces, cgroups v2, OverlayFS, pivot_root, seccomp BPF, and capability dropping — called directly from Python via `ctypes`. No wrappers. No shelling out. Every syscall is explicit.

It includes a full CLI, single-node compose orchestration, multi-node cluster scheduling, and a transparent WSL2 backend for running on Windows.

---

## Quick Start

### On Linux

```bash
git clone https://github.com/Somshubhro07/pycrate.git
cd pycrate
pip install -e .

sudo pycrate pull alpine
sudo pycrate run alpine -- /bin/sh -c "echo Hello from PyCrate"
sudo pycrate ps
```

### On Windows (WSL2)

PyCrate includes a Machine backend that automatically provisions a lightweight Alpine Linux VM inside WSL2. No manual Linux setup required.

```bash
git clone https://github.com/Somshubhro07/pycrate.git
cd pycrate
pip install -e .

pycrate machine init      # Downloads Alpine rootfs, creates WSL2 distro
pycrate machine start     # Boots the VM
pycrate machine ssh        # Drops you into the Linux shell
```

Once inside the VM:

```bash
pycrate run alpine -- /bin/sh -c "echo Hello from PyCrate"
pycrate run alpine --detach --name demo-app -- /bin/sh -c "sleep 3600"
pycrate ps
```

Output:

```
 CONTAINER ID   NAME       IMAGE    STATUS    CPU   MEMORY   PID
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 crate-20868c   demo-app   alpine   running   50%     64MB    28
```

---

## What It Does

| Capability | Implementation |
|---|---|
| Process isolation | Linux namespaces (PID, NET, MNT, UTS, IPC) via `clone()` syscall |
| Resource limits | cgroups v2 — CPU throttling and memory caps with OOM detection |
| Filesystem isolation | OverlayFS copy-on-write layers with `pivot_root` |
| Security hardening | Seccomp BPF syscall filtering + capability bounding set |
| Networking | Virtual ethernet pairs (`veth`) with a host bridge |
| Volume mounts | Bind mounts for local development (`-v host:container`) |
| Multi-image support | Alpine (HTTP tarball), Ubuntu and Debian (debootstrap) |
| Windows support | Transparent WSL2 Machine backend with auto-provisioning |
| CLI | `pycrate run / ps / stop / rm / pull / images / up / down / deploy` |
| Single-node orchestration | Compose engine with health checks and restart policies |
| Multi-node cluster | Master/agent architecture with resource-aware scheduling |
| Management API | FastAPI daemon — REST + WebSocket for live metrics |

---

## How It Works

PyCrate creates isolated processes by calling Linux kernel syscalls directly from Python through `ctypes`. Here is what happens when you run `pycrate run alpine`:

1. **Image pull** — Downloads an Alpine Linux root filesystem tarball and extracts it to `/var/lib/pycrate/images/`.
2. **OverlayFS mount** — Layers a writable directory on top of the read-only base image. The container sees a unified filesystem, but writes only go to its own layer.
3. **cgroup creation** — Creates a cgroup v2 directory under `/sys/fs/cgroup/unified/pycrate/` and writes CPU and memory limits.
4. **`clone()` with namespace flags** — Forks a child process with `CLONE_NEWPID | CLONE_NEWNS | CLONE_NEWUTS | CLONE_NEWNET | CLONE_NEWIPC`. The child enters entirely new namespaces.
5. **Mount isolation** — Inside the child, makes the entire mount tree private (`MS_PRIVATE | MS_REC`) to prevent propagation back to the host. Mounts `/proc`, `/sys`, and `/dev`.
6. **`pivot_root`** — Swaps the root filesystem to the container's OverlayFS merged directory. The host filesystem becomes inaccessible.
7. **Security hardening** — Drops dangerous Linux capabilities and installs a seccomp BPF filter that blocks 22 syscalls.
8. **`execvp`** — Replaces the child process with the user's command.

The parent process assigns the child to its cgroup, sets up a veth network pair, and monitors the child via `waitpid()` in a background thread.

---

## Architecture

```
pycrate/
├── engine/                  Container runtime (pure Python, Linux syscalls)
│   ├── container.py         Container lifecycle: create → start → stop → destroy
│   ├── syscalls.py          ctypes bindings: clone(), mount(), pivot_root(), umount2()
│   ├── namespaces.py        Namespace management (PID, MNT, UTS, NET, IPC)
│   ├── cgroups.py           cgroups v2 resource controller (CPU, memory, OOM)
│   ├── overlay.py           OverlayFS copy-on-write storage driver
│   ├── rootfs.py            Filesystem setup, mount isolation, pivot_root
│   ├── security.py          Seccomp BPF filter + capability dropping
│   ├── networking.py        veth pairs, bridge creation, IP assignment
│   ├── images.py            Multi-image registry and pull (Alpine, Ubuntu, Debian)
│   ├── metrics.py           Real-time CPU/memory collection from cgroup files
│   └── volumes.py           Bind mount support
│
├── machine/                 WSL2 Machine backend (Windows support)
│   ├── wsl.py               WSL2 distro provisioning and lifecycle
│   ├── image.py             Alpine rootfs download and VM bootstrap
│   ├── config.py            Machine configuration and state
│   └── backend.py           Abstract machine backend interface
│
├── orchestrator/            Single-node orchestration
│   ├── compose.py           Compose engine with reconciliation loop
│   ├── manifest.py          pycrate.yml parser
│   └── health.py            Health check system (HTTP, TCP, exec)
│
├── cluster/                 Multi-node orchestration
│   ├── master.py            Control plane API (FastAPI)
│   ├── agent.py             Worker node daemon
│   ├── scheduler.py         Resource-aware spread scheduler
│   ├── reconciler.py        Desired-state convergence engine
│   └── deploy.py            Rolling update manager
│
├── cli/                     Command-line interface (Typer + Rich)
│   ├── main.py              Entry point
│   ├── output.py            Terminal formatting
│   └── commands/            Subcommand modules
│
├── api/                     REST + WebSocket API (FastAPI)
└── dashboard/               Web UI (Next.js 15)
```

### Engine Call Graph

```
pycrate run alpine /bin/sh
    │
    ├── images.py        → pull_image() downloads Alpine rootfs
    ├── overlay.py       → setup_overlay() creates OverlayFS layers
    ├── cgroups.py       → CgroupController.create() writes cpu.max, memory.max
    ├── syscalls.py      → clone(child_fn, CLONE_NEWPID | CLONE_NEWNS | ...)
    │                         │
    │                    [child process]
    │                         ├── rootfs.py      → mount("none", "/", MS_PRIVATE | MS_REC)
    │                         ├── rootfs.py      → setup_mounts() mounts /proc, /sys, /dev
    │                         ├── rootfs.py      → do_pivot_root() swaps root filesystem
    │                         ├── security.py    → harden_container() drops caps + seccomp
    │                         └── os.execvp()    → replaces process with /bin/sh
    │
    ├── cgroups.py       → assign(child_pid) moves child into cgroup
    ├── networking.py    → create_veth_pair() sets up container networking
    └── container.py     → _monitor_process() waitpid() in background thread
```

---

## Security Model

Four defense layers applied to every container:

| Layer | Mechanism | What It Prevents |
|---|---|---|
| Namespaces | `clone()` with 5 namespace flags | Container cannot see host processes, files, or network stack |
| cgroups v2 | `cpu.max` and `memory.max` | Resource exhaustion, fork bombs, runaway memory |
| Capabilities | `prctl(PR_CAPBSET_DROP)` | Loading kernel modules, rebooting, raw network I/O |
| Seccomp BPF | BPF filter via `prctl` | 22 dangerous syscalls blocked: `mount`, `ptrace`, `kexec_load`, `bpf`, etc. |

---

## CLI Reference

### Container Management

```bash
pycrate run <image> [command] [options]    # Create and start a container
  --name <name>                             # Assign a name
  --detach / -d                             # Run in background
  --cpu <percent>                           # CPU limit (default: 50%)
  --memory <mb>                             # Memory limit (default: 64MB)
  -v <host_path>:<container_path>           # Bind mount a volume
  -e <KEY=VALUE>                            # Set environment variable

pycrate ps                                  # List containers
pycrate stop <id|name>                      # Stop a container
pycrate rm <id|name>                        # Remove a container
pycrate logs <id|name>                      # View container logs
pycrate inspect <id|name>                   # Detailed container info
```

### Image Management

```bash
pycrate pull <image>                        # Pull a base image
pycrate images                              # List cached images
```

### Machine (Windows/WSL2)

```bash
pycrate machine init                        # Create a PyCrate VM
pycrate machine start                       # Boot the VM
pycrate machine stop                        # Shut down the VM
pycrate machine ssh                         # Shell into the VM
pycrate machine status                      # VM info and state
```

### Single-Node Compose

```bash
pycrate up                                  # Start services from pycrate.yml
pycrate down                                # Stop all services
pycrate compose status                      # Service health
pycrate compose scale <svc> --replicas=N    # Scale a service
```

### Multi-Node Cluster

```bash
pycrate cluster init                        # Start a master node
pycrate cluster join <master-url>           # Join as a worker
pycrate cluster nodes                       # List cluster nodes
pycrate cluster status                      # Full cluster state

pycrate deploy create <svc> --image alpine --replicas 3
pycrate deploy scale <svc> --replicas 5
pycrate deploy ls
pycrate deploy rm <svc>
```

---

## Supported Images

| Image | Versions | Pull Method | Size |
|---|---|---|---|
| Alpine | 3.19, 3.20 | HTTP tarball | ~3 MB |
| Ubuntu | 22.04, 24.04 | debootstrap | ~150 MB |
| Debian | bookworm, bullseye | debootstrap | ~130 MB |

---

## Requirements

- **Linux** — Ubuntu 22.04+ recommended, any kernel with cgroups v2 and OverlayFS
- **Windows** — Windows 10/11 with WSL2 (PyCrate handles the rest)
- **Python** — 3.11+
- **Root privileges** — Required for namespace and cgroup operations
- **Optional** — `debootstrap` for Ubuntu/Debian images, `iptables` for networking

---

## Development

```bash
git clone https://github.com/Somshubhro07/pycrate.git
cd pycrate
python -m venv .venv && source .venv/bin/activate
pip install -e ".[cluster,server,dev]"

# Run tests
pytest

# Lint
ruff check .
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| 1. Container Engine | Done | Namespaces, cgroups v2, OverlayFS, pivot_root, networking |
| 2. Production Hardening | Done | Multi-image, seccomp BPF, capability dropping, CLI |
| 3. Single-Node Orchestration | Done | Compose manifests, health checks, restart policies |
| 4. Multi-Node Orchestration | Done | Master/agent, resource-aware scheduling, reconciliation |
| 5. Distribution | Done | WSL2 Machine backend, port forwarding, PyPI packaging |

---

## License

MIT
