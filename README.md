# PyCrate

**A container runtime and orchestrator built from scratch in Python.**

PyCrate implements container isolation using Linux kernel primitives -- namespaces, cgroups v2, pivot_root, OverlayFS, seccomp BPF, and capability dropping -- called directly from Python via `ctypes`. No wrappers. No shelling out. Every syscall is explicit.

It includes a CLI tool (`pycrate`), a FastAPI daemon for HTTP/WebSocket management, and a Next.js dashboard for real-time monitoring.

This is not a wrapper around Docker. It implements the same low-level mechanisms that Docker and runc use under the hood.

---

## What It Does

| Capability | Implementation |
|---|---|
| Process isolation | Linux namespaces (PID, NET, MNT, UTS, IPC) via `clone()` syscall |
| Resource limits | cgroups v2 -- CPU throttling and memory caps with OOM detection |
| Filesystem isolation | OverlayFS copy-on-write layers with `pivot_root` |
| Multi-image support | Alpine (HTTP tarball), Ubuntu and Debian (debootstrap) |
| Security hardening | Seccomp BPF syscall filtering + capability bounding set |
| Networking | Virtual ethernet pairs (`veth`) with a host bridge |
| CLI | `pycrate run/ps/stop/rm/pull/images/dashboard` |
| Management API | FastAPI daemon -- REST + WebSocket for live metrics |
| Web dashboard | Next.js UI with live resource graphs |

---

## Quick Start

```bash
# Install (Linux / WSL2)
curl -sSL https://raw.githubusercontent.com/Somshubhro07/pycrate/main/install.sh | sudo bash

# Pull an image
sudo pycrate pull alpine

# Run a container
sudo pycrate run alpine /bin/sh --name test --cpu 50 --memory 64

# List containers
sudo pycrate ps

# Stop and remove
sudo pycrate stop test
sudo pycrate rm test
```

### WSL2 (Windows)

```bash
# Install WSL2 Ubuntu
wsl --install -d Ubuntu-22.04

# Inside WSL2
cd /mnt/c/Users/HP/Desktop/Code\ stuff/container\ runtime/pycrate
bash scripts/setup-wsl.sh
```

---

## Architecture

```
User
  |
  +-- CLI (pycrate run/ps/stop/...)
  |       |
  |       +-- Engine (direct Python calls, no network)
  |
  +-- Dashboard (Next.js :3000)
          |
          +-- API (FastAPI :8000)
                  |
                  +-- Engine
```

### Engine Internals

```
container.py          Container lifecycle (create/start/stop/destroy)
    |
    +-- syscalls.py   ctypes bindings: clone(), pivot_root(), mount()
    +-- namespaces.py Namespace management (PID, MNT, UTS, NET, IPC)
    +-- cgroups.py    cgroups v2 resource limits (CPU, memory)
    +-- images.py     Multi-image registry and pull (Alpine, Ubuntu, Debian)
    +-- overlay.py    OverlayFS copy-on-write storage driver
    +-- rootfs.py     Filesystem setup and pivot_root
    +-- security.py   Seccomp BPF filter + capability dropping
    +-- networking.py veth pairs, bridge, NAT
    +-- metrics.py    Real-time CPU/memory collection from cgroups
```

---

## Security Model

4 defense layers applied per container:

| Layer | Mechanism | What It Prevents |
|---|---|---|
| Namespaces | `clone()` flags | Container can't see host processes, files, network |
| cgroups v2 | `cpu.max`, `memory.max` | Resource exhaustion, fork bombs |
| Capabilities | `prctl(PR_CAPBSET_DROP)` | Loading kernel modules, rebooting, raw I/O |
| Seccomp | BPF filter via `prctl` | 22 dangerous syscalls blocked (mount, ptrace, kexec, bpf) |

See [docs/SECURITY.md](docs/SECURITY.md) for the complete list.

---

## CLI Reference

```bash
pycrate run <image> [command] [options]  # Create and start a container
pycrate ps                               # List containers
pycrate stop <id|name>                   # Stop a container
pycrate rm <id|name>                     # Remove a container
pycrate logs <id|name>                   # View logs
pycrate inspect <id|name>               # Detailed info

pycrate pull <image>                     # Pull a base image
pycrate images                           # List cached images
pycrate rmi <image:version>              # Remove cached image

pycrate dashboard                        # Launch web dashboard
pycrate version                          # Show version info
```

See [docs/CLI.md](docs/CLI.md) for full reference.

---

## Supported Images

| Image | Versions | Pull Method | Size |
|---|---|---|---|
| Alpine | 3.19, 3.20 | HTTP tarball | ~3MB |
| Ubuntu | 22.04, 24.04 | debootstrap | ~150MB |
| Debian | bookworm, bullseye | debootstrap | ~130MB |

---

## Project Structure

```
pycrate/
    engine/               Container runtime (pure Python, Linux syscalls)
        __init__.py       Public API
        container.py      Container lifecycle
        config.py         Immutable container configuration
        syscalls.py       ctypes bindings (clone, mount, pivot_root)
        namespaces.py     Namespace management
        cgroups.py        cgroups v2 controller
        images.py         Multi-image registry and pull
        overlay.py        OverlayFS storage driver
        rootfs.py         Root filesystem setup
        security.py       Seccomp BPF + capabilities
        networking.py     Container networking
        metrics.py        Resource metrics collection
        exceptions.py     Exception hierarchy

    cli/                  Command-line interface (Typer)
        main.py           Entry point
        output.py         Rich terminal formatting
        commands/
            run.py        pycrate run
            containers.py pycrate ps/stop/rm/logs/inspect
            images.py     pycrate pull/images/rmi
            dashboard.py  pycrate dashboard

    api/                  REST + WebSocket API (FastAPI)
        main.py           FastAPI application
        routes/           HTTP endpoints
        schemas.py        Pydantic models

    dashboard/            Web UI (Next.js 15)
        src/
            components/   React components
            pages/        Dashboard pages

    docs/                 Documentation
        ARCHITECTURE.md   System design
        SECURITY.md       Security model
        CLI.md            CLI reference
        ROADMAP.md        Development roadmap
        DEPLOYMENT.md     Deployment guide

    scripts/
        setup-wsl.sh      WSL2 development setup

    install.sh            One-line install script
    pyproject.toml        Package configuration
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| 1. Container Engine | Done | Namespaces, cgroups, rootfs, networking |
| 2. Production Hardening | Done | Multi-image, OverlayFS, seccomp, CLI |
| 3. Single-Node Orchestration | Planned | Compose manifests, health checks, restart policies |
| 4. Multi-Node Orchestration | Planned | Master/agent, scheduling, rolling deployments |

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan.

---

## Requirements

- Linux (Ubuntu 22.04+ recommended, WSL2 supported)
- Python 3.11+
- Root privileges

---

## License

MIT
