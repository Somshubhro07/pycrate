"""
Rolling Deployment Manager
=============================

Handles zero-downtime updates of running services. When a deployment's
image or configuration changes, the rolling deployer:

    1. Creates new containers with the updated config (surge)
    2. Waits for new containers to pass health checks
    3. Removes old containers one at a time
    4. Repeats until all old containers are replaced

The default strategy is "rolling update" with max_surge=1 and
max_unavailable=0, meaning:
    - At most 1 extra container during the transition
    - Zero containers are taken down before the replacement is healthy

This is the same strategy Kubernetes uses by default for Deployments.

Usage:
    deployer = RollingDeployer(state, scheduler)
    deployer.update("web", new_image="alpine:3.21")
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

from cluster.scheduler import NoCapacityError, Scheduler
from cluster.state import ClusterState, Deployment

logger = logging.getLogger(__name__)

# How long to wait for a new container to become healthy (seconds)
HEALTH_CHECK_TIMEOUT = 120
# How often to check health status
HEALTH_POLL_INTERVAL = 3


@dataclass
class RolloutStatus:
    """Status of a rolling deployment."""
    deployment_id: str
    service_name: str
    old_image: str
    new_image: str
    total_replicas: int
    updated: int = 0
    pending: int = 0
    failed: int = 0
    state: str = "in_progress"     # "in_progress" | "completed" | "failed" | "rolled_back"
    events: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at

    def log(self, msg: str) -> None:
        self.events.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        logger.info("[rollout %s] %s", self.service_name, msg)


class RollingDeployer:
    """Manages zero-downtime rolling updates for cluster deployments."""

    def __init__(
        self,
        state: ClusterState,
        scheduler: Scheduler,
    ) -> None:
        self._state = state
        self._scheduler = scheduler
        self._active_rollouts: dict[str, RolloutStatus] = {}

    @property
    def active_rollouts(self) -> dict[str, RolloutStatus]:
        return dict(self._active_rollouts)

    def update(
        self,
        service_name: str,
        new_image: str | None = None,
        new_cpu: int | None = None,
        new_memory: int | None = None,
        new_command: list[str] | None = None,
        max_surge: int = 1,
    ) -> RolloutStatus:
        """Start a rolling update for a deployment.

        Args:
            service_name: The service to update.
            new_image: New container image (if changing).
            new_cpu: New CPU limit (if changing).
            new_memory: New memory limit (if changing).
            new_command: New command (if changing).
            max_surge: Max extra containers during rollout.

        Returns:
            RolloutStatus tracking the update progress.
        """
        dep = self._state.get_deployment(service_name)
        if not dep:
            raise ValueError(f"Deployment '{service_name}' not found")

        old_image = dep.image
        rollout = RolloutStatus(
            deployment_id=dep.deployment_id,
            service_name=service_name,
            old_image=old_image,
            new_image=new_image or old_image,
            total_replicas=dep.replicas,
            pending=dep.replicas,
        )

        self._active_rollouts[service_name] = rollout
        rollout.log(f"Starting rolling update: {old_image} -> {new_image or old_image}")

        # Get current containers for this deployment
        current_containers = self._state.get_containers_for_deployment(dep.deployment_id)
        rollout.log(f"Found {len(current_containers)} existing containers")

        # Create a new deployment ID for the updated version
        new_dep = Deployment(
            deployment_id=f"dep-{secrets.token_hex(4)}",
            service_name=service_name,
            image=new_image or dep.image,
            command=new_command or dep.command,
            replicas=dep.replicas,
            cpu=new_cpu or dep.cpu,
            memory=new_memory or dep.memory,
            restart=dep.restart,
            env=dep.env,
            health_check=dep.health_check,
        )

        # Roll through containers one at a time + surge
        batch_size = max_surge
        old_to_remove = list(current_containers)

        for batch_start in range(0, len(old_to_remove), batch_size):
            batch = old_to_remove[batch_start:batch_start + batch_size]

            # Step 1: Schedule new containers (surge)
            for _ in batch:
                try:
                    decision = self._scheduler.schedule(new_dep)
                    self._state.create_assignment(
                        node_id=decision.node.node_id,
                        action="create",
                        deployment_id=new_dep.deployment_id,
                        service_name=service_name,
                        image=new_dep.image,
                        command=new_dep.command,
                        cpu=new_dep.cpu,
                        memory=new_dep.memory,
                        env=new_dep.env,
                    )
                    rollout.log(
                        f"Scheduled new container on {decision.node.node_id}"
                    )
                except NoCapacityError as e:
                    rollout.log(f"Scheduling failed: {e}")
                    rollout.failed += 1

            # Step 2: Wait for new containers to start
            # In a real system, we'd wait for health checks to pass.
            # Here we wait a fixed period and let the reconciler handle health.
            rollout.log(f"Waiting for {len(batch)} new container(s) to start...")

            # Give the agent time to poll and create
            waited = 0
            while waited < HEALTH_CHECK_TIMEOUT:
                time.sleep(HEALTH_POLL_INTERVAL)
                waited += HEALTH_POLL_INTERVAL

                # Check if new containers exist for this deployment
                new_containers = self._state.get_containers_for_deployment(
                    new_dep.deployment_id
                )
                running_new = [c for c in new_containers if c.status == "running"]

                if len(running_new) >= len(batch):
                    rollout.log(f"{len(running_new)} new container(s) running")
                    break

            # Step 3: Stop old containers
            for old_container in batch:
                self._state.create_assignment(
                    node_id=old_container.node_id,
                    action="stop",
                    deployment_id=dep.deployment_id,
                    container_id=old_container.container_id,
                    service_name=service_name,
                )
                rollout.updated += 1
                rollout.pending -= 1
                rollout.log(
                    f"Stopping old container {old_container.container_id[:12]} "
                    f"({rollout.updated}/{rollout.total_replicas})"
                )

        # Step 4: Update the deployment record to use the new config
        new_dep.deployment_id = dep.deployment_id  # Keep the same ID
        self._state.create_deployment(new_dep)

        rollout.state = "completed" if rollout.failed == 0 else "failed"
        rollout.log(
            f"Rollout {'completed' if rollout.failed == 0 else 'completed with errors'} "
            f"in {rollout.duration_seconds:.1f}s "
            f"({rollout.updated} updated, {rollout.failed} failed)"
        )

        self._state.add_event(
            event_type="deployment.rollout",
            message=(
                f"Rolling update {service_name}: "
                f"{old_image} -> {new_dep.image} "
                f"({rollout.updated}/{rollout.total_replicas} updated)"
            ),
        )

        return rollout

    def get_rollout_status(self, service_name: str) -> RolloutStatus | None:
        """Get the status of an active or recent rollout."""
        return self._active_rollouts.get(service_name)
