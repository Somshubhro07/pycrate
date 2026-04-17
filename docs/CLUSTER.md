# PyCrate Cluster Guide

Multi-node container orchestration across 2-10 machines.

---

## Architecture

PyCrate's cluster uses a master/agent model with HTTP polling:

```
Master Node                          Worker Nodes
+---------------------------+        +---------------------------+
| Master API (:9000)        |  <--   | Agent (polls every 5s)    |
| Reconciler (5s loop)      |        |   - Reports state         |
| Scheduler (spread)        |        |   - Executes assignments  |
| SQLite state store        |        |   - Runs containers       |
+---------------------------+        +---------------------------+
```

**Key design choices:**
- Agents initiate all connections (works through NAT/firewalls)
- SQLite for state (zero external dependencies)
- Resource-aware spread scheduling
- No overlay networking in v1 (port forwarding instead)

---

## Quick Start

### 1. Start a Master

```bash
# On the master machine (e.g., 10.0.1.1)
sudo pycrate cluster init --port 9000
```

The master starts a FastAPI server and begins the reconciliation loop.

### 2. Join Workers

```bash
# On each worker machine
sudo pycrate cluster join http://10.0.1.1:9000
```

Workers automatically detect their CPU/memory capacity and register with the master.

### 3. Deploy a Service

```bash
# From any machine that can reach the master
pycrate deploy create web \
    --image alpine:3.20 \
    --replicas 3 \
    --cpu 25 \
    --memory 128

# The scheduler distributes replicas across workers
```

### 4. Check Status

```bash
# Cluster overview
pycrate cluster status

# Node list
pycrate cluster nodes

# Deployments
pycrate deploy ls

# Recent events
pycrate deploy events
```

### 5. Scale

```bash
pycrate deploy scale web --replicas 5
```

The reconciler detects the deficit and schedules 2 more containers.

### 6. Rolling Update

```bash
pycrate deploy rollout web --image alpine:3.21
pycrate deploy rollout-status web
```

Zero-downtime: new containers start before old ones are stopped.

### 7. Tear Down

```bash
# Remove a deployment
pycrate deploy rm web

# View cleanup events
pycrate deploy events
```

---

## How It Works

### Reconciliation Loop

Every 5 seconds on the master:

1. **Check node health** — mark nodes as unhealthy if they miss heartbeats (>30s)
2. **Compare desired vs actual** — for each deployment, count running containers
3. **Schedule new containers** — if running < desired, pick a node and create an assignment
4. **Stop excess containers** — if running > desired, mark extras for removal
5. **Clean up orphans** — stop containers that don't belong to any deployment

### Scheduling Algorithm

Resource-aware spread:

1. **Filter** — only healthy nodes with enough free CPU + memory
2. **Score** — rank by available resources (most free = highest score)
3. **Penalty** — master nodes get -20 score, each running container costs -2
4. **Select** — highest scored node wins

This distributes load evenly, making node failures less impactful.

### Agent Lifecycle

```
Register → Heartbeat → Poll → Execute → Ack → Sleep(5s) → repeat
```

- **Register**: POST /api/v1/join with node ID and resource capacity
- **Heartbeat**: POST /api/v1/heartbeat with container state + resource usage
- **Poll**: GET /api/v1/assignments/{node_id} for pending work
- **Execute**: Create or stop containers using the local engine
- **Ack**: POST /api/v1/assignments/ack to mark work as done

If the master is unreachable, agents continue running existing containers
and retry with exponential backoff (5s → 10s → 20s → ... → 60s max).

---

## API Reference

All endpoints are on the master node (default port 9000).

### Nodes

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/join | Register a worker node |
| DELETE | /api/v1/nodes/{id} | Remove a node |
| GET | /api/v1/nodes | List all nodes |

### Heartbeat

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/heartbeat | Agent state report |

### Assignments

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/assignments/{node_id} | Get pending work |
| POST | /api/v1/assignments/ack | Acknowledge completed work |

### Deployments

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/deploy | Create/update deployment |
| GET | /api/v1/deployments | List all deployments |
| PUT | /api/v1/deploy/{name}/scale | Scale replicas |
| DELETE | /api/v1/deploy/{name} | Delete deployment |

### Rolling Updates

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/rollout/{name} | Start rolling update |
| GET | /api/v1/rollout/{name} | Check rollout status |

### Status

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/state | Full cluster summary |
| GET | /api/v1/events | Recent events |
| GET | /api/v1/capacity | Node capacity report |

---

## Limitations (v1)

- **No cross-node networking**: Containers on different nodes communicate via host IP + port forwarding, not by container IP.
- **No authentication**: Any machine that can reach the master can join and deploy. Add firewall rules to restrict access.
- **Single master**: If the master dies, agents keep running containers but no new scheduling happens. Restart the master to resume.
- **No persistent volumes**: Volumes are node-local. A rescheduled container won't have the same data.
- **SQLite concurrency**: Write throughput is limited to ~50 transactions/second. Sufficient for our target scale.

---

## Troubleshooting

### Agent can't reach master

```bash
# Check connectivity
curl http://10.0.1.1:9000/api/v1/state

# Check firewall
sudo ufw allow 9000/tcp
```

### Containers not starting

```bash
# Check events for scheduling failures
pycrate deploy events

# Check node capacity
curl http://localhost:9000/api/v1/capacity
```

### Node marked unhealthy

The master marks a node unhealthy after 30 seconds without a heartbeat.
Containers on that node are marked as "lost" and rescheduled.

```bash
# Check node status
pycrate cluster nodes

# Force re-register
sudo pycrate cluster join http://master:9000
```
