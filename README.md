# PyCrate

**A container runtime written from scratch in Python.**

PyCrate implements process isolation using Linux kernel primitives — namespaces, cgroups v2, and pivot_root — called directly from Python via `ctypes`. It includes a FastAPI-based daemon for managing containers over HTTP and WebSockets, and a Next.js dashboard for real-time monitoring.

This is not a wrapper around Docker. It implements the same low-level mechanisms that Docker and runc use under the hood.

---

## What It Does

| Capability | Implementation |
|---|---|
| Process isolation | Linux namespaces (PID, NET, MNT, UTS, USER) via `clone()` syscall |
| Resource limits | cgroups v2 — CPU throttling and memory caps with OOM detection |
| Filesystem isolation | Alpine Linux rootfs with `pivot_root` — each container has its own `/` |
| Networking | Virtual ethernet pairs (`veth`) with a host bridge — containers get their own IP |
| Management API | FastAPI daemon — create, start, stop, inspect containers over REST |
| Real-time monitoring | WebSocket streams for live logs and CPU/memory metrics |
| Web dashboard | Next.js UI with live resource graphs and container management |

---

## Architecture

```
                           Vercel (Free Tier)
  +--------------------------------------------------------+
  |              Next.js 15 Dashboard                       |
  |         Tailwind CSS  /  Framer Motion  /  Recharts     |
  +-------------------+------------------+-----------------+
                      | REST API         | WebSocket
                      v                  v
  +--------------------------------------------------------+
  |                  EC2 t2.micro (Linux)                   |
  |  +--------------------------------------------------+  |
  |  |              FastAPI Daemon (:8000)                |  |
  |  |         REST endpoints / WebSocket streams        |  |
  |  |            API key authentication                 |  |
  |  +----------------------+---------------------------+  |
  |                         |                              |
  |  +----------------------v---------------------------+  |
  |  |                PyCrate Engine                     |  |
  |  |  +----------+ +--------+ +---------+ +---------+ |  |
  |  |  |Namespaces| |cgroups | | rootfs  | |  network| |  |
  |  |  | clone()  | |  v2    | |pivot_root| |veth+br | |  |
  |  |  +----------+ +--------+ +---------+ +---------+ |  |
  |  +--------------------------------------------------+  |
  +--------------------------------------------------------+
                      |
                      v
  +--------------------------------------------------------+
  |            MongoDB Atlas (M0 Free Tier)                 |
  |       Container metadata / Event logs / Metrics         |
  +--------------------------------------------------------+
```

### Data Flow

1. User interacts with the Next.js dashboard hosted on Vercel.
2. Dashboard sends REST requests to the FastAPI daemon running on EC2.
3. FastAPI invokes the PyCrate engine to manage container processes via Linux syscalls.
4. The engine creates isolated processes using namespaces, applies cgroup limits, and sets up rootfs.
5. Resource metrics are read from the cgroup filesystem and streamed to the dashboard via WebSocket.
6. Container logs are captured from process stdout/stderr and streamed via WebSocket.
7. Container state and events are persisted to MongoDB Atlas.

---

## Why Python?

Go is the standard language for container runtimes — Docker, containerd, and runc are all written in Go. Building one in Python is a deliberate choice. The `ctypes` approach makes every syscall explicit: you see the exact flags passed to `clone()`, the exact bytes written to cgroup files, the exact sequence of `mount` and `pivot_root` calls. There is no standard library abstracting this away. Every line of isolation code maps directly to a kernel operation.

---

## How It Works

### Namespaces — Process Isolation

```python
# clone() with namespace flags — the same syscall Docker/runc uses
CLONE_NEWPID = 0x20000000   # New PID namespace  — container sees itself as PID 1
CLONE_NEWNS  = 0x00020000   # New mount namespace — container gets its own filesystem
CLONE_NEWUTS = 0x04000000   # New UTS namespace   — container gets its own hostname
CLONE_NEWNET = 0x40000000   # New network namespace — isolated network stack

libc = ctypes.CDLL("libc.so.6", use_errno=True)
child_pid = libc.clone(child_fn, stack_top, flags)
```

### cgroups v2 — Resource Limits

```python
# Writing directly to the kernel's cgroup filesystem
cgroup_path = Path(f"/sys/fs/cgroup/pycrate/{container_id}")
cgroup_path.mkdir(parents=True, exist_ok=True)

# Limit CPU to 50% of one core (50000us quota per 100000us period)
(cgroup_path / "cpu.max").write_text("50000 100000")

# Limit memory to 64MB
(cgroup_path / "memory.max").write_text("67108864")

# Assign the container process to this cgroup
(cgroup_path / "cgroup.procs").write_text(str(container_pid))
```

### pivot_root — Filesystem Isolation

