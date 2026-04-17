"""
Cluster State Store
=====================

SQLite-based persistent state for the cluster control plane.
Stores node registrations, deployment definitions, container assignments,
and event logs.

SQLite is used instead of MongoDB because:
    - Zero external dependencies (sqlite3 is in Python's stdlib)
    - The master must be self-contained (no DB server to install)
    - ACID transactions for consistent state updates
    - Single-file database, trivial to backup and restore

The state store is the single source of truth for the reconciler.
Agents report actual state -> reconciler compares with desired state
-> scheduler produces assignments -> agents poll for assignments.

Schema:
    nodes        — Registered cluster nodes (master + workers)
    deployments  — Desired state (services the user wants running)
    containers   — Actual state (what's really running, per agent report)
    assignments  — Pending work for agents (create/stop commands)
    events       — Audit log for debugging

Usage:
    store = ClusterState("/var/lib/pycrate/cluster.db")
    store.register_node("worker-1", "10.0.1.11:9001", ...)
    store.create_deployment(deployment)
    store.update_heartbeat("worker-1", containers, resources)
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("/var/lib/pycrate/cluster.db")

# Node health timeout: if no heartbeat for this many seconds, mark unhealthy
NODE_TIMEOUT_SECONDS = 30
# Events older than this are cleaned up
EVENT_RETENTION_SECONDS = 7 * 24 * 60 * 60  # 7 days


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class NodeInfo:
    """Registered cluster node."""
    node_id: str
    address: str
    role: str = "worker"           # "master" | "worker"
    status: str = "healthy"        # "healthy" | "unhealthy" | "offline"
    cpu_total: int = 100           # Total CPU capacity (% units, 100 per core)
    cpu_used: int = 0              # Currently allocated CPU
    memory_total: int = 1024       # Total memory MB
    memory_used: int = 0           # Currently allocated memory MB
    last_heartbeat: float = 0.0
    joined_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def cpu_available(self) -> int:
        return max(0, self.cpu_total - self.cpu_used)

    @property
    def memory_available(self) -> int:
        return max(0, self.memory_total - self.memory_used)

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"


@dataclass
class Deployment:
    """Desired state for a service across the cluster."""
    deployment_id: str
    service_name: str
    image: str
    command: list[str]
    replicas: int = 1
    cpu: int = 50
    memory: int = 64
    restart: str = "always"
    env: dict[str, str] = field(default_factory=dict)
    health_check: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class ContainerRecord:
    """Actual state of a container (reported by an agent)."""
    container_id: str
    node_id: str
    deployment_id: str
    service_name: str
    status: str                    # "running" | "stopped" | "error"
    pid: int | None = None
    cpu_used: int = 0
    memory_used: int = 0
    health: str = "none"
    started_at: float = 0.0
    reported_at: float = field(default_factory=time.time)


@dataclass
class Assignment:
    """Pending work for an agent to execute."""
    assignment_id: str
    node_id: str
    action: str                    # "create" | "stop"
    deployment_id: str = ""
    container_id: str = ""         # For stop actions
    service_name: str = ""
    image: str = ""
    command: list[str] = field(default_factory=list)
    cpu: int = 50
    memory: int = 64
    env: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    acknowledged: bool = False


# ---------------------------------------------------------------------------
# State store
# ---------------------------------------------------------------------------

class ClusterState:
    """Thread-safe SQLite state store for the cluster control plane.

    All public methods are thread-safe (each acquires a connection from
    the thread-local storage and runs inside a transaction).
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()  # Serialize all writes
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id         TEXT PRIMARY KEY,
                address         TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'worker',
                status          TEXT NOT NULL DEFAULT 'healthy',
                cpu_total       INTEGER DEFAULT 100,
                cpu_used        INTEGER DEFAULT 0,
                memory_total    INTEGER DEFAULT 1024,
                memory_used     INTEGER DEFAULT 0,
                last_heartbeat  REAL,
                joined_at       REAL,
                metadata        TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS deployments (
                deployment_id   TEXT PRIMARY KEY,
                service_name    TEXT NOT NULL UNIQUE,
                image           TEXT NOT NULL,
                command         TEXT NOT NULL,
                replicas        INTEGER DEFAULT 1,
                cpu             INTEGER DEFAULT 50,
                memory          INTEGER DEFAULT 64,
                restart         TEXT DEFAULT 'always',
                env             TEXT DEFAULT '{}',
                health_check    TEXT,
                created_at      REAL,
                updated_at      REAL
            );

            CREATE TABLE IF NOT EXISTS containers (
                container_id    TEXT PRIMARY KEY,
                node_id         TEXT NOT NULL,
                deployment_id   TEXT,
                service_name    TEXT NOT NULL,
                status          TEXT NOT NULL,
                pid             INTEGER,
                cpu_used        INTEGER DEFAULT 0,
                memory_used     INTEGER DEFAULT 0,
                health          TEXT DEFAULT 'none',
                started_at      REAL,
                reported_at     REAL,
                FOREIGN KEY (node_id) REFERENCES nodes(node_id)
            );

            CREATE TABLE IF NOT EXISTS assignments (
                assignment_id   TEXT PRIMARY KEY,
                node_id         TEXT NOT NULL,
                action          TEXT NOT NULL,
                deployment_id   TEXT DEFAULT '',
                container_id    TEXT DEFAULT '',
                service_name    TEXT DEFAULT '',
                image           TEXT DEFAULT '',
                command         TEXT DEFAULT '[]',
                cpu             INTEGER DEFAULT 50,
                memory          INTEGER DEFAULT 64,
                env             TEXT DEFAULT '{}',
                created_at      REAL,
                acknowledged    INTEGER DEFAULT 0,
                FOREIGN KEY (node_id) REFERENCES nodes(node_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       REAL NOT NULL,
                node_id         TEXT,
                container_id    TEXT,
                event_type      TEXT NOT NULL,
                message         TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_containers_node ON containers(node_id);
            CREATE INDEX IF NOT EXISTS idx_containers_deployment ON containers(deployment_id);
            CREATE INDEX IF NOT EXISTS idx_assignments_node ON assignments(node_id);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        """)
        conn.commit()
        logger.info("Cluster state store initialized at %s", self.db_path)

    # -- Node operations -------------------------------------------------------

    def register_node(
        self,
        node_id: str,
        address: str,
        role: str = "worker",
        cpu_total: int = 100,
        memory_total: int = 1024,
    ) -> NodeInfo:
        """Register a new node or update an existing one."""
        conn = self._get_conn()
        now = time.time()

        conn.execute("""
            INSERT INTO nodes (node_id, address, role, status, cpu_total, memory_total,
                              last_heartbeat, joined_at)
            VALUES (?, ?, ?, 'healthy', ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                address = excluded.address,
                status = 'healthy',
                cpu_total = excluded.cpu_total,
                memory_total = excluded.memory_total,
                last_heartbeat = excluded.last_heartbeat
        """, (node_id, address, role, cpu_total, memory_total, now, now))
        conn.commit()

        self.add_event(node_id=node_id, event_type="node.joined",
                       message=f"Node {node_id} joined as {role}")

        return self.get_node(node_id)

    def get_node(self, node_id: str) -> NodeInfo | None:
        """Get a node by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_all_nodes(self) -> list[NodeInfo]:
        """Get all registered nodes."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM nodes ORDER BY joined_at").fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_healthy_nodes(self) -> list[NodeInfo]:
        """Get all healthy nodes."""
        return [n for n in self.get_all_nodes() if n.is_healthy]

    def update_heartbeat(
        self,
        node_id: str,
        containers: list[dict],
        resources: dict[str, int],
    ) -> None:
        """Process a heartbeat from an agent.

        Atomic: wraps DELETE + INSERT in a single transaction so the
        reconciler never sees an empty container set mid-update.
        """
        conn = self._get_conn()
        now = time.time()

        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Update node status and resources
                conn.execute("""
                    UPDATE nodes SET
                        status = 'healthy',
                        last_heartbeat = ?,
                        cpu_used = ?,
                        memory_used = ?
                    WHERE node_id = ?
                """, (now, resources.get("cpu_used", 0),
                      resources.get("memory_used", 0), node_id))

                # Sync container state from this node
                # Atomic: delete then insert in the same transaction
                conn.execute(
                    "DELETE FROM containers WHERE node_id = ?", (node_id,)
                )

                # Insert fresh container records
                for c in containers:
                    conn.execute("""
                        INSERT OR REPLACE INTO containers
                            (container_id, node_id, deployment_id, service_name,
                             status, pid, cpu_used, memory_used, health,
                             started_at, reported_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        c.get("container_id", ""),
                        node_id,
                        c.get("deployment_id", ""),
                        c.get("name", c.get("service_name", "")),
                        c.get("status", "unknown"),
                        c.get("pid"),
                        c.get("config", {}).get("cpu_limit_percent", 0),
                        c.get("config", {}).get("memory_limit_mb", 0),
                        c.get("health", "none"),
                        c.get("started_at", 0),
                        now,
                    ))

                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def mark_node_unhealthy(self, node_id: str) -> None:
        """Mark a node as unhealthy (missed heartbeats)."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE nodes SET status = 'unhealthy' WHERE node_id = ?",
            (node_id,),
        )
        conn.commit()
        self.add_event(node_id=node_id, event_type="node.unhealthy",
                       message=f"Node {node_id} missed heartbeat")

    def remove_node(self, node_id: str) -> None:
        """Remove a node from the cluster."""
        conn = self._get_conn()
        conn.execute("DELETE FROM assignments WHERE node_id = ?", (node_id,))
        conn.execute("DELETE FROM containers WHERE node_id = ?", (node_id,))
        conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        conn.commit()

    def check_node_health(self) -> list[str]:
        """Check all nodes for missed heartbeats. Returns list of newly-unhealthy node IDs."""
        conn = self._get_conn()
        cutoff = time.time() - NODE_TIMEOUT_SECONDS
        rows = conn.execute("""
            SELECT node_id FROM nodes
            WHERE status = 'healthy' AND last_heartbeat < ?
        """, (cutoff,)).fetchall()

        unhealthy = []
        for row in rows:
            nid = row["node_id"]
            self.mark_node_unhealthy(nid)
            unhealthy.append(nid)

        return unhealthy

    # -- Deployment operations -------------------------------------------------

    def create_deployment(self, dep: Deployment) -> Deployment:
        """Create or update a deployment."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO deployments
                (deployment_id, service_name, image, command, replicas,
                 cpu, memory, restart, env, health_check, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                image = excluded.image,
                command = excluded.command,
                replicas = excluded.replicas,
                cpu = excluded.cpu,
                memory = excluded.memory,
                restart = excluded.restart,
                env = excluded.env,
                health_check = excluded.health_check,
                updated_at = excluded.updated_at
        """, (
            dep.deployment_id, dep.service_name, dep.image,
            json.dumps(dep.command), dep.replicas, dep.cpu, dep.memory,
            dep.restart, json.dumps(dep.env),
            json.dumps(dep.health_check) if dep.health_check else None,
            dep.created_at, dep.updated_at,
        ))
        conn.commit()

        self.add_event(event_type="deployment.created",
                       message=f"Deployment {dep.service_name} "
                               f"({dep.replicas} replicas of {dep.image})")
        return dep

    def get_deployment(self, service_name: str) -> Deployment | None:
        """Get a deployment by service name."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM deployments WHERE service_name = ?",
            (service_name,),
        ).fetchone()
        return self._row_to_deployment(row) if row else None

    def get_all_deployments(self) -> list[Deployment]:
        """Get all active deployments."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM deployments ORDER BY created_at"
        ).fetchall()
        return [self._row_to_deployment(r) for r in rows]

    def delete_deployment(self, service_name: str) -> None:
        """Delete a deployment (reconciler will stop containers)."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM deployments WHERE service_name = ?",
            (service_name,),
        )
        conn.commit()
        self.add_event(event_type="deployment.deleted",
                       message=f"Deployment {service_name} deleted")

    def update_replicas(self, service_name: str, replicas: int) -> None:
        """Update desired replica count for a deployment."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE deployments SET replicas = ?, updated_at = ?
            WHERE service_name = ?
        """, (replicas, time.time(), service_name))
        conn.commit()

    # -- Container operations --------------------------------------------------

    def get_containers_for_deployment(self, deployment_id: str) -> list[ContainerRecord]:
        """Get all containers for a specific deployment."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM containers WHERE deployment_id = ? AND status = 'running'",
            (deployment_id,),
        ).fetchall()
        return [self._row_to_container(r) for r in rows]

    def get_containers_for_node(self, node_id: str) -> list[ContainerRecord]:
        """Get all containers on a specific node."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM containers WHERE node_id = ?", (node_id,),
        ).fetchall()
        return [self._row_to_container(r) for r in rows]

    def get_all_containers(self) -> list[ContainerRecord]:
        """Get all container records across the cluster."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM containers").fetchall()
        return [self._row_to_container(r) for r in rows]

    def mark_container_lost(self, container_id: str) -> None:
        """Mark a container as lost (node went down)."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE containers SET status = 'lost' WHERE container_id = ?",
            (container_id,),
        )
        conn.commit()

    # -- Assignment operations -------------------------------------------------

    def create_assignment(
        self,
        node_id: str,
        action: str,
        deployment_id: str = "",
        container_id: str = "",
        service_name: str = "",
        image: str = "",
        command: list[str] | None = None,
        cpu: int = 50,
        memory: int = 64,
        env: dict[str, str] | None = None,
    ) -> Assignment:
        """Create a pending assignment for an agent."""
        assignment_id = f"assign-{secrets.token_hex(4)}"
        conn = self._get_conn()

        conn.execute("""
            INSERT INTO assignments
                (assignment_id, node_id, action, deployment_id, container_id,
                 service_name, image, command, cpu, memory, env, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            assignment_id, node_id, action, deployment_id, container_id,
            service_name, image, json.dumps(command or []),
            cpu, memory, json.dumps(env or {}), time.time(),
        ))
        conn.commit()

        return Assignment(
            assignment_id=assignment_id,
            node_id=node_id,
            action=action,
            deployment_id=deployment_id,
            container_id=container_id,
            service_name=service_name,
            image=image,
            command=command or [],
            cpu=cpu,
            memory=memory,
            env=env or {},
        )

    def get_pending_assignments(self, node_id: str) -> list[Assignment]:
        """Get unacknowledged assignments for a node."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM assignments
            WHERE node_id = ? AND acknowledged = 0
            ORDER BY created_at
        """, (node_id,)).fetchall()
        return [self._row_to_assignment(r) for r in rows]

    def acknowledge_assignment(self, assignment_id: str) -> None:
        """Mark an assignment as acknowledged by the agent."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE assignments SET acknowledged = 1 WHERE assignment_id = ?",
            (assignment_id,),
        )
        conn.commit()

    def clear_old_assignments(self) -> None:
        """Remove acknowledged assignments older than 1 hour."""
        conn = self._get_conn()
        cutoff = time.time() - 3600
        with self._write_lock:
            conn.execute(
                "DELETE FROM assignments WHERE acknowledged = 1 AND created_at < ?",
                (cutoff,),
            )
            conn.commit()

    def count_pending_creates(self, deployment_id: str) -> int:
        """Count unacknowledged 'create' assignments for a deployment.

        Used by the reconciler to avoid scheduling duplicate containers
        while agents haven't yet polled and started them.
        """
        conn = self._get_conn()
        row = conn.execute("""
            SELECT COUNT(*) FROM assignments
            WHERE deployment_id = ? AND action = 'create' AND acknowledged = 0
        """, (deployment_id,)).fetchone()
        return row[0] if row else 0

    def has_pending_stop(self, container_id: str) -> bool:
        """Check if there's already a pending stop assignment for a container."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT COUNT(*) FROM assignments
            WHERE container_id = ? AND action = 'stop' AND acknowledged = 0
        """, (container_id,)).fetchone()
        return (row[0] or 0) > 0

    def reserve_resources(self, node_id: str, cpu: int, memory: int) -> None:
        """Optimistically reserve resources on a node.

        Called by the reconciler after scheduling. Will be corrected
        on the next heartbeat from the agent.
        """
        conn = self._get_conn()
        with self._write_lock:
            conn.execute("""
                UPDATE nodes SET
                    cpu_used = cpu_used + ?,
                    memory_used = memory_used + ?
                WHERE node_id = ?
            """, (cpu, memory, node_id))
            conn.commit()

    def update_master_heartbeat(self, node_id: str) -> None:
        """Update the master node's own heartbeat timestamp.

        Called by the reconciler so the master doesn't mark itself
        as unhealthy.
        """
        conn = self._get_conn()
        conn.execute("""
            UPDATE nodes SET last_heartbeat = ?
            WHERE node_id = ? AND role = 'master'
        """, (time.time(), node_id))
        conn.commit()

    # -- Events ----------------------------------------------------------------

    def add_event(
        self,
        event_type: str,
        message: str,
        node_id: str = "",
        container_id: str = "",
    ) -> None:
        """Log a cluster event."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO events (timestamp, node_id, container_id, event_type, message)
            VALUES (?, ?, ?, ?, ?)
        """, (time.time(), node_id, container_id, event_type, message))
        conn.commit()

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """Get recent events for display."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM events ORDER BY timestamp DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def cleanup_old_events(self) -> None:
        """Remove events older than retention period."""
        conn = self._get_conn()
        cutoff = time.time() - EVENT_RETENTION_SECONDS
        conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        conn.commit()

    # -- Cluster summary -------------------------------------------------------

    def get_cluster_summary(self) -> dict[str, Any]:
        """Get a summary of the entire cluster state."""
        nodes = self.get_all_nodes()
        deployments = self.get_all_deployments()
        containers = self.get_all_containers()

        return {
            "nodes": {
                "total": len(nodes),
                "healthy": sum(1 for n in nodes if n.is_healthy),
                "unhealthy": sum(1 for n in nodes if not n.is_healthy),
                "list": [
                    {
                        "node_id": n.node_id,
                        "address": n.address,
                        "role": n.role,
                        "status": n.status,
                        "cpu": f"{n.cpu_used}/{n.cpu_total}",
                        "memory": f"{n.memory_used}/{n.memory_total}MB",
                        "last_heartbeat": n.last_heartbeat,
                    }
                    for n in nodes
                ],
            },
            "deployments": {
                "total": len(deployments),
                "list": [
                    {
                        "service": d.service_name,
                        "image": d.image,
                        "replicas": d.replicas,
                        "running": sum(
                            1 for c in containers
                            if c.deployment_id == d.deployment_id
                            and c.status == "running"
                        ),
                    }
                    for d in deployments
                ],
            },
            "containers": {
                "total": len(containers),
                "running": sum(1 for c in containers if c.status == "running"),
                "stopped": sum(1 for c in containers if c.status != "running"),
            },
        }

    # -- Helpers ---------------------------------------------------------------

    def _row_to_node(self, row: sqlite3.Row) -> NodeInfo:
        return NodeInfo(
            node_id=row["node_id"],
            address=row["address"],
            role=row["role"],
            status=row["status"],
            cpu_total=row["cpu_total"],
            cpu_used=row["cpu_used"],
            memory_total=row["memory_total"],
            memory_used=row["memory_used"],
            last_heartbeat=row["last_heartbeat"] or 0.0,
            joined_at=row["joined_at"] or 0.0,
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def _row_to_deployment(self, row: sqlite3.Row) -> Deployment:
        return Deployment(
            deployment_id=row["deployment_id"],
            service_name=row["service_name"],
            image=row["image"],
            command=json.loads(row["command"]),
            replicas=row["replicas"],
            cpu=row["cpu"],
            memory=row["memory"],
            restart=row["restart"],
            env=json.loads(row["env"] or "{}"),
            health_check=json.loads(row["health_check"]) if row["health_check"] else None,
            created_at=row["created_at"] or 0.0,
            updated_at=row["updated_at"] or 0.0,
        )

    def _row_to_container(self, row: sqlite3.Row) -> ContainerRecord:
        return ContainerRecord(
            container_id=row["container_id"],
            node_id=row["node_id"],
            deployment_id=row["deployment_id"] or "",
            service_name=row["service_name"],
            status=row["status"],
            pid=row["pid"],
            cpu_used=row["cpu_used"],
            memory_used=row["memory_used"],
            health=row["health"] or "none",
            started_at=row["started_at"] or 0.0,
            reported_at=row["reported_at"] or 0.0,
        )

    def _row_to_assignment(self, row: sqlite3.Row) -> Assignment:
        return Assignment(
            assignment_id=row["assignment_id"],
            node_id=row["node_id"],
            action=row["action"],
            deployment_id=row["deployment_id"] or "",
            container_id=row["container_id"] or "",
            service_name=row["service_name"] or "",
            image=row["image"] or "",
            command=json.loads(row["command"] or "[]"),
            cpu=row["cpu"],
            memory=row["memory"],
            env=json.loads(row["env"] or "{}"),
            created_at=row["created_at"] or 0.0,
            acknowledged=bool(row["acknowledged"]),
        )
