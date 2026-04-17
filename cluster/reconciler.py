"""
Cluster Reconciliation Engine
================================

The heart of PyCrate's multi-node orchestration. Runs on the master node
as a background thread, executing every RECONCILE_INTERVAL seconds.

The reconciliation loop:
    1. Read desired state (deployments table)
    2. Read actual state (containers table, populated by agent heartbeats)
    3. Compute the diff (what's missing, what's excess, what's orphaned)
    4. Generate assignments for agents to execute the diff
    5. Detect dead nodes and mark their containers for rescheduling

This is the same pattern used by:
    - Kubernetes controller manager (reconciles Deployments → Pods)
    - Nomad scheduler (reconciles Jobs → Allocations)
    - Docker Swarm manager (reconciles Services → Tasks)

PyCrate implements it at the simplest possible level: one thread, one
SQLite database, pure Python.

Usage:
    reconciler = Reconciler(state, scheduler)
    reconciler.start()     # Background thread
    reconciler.stop()      # Graceful shutdown
    reconciler.reconcile() # Manual single pass (for testing)
"""

from __future__ import annotations

import logging
import threading
import time

from cluster.scheduler import NoCapacityError, Scheduler
from cluster.state import ClusterState, Deployment

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 5         # Seconds between reconciliation passes
NODE_TIMEOUT_SECONDS = 30      # Seconds before a node is marked unhealthy
MAX_RESTART_BACKOFF = 300       # Max seconds between restart attempts