```python
# Give the container its own root filesystem (Alpine Linux)
libc.pivot_root(new_root.encode(), old_root.encode())
os.chdir("/")
libc.umount2(old_root.encode(), MNT_DETACH)
```

---

## Tech Stack

### Backend (runs on EC2)

| Technology | Role |
|---|---|
| Python 3.11+ | Core engine — `ctypes` for Linux syscall access |
| FastAPI | REST API and WebSocket server |
| Motor | Async MongoDB driver |
| Pydantic | Request/response validation and settings |
| uvicorn | ASGI server |

### Frontend (deployed to Vercel)

| Technology | Role |
|---|---|
| Next.js 15 | React framework, App Router |
| Tailwind CSS v4 | Styling |
| Framer Motion | Animations |
| Recharts | Real-time CPU/memory charts |
| TypeScript | Type safety |

### Infrastructure

| Service | Role | Cost |
|---|---|---|
| AWS EC2 t2.micro | Hosts engine and API | Free tier (12 months) |
| Vercel | Hosts dashboard | Free (hobby plan) |
| MongoDB Atlas M0 | Database | Free (512MB) |
| GitHub Actions | CI/CD | Free |

---

## Project Structure

```
pycrate/
├── engine/                         # Container engine (Python + Linux syscalls)
│   ├── syscalls.py                 # ctypes bindings: clone, unshare, mount, pivot_root
│   ├── namespaces.py               # Namespace creation and management
│   ├── cgroups.py                  # cgroup v2 controller (CPU + memory)
│   ├── rootfs.py                   # Alpine rootfs extraction and pivot_root setup
│   ├── networking.py               # veth pairs + bridge networking
│   ├── container.py                # Container lifecycle (create/start/stop/destroy)
│   ├── metrics.py                  # Resource usage from cgroup filesystem
│   ├── config.py                   # ContainerConfig dataclass
│   └── exceptions.py               # Exception hierarchy
│
├── api/                            # FastAPI daemon
│   ├── main.py                     # App factory, CORS, lifespan
│   ├── config.py                   # Settings via pydantic-settings
│   ├── database.py                 # Motor async MongoDB client
│   ├── models.py                   # MongoDB document models
│   ├── schemas.py                  # Pydantic request/response schemas
│   ├── dependencies.py             # Auth middleware, engine injection
│   └── routes/
│       ├── containers.py           # Container CRUD endpoints
│       ├── system.py               # System info and health check
│       └── websockets.py           # Live log and metrics streaming
│
├── dashboard/                      # Next.js 15 web UI
│   ├── app/                        # App Router pages
│   ├── components/                 # UI components
│   ├── hooks/                      # WebSocket and data fetching hooks
│   └── lib/                        # API client, constants
│
├── infrastructure/                 # Deployment configs
│   ├── pycrate.service             # systemd unit file
│   ├── setup-ec2.sh                # EC2 bootstrap script
│   └── nginx.conf                  # Reverse proxy config
│
├── tests/                          # Test suite
├── docs/                           # Documentation
├── .github/workflows/deploy.yml    # CI/CD pipeline
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## API Reference

Full interactive docs available at `http://YOUR_EC2_IP:8000/docs` when the daemon is running.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/containers` | Create a new container |
| `GET` | `/api/containers` | List all containers |
| `GET` | `/api/containers/{id}` | Inspect container details |
| `POST` | `/api/containers/{id}/start` | Start a stopped container |
| `POST` | `/api/containers/{id}/stop` | Stop a running container |
| `DELETE` | `/api/containers/{id}` | Remove a container |
| `GET` | `/api/containers/{id}/logs` | Retrieve container logs |
| `WS` | `/ws/logs/{id}` | Stream live container logs |
| `WS` | `/ws/metrics` | Stream live resource metrics |
| `GET` | `/api/system/info` | Engine and host information |
| `GET` | `/api/health` | Health check |

---

## Quick Start

### Prerequisites

- Linux machine with cgroups v2 enabled (Ubuntu 22.04+ recommended)
- Python 3.11+
- Node.js 18+ (for dashboard development)
- MongoDB Atlas account (free M0 cluster)

### 1. Clone and configure

```bash
git clone https://github.com/Somshubhro07/pycrate.git
cd pycrate

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your MongoDB URI and API key
```

### 2. Start the daemon

```bash
# Requires root for namespace and cgroup operations
sudo $(which uvicorn) api.main:app --host 0.0.0.0 --port 8000
```

### 3. Create a container

```bash
curl -X POST http://localhost:8000/api/containers \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "name": "test",
    "command": ["/bin/sh"],
    "cpu_limit_percent": 50,
    "memory_limit_mb": 64
  }'
```

### 4. Run the dashboard

```bash
cd dashboard
npm install
npm run dev
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

Built by [Somshubhro Guha](https://github.com/Somshubhro07)
