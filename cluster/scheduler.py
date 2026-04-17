"""
Cluster Scheduler
===================

Decides which node should run a new container. Uses resource-aware
spread scheduling: containers are placed on the node with the most
available resources to distribute load evenly.

Why spread (not bin-pack)?
    At small scale (2-10 nodes), spreading containers evenly makes node
    failures less impactful. If a 3-node cluster loses a node with spread,
    you lose ~33% of containers. With bin-pack, you could lose 80% because
    they were all on the most-utilized node.

The scheduler runs synchronously inside the reconciler's lock. At our
target scale (~50 containers), this adds <1ms per reconciliation pass.

Algorithm:
    1. Filter: only healthy nodes with enough free CPU + memory
    2. Score: rank by available resources (most free wins)
    3. Select: return the top-scored node
    4. Reserve: optimistically mark resources as used

Usage:
    scheduler = Scheduler(state)
    node = scheduler.schedule(deployment)
    # If None, no eligible node has capacity
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cluster.state import ClusterState, Deployment, NodeInfo

logger = logging.getLogger(__name__)


class NoCapacityError(Exception):
    """Raised when no node has enough resources for a deployment."""

    def __init__(self, deployment: Deployment, reason: str = "") -> None:
        self.deployment = deployment
        msg = (
            f"Cannot schedule {deployment.service_name}: "
            f"requires {deployment.cpu}% CPU + {deployment.memory}MB memory"
        )
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


@dataclass
class SchedulingDecision:
    """Result of a scheduling decision."""
    node: NodeInfo
    deployment: Deployment
    score: float
    reason: str


class Scheduler:
    """Resource-aware container scheduler.

    The scheduler is stateless — it reads current cluster state from the
    state store on every call and makes decisions based on current conditions.
    """

    def __init__(self, state: ClusterState) -> None:
        self._state = state

    def schedule(self, deployment: Deployment) -> SchedulingDecision:
        """Find the best node for a new container.

        Args:
            deployment: The deployment that needs a new replica.

        Returns:
            SchedulingDecision with the selected node.

        Raises:
            NoCapacityError: If no healthy node has enough resources.
        """
        nodes = self._state.get_healthy_nodes()

        if not nodes:
            raise NoCapacityError(deployment, "no healthy nodes in cluster")

        # Step 1: Filter — only nodes with enough resources
        eligible = []
        for node in nodes:
            if node.role == "master":
                # By default, don't schedule work on master
                # (master runs the control plane, not user workloads)
                continue
            if node.cpu_available >= deployment.cpu and node.memory_available >= deployment.memory:
                eligible.append(node)

        if not eligible:
            # If no workers have capacity, try master as fallback
            for node in nodes:
                if node.cpu_available >= deployment.cpu and node.memory_available >= deployment.memory:
                    eligible.append(node)

        if not eligible:
            # Build detailed capacity report for error message
            capacity_report = "; ".join(
                f"{n.node_id}: {n.cpu_available}% CPU, {n.memory_available}MB free"
                for n in nodes
            )
            raise NoCapacityError(
                deployment,
                f"no node has enough resources. Current: [{capacity_report}]",
            )

        # Step 2: Score — rank by available resources (spread strategy)
        # Higher score = more free resources = preferred target
        scored = []
        for node in eligible:
            score = self._score_node(node, deployment)
            scored.append(SchedulingDecision(
                node=node,
                deployment=deployment,
                score=score,
                reason=self._explain_score(node, score),
            ))

        scored.sort(key=lambda d: d.score, reverse=True)

        # Step 3: Select — top scored node
        decision = scored[0]
        logger.info(
            "Scheduled %s -> %s (score=%.1f, %s)",
            deployment.service_name,
            decision.node.node_id,
            decision.score,
            decision.reason,
        )

        return decision

    def schedule_batch(
        self, deployments: list[Deployment]
    ) -> list[SchedulingDecision]:
        """Schedule multiple containers. Returns decisions in order.

        Failed scheduling for one deployment doesn't affect others.
        """
        decisions = []
        for dep in deployments:
            try:
                decision = self.schedule(dep)
                decisions.append(decision)
            except NoCapacityError as e:
                logger.warning("Scheduling failed: %s", e)
        return decisions

    def _score_node(self, node: NodeInfo, deployment: Deployment) -> float:
        """Compute a scheduling score for a node.

        Factors:
            - Available CPU (40% weight)
            - Available memory (40% weight)
            - Role penalty: master nodes get a -20 penalty (prefer workers)
            - Existing container count penalty (mild anti-affinity)
        """
        # Normalize resources to 0-100 scale
        cpu_score = (node.cpu_available / max(node.cpu_total, 1)) * 100
        mem_score = (node.memory_available / max(node.memory_total, 1)) * 100

        score = (cpu_score * 0.4) + (mem_score * 0.4)

        # Role penalty
        if node.role == "master":
            score -= 20.0

        # Container density penalty (mild — prefer less-loaded nodes)
        containers_on_node = self._state.get_containers_for_node(node.node_id)
        running = sum(1 for c in containers_on_node if c.status == "running")
        score -= running * 2.0  # -2 points per running container

        return max(score, 0)

    def _explain_score(self, node: NodeInfo, score: float) -> str:
        """Human-readable explanation of why this score was assigned."""
        return (
            f"{node.cpu_available}% CPU free, "
            f"{node.memory_available}MB memory free, "
            f"score={score:.1f}"
        )

    def get_capacity_report(self) -> list[dict]:
        """Generate a human-readable capacity report for all nodes."""
        nodes = self._state.get_all_nodes()
        report = []
        for node in nodes:
            containers = self._state.get_containers_for_node(node.node_id)
            running = sum(1 for c in containers if c.status == "running")
            report.append({
                "node_id": node.node_id,
                "status": node.status,
                "role": node.role,
                "cpu": f"{node.cpu_used}/{node.cpu_total} ({node.cpu_available} free)",
                "memory": f"{node.memory_used}/{node.memory_total}MB ({node.memory_available}MB free)",
                "containers": running,
            })
        return report