class Reconciler:
    """Continuously reconciles desired state with actual state.

    Runs as a daemon thread on the master node. Each pass:
    1. Checks node health (heartbeat timeouts)
    2. Computes scheduling decisions (deficit/excess containers)
    3. Creates assignments for agents
    4. Cleans up stale data
    """

    def __init__(
        self,
        state: ClusterState,
        scheduler: Scheduler,
    ) -> None:
        self._state = state
        self._scheduler = scheduler
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pass_count = 0
        self._last_pass_duration: float = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        return {
            "running": self.is_running,
            "pass_count": self._pass_count,
            "last_pass_ms": round(self._last_pass_duration * 1000, 1),
            "interval_seconds": RECONCILE_INTERVAL,
        }

    def start(self) -> None:
        """Start the reconciliation loop in a background thread."""
        if self.is_running:
            logger.warning("Reconciler is already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="cluster-reconciler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Cluster reconciler started (interval=%ds)", RECONCILE_INTERVAL)

    def stop(self) -> None:
        """Stop the reconciliation loop."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=RECONCILE_INTERVAL + 2)
        logger.info(
            "Cluster reconciler stopped after %d passes", self._pass_count
        )

    def _run_loop(self) -> None:
        """Main reconciliation loop."""
        while not self._stop_event.is_set():
            start = time.monotonic()

            try:
                self.reconcile()
            except Exception as e:
                logger.error("Reconciliation pass failed: %s", e, exc_info=True)
                self._state.add_event(
                    event_type="reconciler.error",
                    message=f"Pass {self._pass_count} failed: {e}",
                )

            self._last_pass_duration = time.monotonic() - start
            self._pass_count += 1

            self._stop_event.wait(timeout=RECONCILE_INTERVAL)

    def reconcile(self) -> None:
        """Execute a single reconciliation pass.

        Public method so it can be called manually for testing.
        """
        # Phase 1: Check node health
        unhealthy_nodes = self._check_node_health()

        # Phase 2: Handle containers on unhealthy nodes
        if unhealthy_nodes:
            self._handle_node_failures(unhealthy_nodes)

        # Phase 3: Reconcile deployments
        self._reconcile_deployments()

        # Phase 4: Clean up orphaned containers
        self._cleanup_orphans()

        # Phase 5: Periodic maintenance
        if self._pass_count % 60 == 0:  # Every ~5 minutes
            self._state.clear_old_assignments()
            self._state.cleanup_old_events()

    def _check_node_health(self) -> list[str]:
        """Check for nodes that missed their heartbeat."""
        unhealthy = self._state.check_node_health()

        for node_id in unhealthy:
            logger.warning(
                "Node %s missed heartbeat (>%ds), marking unhealthy",
                node_id, NODE_TIMEOUT_SECONDS,
            )

        return unhealthy

    def _handle_node_failures(self, unhealthy_nodes: list[str]) -> None:
        """Handle containers on unhealthy nodes.

        Mark containers as lost. The deficit will be detected in
        _reconcile_deployments() and new containers will be scheduled
        on healthy nodes.
        """
        for node_id in unhealthy_nodes:
            containers = self._state.get_containers_for_node(node_id)
            for c in containers:
                if c.status in ("running", "starting"):
                    self._state.mark_container_lost(c.container_id)
                    logger.warning(
                        "Container %s on %s marked as lost",
                        c.container_id, node_id,
                    )
                    self._state.add_event(
                        event_type="container.lost",
                        node_id=node_id,
                        container_id=c.container_id,
                        message=f"Container lost due to node {node_id} failure",
                    )

    def _reconcile_deployments(self) -> None:
        """Compare desired deployments with actual containers.

        For each deployment:
        - If running < desired: schedule new containers
        - If running > desired: mark excess for removal
        """
        deployments = self._state.get_all_deployments()
        all_containers = self._state.get_all_containers()

        for deployment in deployments:
            running = [
                c for c in all_containers
                if c.deployment_id == deployment.deployment_id
                and c.status == "running"
            ]

            deficit = deployment.replicas - len(running)

            if deficit > 0:
                self._scale_up(deployment, deficit)
            elif deficit < 0:
                self._scale_down(deployment, running, abs(deficit))

    def _scale_up(self, deployment: Deployment, count: int) -> None:
        """Create assignments to start new containers."""
        logger.info(
            "Scaling up %s: need %d more replica(s)",
            deployment.service_name, count,
        )

        for _ in range(count):
            try:
                decision = self._scheduler.schedule(deployment)
                node = decision.node

                self._state.create_assignment(
                    node_id=node.node_id,
                    action="create",
                    deployment_id=deployment.deployment_id,
                    service_name=deployment.service_name,
                    image=deployment.image,
                    command=deployment.command,
                    cpu=deployment.cpu,
                    memory=deployment.memory,
                    env=deployment.env,
                )

                # Optimistic resource reservation
                # (will be corrected on next heartbeat)
                self._state._get_conn().execute("""
                    UPDATE nodes SET
                        cpu_used = cpu_used + ?,
                        memory_used = memory_used + ?
                    WHERE node_id = ?
                """, (deployment.cpu, deployment.memory, node.node_id))
                self._state._get_conn().commit()

                self._state.add_event(
                    event_type="container.scheduled",
                    node_id=node.node_id,
                    message=(
                        f"Scheduled {deployment.service_name} on {node.node_id} "
                        f"(score={decision.score:.1f})"
                    ),
                )

            except NoCapacityError as e:
                logger.warning(
                    "Cannot scale %s: %s", deployment.service_name, e
                )
                self._state.add_event(
                    event_type="scheduler.no_capacity",
                    message=str(e),
                )
                break  # Don't keep trying if we're out of capacity

    def _scale_down(
        self,
        deployment: Deployment,
        running: list,
        count: int,
    ) -> None:
        """Create assignments to stop excess containers."""
        logger.info(
            "Scaling down %s: stopping %d excess replica(s)",
            deployment.service_name, count,
        )

        # Stop newest containers first (keep oldest, most stable ones)
        excess = sorted(running, key=lambda c: c.started_at, reverse=True)

        for container in excess[:count]:
            self._state.create_assignment(
                node_id=container.node_id,
                action="stop",
                deployment_id=deployment.deployment_id,
                container_id=container.container_id,
                service_name=deployment.service_name,
            )

            self._state.add_event(
                event_type="container.scaling_down",
                node_id=container.node_id,
                container_id=container.container_id,
                message=f"Stopping excess replica of {deployment.service_name}",
            )

    def _cleanup_orphans(self) -> None:
        """Find and stop containers that don't belong to any deployment.

        These can happen when a deployment is deleted while containers
        are still running.
        """
        deployments = self._state.get_all_deployments()
        deployment_ids = {d.deployment_id for d in deployments}

        all_containers = self._state.get_all_containers()

        for container in all_containers:
            if container.status != "running":
                continue

            if (container.deployment_id
                    and container.deployment_id not in deployment_ids):
                logger.info(
                    "Orphaned container %s (deployment %s deleted), stopping",
                    container.container_id, container.deployment_id,
                )

                self._state.create_assignment(
                    node_id=container.node_id,
                    action="stop",
                    container_id=container.container_id,
                    service_name=container.service_name,
                )

                self._state.add_event(
                    event_type="container.orphaned",
                    node_id=container.node_id,
                    container_id=container.container_id,
                    message="Stopping orphaned container",
                )
