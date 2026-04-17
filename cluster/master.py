"""
Master Node — Cluster Control Plane
======================================

The master runs a FastAPI server that agents communicate with, plus
background threads for reconciliation and node health monitoring.

The master is the single source of truth for the cluster. It:
    - Accepts agent registrations (join)
    - Receives heartbeats (state reports from agents)
    - Serves work assignments (agents poll for tasks)
    - Runs the reconciliation loop (desired vs actual state)
    - Provides cluster-wide status to the CLI

API design: agents initiate all connections (HTTP polling).
This means the master never needs to reach agents — it works
through NAT, firewalls, and different networks.

Usage:
    master = MasterNode(host="0.0.0.0", port=9000)
    master.start()   # Blocks (runs uvicorn)
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path

from cluster.deploy import RollingDeployer
from cluster.reconciler import Reconciler
from cluster.scheduler import Scheduler
from cluster.state import ClusterState, Deployment

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9000
DATA_DIR = Path("/var/lib/pycrate")


def _get_host_resources() -> tuple[int, int]:
    """Detect host CPU and memory capacity.

    Returns:
        (cpu_total, memory_total_mb)
        cpu_total is in percentage units: 100 per core.
    """
    try:
        cpu_count = os.cpu_count() or 1
        cpu_total = cpu_count * 100  # 100% per core

        # Read total memory from /proc/meminfo
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    memory_total = kb // 1024  # Convert to MB
                    return cpu_total, memory_total

        return cpu_total, 1024  # Fallback
    except Exception:
        return 100, 1024  # Fallback: 1 core, 1GB


def create_master_app(
    db_path: Path | str | None = None,
    node_id: str | None = None,
):
    """Create the FastAPI application for the master node.

    Separated from MasterNode class so it can be used with
    uvicorn programmatically or via CLI.
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
    except ImportError:
        raise RuntimeError(
            "Master node requires FastAPI. Install with: "
            "pip install 'pycrate[server]'"
        )

    # Initialize state
    db = db_path or DATA_DIR / "cluster.db"
    state = ClusterState(db)

    # Register self as master node
    master_id = node_id or f"master-{secrets.token_hex(3)}"
    cpu_total, memory_total = _get_host_resources()
    state.register_node(
        node_id=master_id,
        address=f"localhost:{DEFAULT_PORT}",
        role="master",
        cpu_total=cpu_total,
        memory_total=memory_total,
    )

    # Initialize scheduler, reconciler, and deployer
    scheduler = Scheduler(state)
    reconciler = Reconciler(state, scheduler)
    deployer = RollingDeployer(state, scheduler)

    # --- FastAPI app ---
    app = FastAPI(
        title="PyCrate Cluster Master",
        version="0.3.0",
        description="Multi-node container orchestration control plane",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store references on app state for access in endpoints
    app.state.cluster_state = state
    app.state.scheduler = scheduler
    app.state.reconciler = reconciler
    app.state.deployer = deployer
    app.state.master_id = master_id

    # --- Pydantic models ---

    class JoinRequest(BaseModel):
        node_id: str
        address: str
        cpu_total: int = 100
        memory_total: int = 1024

    class HeartbeatRequest(BaseModel):
        node_id: str
        containers: list[dict] = []
        resources: dict = {}

    class DeployRequest(BaseModel):
        service_name: str
        image: str
        command: list[str] = ["/bin/sh"]
        replicas: int = 1
        cpu: int = 50
        memory: int = 64
        restart: str = "always"
        env: dict = {}
        health_check: dict | None = None

    class ScaleRequest(BaseModel):
        replicas: int

    class AckRequest(BaseModel):
        assignment_ids: list[str]

    class RolloutRequest(BaseModel):
        image: str | None = None
        cpu: int | None = None
        memory: int | None = None
        command: list[str] | None = None
        max_surge: int = 1

    # --- Lifecycle events ---

    @app.on_event("startup")
    async def startup():
        reconciler.start()
        logger.info("Master node %s started", master_id)

    @app.on_event("shutdown")
    async def shutdown():
        reconciler.stop()
        logger.info("Master node %s shutting down", master_id)

    # --- Node management ---

    @app.post("/api/v1/join")
    async def join_cluster(req: JoinRequest):
        """Register a worker node with the cluster."""
        node = state.register_node(
            node_id=req.node_id,
            address=req.address,
            role="worker",
            cpu_total=req.cpu_total,
            memory_total=req.memory_total,
        )
        logger.info(
            "Node %s joined from %s (%d CPU, %dMB)",
            req.node_id, req.address, req.cpu_total, req.memory_total,
        )
        return {
            "status": "joined",
            "master_id": master_id,
            "node_id": node.node_id,
        }

    @app.delete("/api/v1/nodes/{node_id}")
    async def remove_node(node_id: str):
        """Remove a node from the cluster."""
        node = state.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        state.remove_node(node_id)
        return {"status": "removed", "node_id": node_id}

    # --- Heartbeat ---

    @app.post("/api/v1/heartbeat")
    async def heartbeat(req: HeartbeatRequest):
        """Receive a heartbeat from an agent."""
        node = state.get_node(req.node_id)
        if not node:
            raise HTTPException(404, f"Node {req.node_id} not registered")

        state.update_heartbeat(
            node_id=req.node_id,
            containers=req.containers,
            resources=req.resources,
        )
        return {"status": "ok", "timestamp": time.time()}

    # --- Assignments ---

    @app.get("/api/v1/assignments/{node_id}")
    async def get_assignments(node_id: str):
        """Get pending work assignments for a node."""
        node = state.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not registered")

        assignments = state.get_pending_assignments(node_id)
        return {
            "assignments": [
                {
                    "assignment_id": a.assignment_id,
                    "action": a.action,
                    "deployment_id": a.deployment_id,
                    "container_id": a.container_id,
                    "service_name": a.service_name,
                    "image": a.image,
                    "command": a.command,
                    "cpu": a.cpu,
                    "memory": a.memory,
                    "env": a.env,
                }
                for a in assignments
            ],
        }

    @app.post("/api/v1/assignments/ack")
    async def ack_assignments(req: AckRequest):
        """Acknowledge that assignments have been processed."""
        for aid in req.assignment_ids:
            state.acknowledge_assignment(aid)
        return {"status": "ok", "acknowledged": len(req.assignment_ids)}

    # --- Deployments ---

    @app.post("/api/v1/deploy")
    async def create_deployment(req: DeployRequest):
        """Create or update a deployment."""
        dep = Deployment(
            deployment_id=f"dep-{secrets.token_hex(4)}",
            service_name=req.service_name,
            image=req.image,
            command=req.command,
            replicas=req.replicas,
            cpu=req.cpu,
            memory=req.memory,
            restart=req.restart,
            env=req.env,
            health_check=req.health_check,
        )
        state.create_deployment(dep)

        logger.info(
            "Deployment created: %s (%d x %s)",
            dep.service_name, dep.replicas, dep.image,
        )
        return {
            "status": "created",
            "deployment_id": dep.deployment_id,
            "service_name": dep.service_name,
        }

    @app.put("/api/v1/deploy/{service_name}/scale")
    async def scale_deployment(service_name: str, req: ScaleRequest):
        """Scale a deployment's replica count."""
        dep = state.get_deployment(service_name)
        if not dep:
            raise HTTPException(404, f"Deployment {service_name} not found")

        state.update_replicas(service_name, req.replicas)
        logger.info("Scaled %s to %d replicas", service_name, req.replicas)
        return {"status": "scaled", "service_name": service_name, "replicas": req.replicas}

    @app.delete("/api/v1/deploy/{service_name}")
    async def delete_deployment(service_name: str):
        """Delete a deployment (reconciler will stop containers)."""
        dep = state.get_deployment(service_name)
        if not dep:
            raise HTTPException(404, f"Deployment {service_name} not found")

        state.delete_deployment(service_name)
        return {"status": "deleted", "service_name": service_name}

    @app.get("/api/v1/deployments")
    async def list_deployments():
        """List all deployments."""
        deps = state.get_all_deployments()
        return {
            "deployments": [
                {
                    "deployment_id": d.deployment_id,
                    "service_name": d.service_name,
                    "image": d.image,
                    "replicas": d.replicas,
                    "cpu": d.cpu,
                    "memory": d.memory,
                }
                for d in deps
            ],
        }

    # --- Cluster status ---

    @app.get("/api/v1/state")
    async def cluster_status():
        """Full cluster state for CLI and dashboard."""
        summary = state.get_cluster_summary()
        summary["reconciler"] = reconciler.stats
        summary["master_id"] = master_id
        return summary

    @app.get("/api/v1/nodes")
    async def list_nodes():
        """List all registered nodes."""
        nodes = state.get_all_nodes()
        return {
            "nodes": [
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
        }

    @app.get("/api/v1/events")
    async def list_events(limit: int = 50):
        """Recent cluster events."""
        return {"events": state.get_recent_events(limit)}

    @app.get("/api/v1/capacity")
    async def capacity_report():
        """Node capacity overview for scheduling decisions."""
        return {"nodes": scheduler.get_capacity_report()}

    # --- Rolling deployments ---

    @app.post("/api/v1/rollout/{service_name}")
    async def rollout_update(service_name: str, req: RolloutRequest):
        """Trigger a rolling update for a deployment."""
        import threading

        dep = state.get_deployment(service_name)
        if not dep:
            raise HTTPException(404, f"Deployment {service_name} not found")

        # Run the rollout in a background thread (it blocks while waiting
        # for new containers to start)
        def _run_rollout():
            try:
                deployer.update(
                    service_name=service_name,
                    new_image=req.image,
                    new_cpu=req.cpu,
                    new_memory=req.memory,
                    new_command=req.command,
                    max_surge=req.max_surge,
                )
            except Exception as e:
                logger.error("Rollout failed for %s: %s", service_name, e)

        thread = threading.Thread(
            target=_run_rollout,
            name=f"rollout-{service_name}",
            daemon=True,
        )
        thread.start()

        return {
            "status": "rolling_update_started",
            "service_name": service_name,
            "new_image": req.image or dep.image,
        }

    @app.get("/api/v1/rollout/{service_name}")
    async def rollout_status(service_name: str):
        """Get the status of an active rollout."""
        status = deployer.get_rollout_status(service_name)
        if not status:
            return {"status": "no_active_rollout", "service_name": service_name}
        return {
            "service_name": status.service_name,
            "old_image": status.old_image,
            "new_image": status.new_image,
            "state": status.state,
            "updated": status.updated,
            "pending": status.pending,
            "failed": status.failed,
            "total": status.total_replicas,
            "duration_seconds": round(status.duration_seconds, 1),
            "events": status.events[-10:],  # Last 10 events
        }

    return app


class MasterNode:
    """Convenience wrapper to run the master as a standalone process."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        db_path: Path | str | None = None,
        node_id: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.app = create_master_app(db_path=db_path, node_id=node_id)

    def start(self) -> None:
        """Start the master node (blocks)."""
        import uvicorn

        logger.info("Starting master on %s:%d", self.host, self.port)
        uvicorn.run(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
