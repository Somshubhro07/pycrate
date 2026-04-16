"""
Compose Engine
================

Single-node multi-container orchestrator. Manages the lifecycle of all
services defined in a pycrate.yml manifest.

Core responsibilities:
    - Start services in dependency order (topological sort)
    - Monitor service health via health checks
    - Restart failed containers based on restart policy
    - Graceful shutdown in reverse dependency order

The compose engine runs a reconciliation loop in a background thread
that continuously ensures the actual state matches the desired state.
This is the same pattern Kubernetes uses at cluster scale -- we use
it at single-node scale.

Usage:
    engine = ComposeEngine(manifest)
    engine.up()       # Start all services
    engine.status()   # Check service health
    engine.down()     # Stop all services
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from engine.config import ContainerConfig
from engine.container import Container, ContainerManager
from orchestrator.health import HealthChecker, HealthStatus
from orchestrator.manifest import Manifest, ServiceConfig

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 5  # Seconds between reconciliation loops


class ServiceState(str, Enum):
    """State of a service in the compose stack."""
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"
    RESTARTING = "restarting"
    ERROR = "error"


@dataclass
class ServiceInstance:
    """Runtime state for a single service instance (one replica)."""
    service_name: str
    replica_index: int
    container: Container | None = None
    health_checker: HealthChecker | None = None
    state: ServiceState = ServiceState.PENDING
    restart_count: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def container_name(self) -> str:
        """Generate a unique container name for this instance."""
        if self.replica_index > 0:
            return f"{self.service_name}-{self.replica_index}"
        return self.service_name

    @property
    def health_status(self) -> HealthStatus:
        if self.health_checker:
            return self.health_checker.status
        return HealthStatus.NONE


class ComposeEngine:
    """Orchestrates multi-container applications from a manifest.

    The engine maintains the desired state (from the manifest) and
    continuously reconciles it with the actual state (running containers).
    """

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._manager = ContainerManager(max_containers=50)
        self._instances: dict[str, list[ServiceInstance]] = {}
        self._lock = threading.Lock()
        self._reconcile_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._initialized = False

    def up(self) -> None:
        """Start all services defined in the manifest.

        Services are started in dependency order. Each service's
        dependencies must be running before it starts.
        """
        if not self._initialized:
            self._manager.initialize()
            self._initialized = True

        start_order = self.manifest.get_start_order()
        logger.info(
            "Starting %d services in order: %s",
            len(start_order), " -> ".join(start_order),
        )

        for service_name in start_order:
            service = self.manifest.services[service_name]
            self._start_service(service)

        # Start the reconciliation loop
        self._stop_event.clear()
        self._reconcile_thread = threading.Thread(
            target=self._reconcile_loop,
            name="compose-reconciler",
            daemon=True,
        )
        self._reconcile_thread.start()

        logger.info("All services started. Reconciliation loop active.")

    def down(self) -> None:
        """Stop all services in reverse dependency order."""
        # Stop reconciliation loop first
        self._stop_event.set()
        if self._reconcile_thread and self._reconcile_thread.is_alive():
            self._reconcile_thread.join(timeout=5)

        # Stop in reverse order
        stop_order = list(reversed(self.manifest.get_start_order()))
        logger.info("Stopping services: %s", " -> ".join(stop_order))

        for service_name in stop_order:
            self._stop_service(service_name)

        logger.info("All services stopped.")

    def status(self) -> list[dict[str, Any]]:
        """Get status of all service instances."""
        result = []
        with self._lock:
            for service_name, instances in self._instances.items():
                service = self.manifest.services.get(service_name)
                for inst in instances:
                    info = {
                        "service": service_name,
                        "replica": inst.replica_index,
                        "container_name": inst.container_name,
                        "container_id": (
                            inst.container.container_id if inst.container else ""
                        ),
                        "state": inst.state.value,
                        "health": inst.health_status.value,
                        "restart_count": inst.restart_count,
                        "pid": inst.container.pid if inst.container else None,
                        "image": service.image if service else "",
                        "error": inst.last_error,
                    }
                    result.append(info)

        return result

    def scale(self, service_name: str, replicas: int) -> None:
        """Scale a service to the specified number of replicas."""
        if service_name not in self.manifest.services:
            raise ValueError(f"Unknown service: {service_name}")

        service = self.manifest.services[service_name]
        service.replicas = replicas

        # Reconciliation loop will handle the actual scaling
        logger.info("Scaling %s to %d replicas", service_name, replicas)

    def _start_service(self, service: ServiceConfig) -> None:
        """Start all replicas of a service."""
        with self._lock:
            instances = self._instances.get(service.name, [])

            for i in range(service.replicas):
                # Skip already running replicas
                existing = next(
                    (inst for inst in instances if inst.replica_index == i),
                    None,
                )
                if existing and existing.state == ServiceState.RUNNING:
                    continue

                instance = self._create_instance(service, i)
                if existing:
                    instances[instances.index(existing)] = instance
                else:
                    instances.append(instance)

            self._instances[service.name] = instances

    def _create_instance(
        self, service: ServiceConfig, replica_index: int
    ) -> ServiceInstance:
        """Create and start a single service instance."""
        instance = ServiceInstance(
            service_name=service.name,
            replica_index=replica_index,
        )
        instance.state = ServiceState.STARTING

        # Build the container config
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        env.update(service.env)
        # Inject service metadata as env vars
        env["PYCRATE_SERVICE"] = service.name
        env["PYCRATE_REPLICA"] = str(replica_index)

        config = ContainerConfig(
            name=instance.container_name,
            command=service.command,
            cpu_limit_percent=service.cpu,
            memory_limit_mb=service.memory,
            image=service.image,
            env=env,
        )

        try:
            container = self._manager.create_container(config)
            self._manager.start_container(container.container_id)
            instance.container = container
            instance.state = ServiceState.RUNNING

            # Start health checker if configured
            if service.health_check and container.pid:
                checker = HealthChecker(
                    container_id=container.container_id,
                    container_pid=container.pid,
                    exec_cmd=service.health_check.exec_cmd,
                    http_url=service.health_check.http_url,
                    tcp_port=service.health_check.tcp_port,
                    interval=service.health_check.interval,
                    timeout=service.health_check.timeout,
                    retries=service.health_check.retries,
                    start_period=service.health_check.start_period,
                    on_status_change=self._on_health_change,
                )
                checker.start()
                instance.health_checker = checker

            logger.info(
                "Started %s (replica %d) as %s [PID %s]",
                service.name, replica_index,
                container.container_id, container.pid,
            )

        except Exception as e:
            instance.state = ServiceState.ERROR
            instance.last_error = str(e)
            logger.error("Failed to start %s (replica %d): %s",
                         service.name, replica_index, e)

        return instance

    def _stop_service(self, service_name: str) -> None:
        """Stop all replicas of a service."""
        with self._lock:
            instances = self._instances.get(service_name, [])

            for inst in instances:
                self._stop_instance(inst)

            self._instances[service_name] = []

    def _stop_instance(self, instance: ServiceInstance) -> None:
        """Stop a single service instance."""
        # Stop health checker
        if instance.health_checker:
            instance.health_checker.stop()

        # Stop container
        if instance.container and instance.container.is_running:
            try:
                instance.container.stop(timeout=10)
                logger.info("Stopped %s", instance.container_name)
            except Exception as e:
                logger.warning("Error stopping %s: %s", instance.container_name, e)

        # Remove container
        if instance.container:
            try:
                self._manager.remove_container(instance.container.container_id)
            except Exception:
                pass

        instance.state = ServiceState.STOPPED

    def _on_health_change(
        self, container_id: str, new_status: HealthStatus
    ) -> None:
        """Callback when a container's health status changes."""
        with self._lock:
            for instances in self._instances.values():
                for inst in instances:
                    if (inst.container
                            and inst.container.container_id == container_id):
                        if new_status == HealthStatus.UNHEALTHY:
                            inst.state = ServiceState.UNHEALTHY
                            logger.warning(
                                "Service %s is unhealthy", inst.container_name
                            )
                        elif new_status == HealthStatus.HEALTHY:
                            inst.state = ServiceState.RUNNING
                            logger.info(
                                "Service %s is healthy", inst.container_name
                            )
                        return

    def _reconcile_loop(self) -> None:
        """Background thread that continuously reconciles desired vs actual state.

        This is the core pattern used by Kubernetes. Every N seconds:
        1. Check what's supposed to be running (desired state from manifest)
        2. Check what's actually running (container status)
        3. Fix any differences (restart crashed, scale up/down)
        """
        while not self._stop_event.is_set():
            try:
                self._reconcile()
            except Exception as e:
                logger.error("Reconciliation error: %s", e)

            self._stop_event.wait(timeout=RECONCILE_INTERVAL)

    def _reconcile(self) -> None:
        """Single reconciliation pass."""
        with self._lock:
            for service_name, service in self.manifest.services.items():
                instances = self._instances.get(service_name, [])

                # Check for crashed containers that need restart
                for inst in instances:
                    if inst.container and not inst.container.is_running:
                        if inst.state == ServiceState.RUNNING:
                            inst.state = ServiceState.STOPPED
                            logger.warning(
                                "Container %s exited unexpectedly",
                                inst.container_name,
                            )

                    # Apply restart policy
                    if inst.state in (
                        ServiceState.STOPPED,
                        ServiceState.ERROR,
                        ServiceState.UNHEALTHY,
                    ):
                        should_restart = (
                            service.restart == "always"
                            or (service.restart == "on-failure"
                                and inst.state != ServiceState.STOPPED)
                        )
                        if should_restart:
                            logger.info(
                                "Restarting %s (policy=%s, state=%s)",
                                inst.container_name,
                                service.restart,
                                inst.state.value,
                            )
                            self._restart_instance(inst, service)

                # Scale up if needed
                running_count = sum(
                    1 for inst in instances
                    if inst.state in (ServiceState.RUNNING, ServiceState.STARTING)
                )

                if running_count < service.replicas:
                    for i in range(running_count, service.replicas):
                        new_inst = self._create_instance(service, i)
                        instances.append(new_inst)
                        logger.info(
                            "Scaled up %s to replica %d", service_name, i
                        )

                # Scale down if needed
                elif running_count > service.replicas:
                    excess = running_count - service.replicas
                    for inst in reversed(instances):
                        if excess <= 0:
                            break
                        if inst.state in (
                            ServiceState.RUNNING, ServiceState.STARTING
                        ):
                            self._stop_instance(inst)
                            instances.remove(inst)
                            excess -= 1
                            logger.info(
                                "Scaled down %s, removed replica %d",
                                service_name, inst.replica_index,
                            )

                self._instances[service_name] = instances

    def _restart_instance(
        self, instance: ServiceInstance, service: ServiceConfig
    ) -> None:
        """Restart a failed service instance."""
        instance.state = ServiceState.RESTARTING
        instance.restart_count += 1

        # Stop and clean up old container
        self._stop_instance(instance)

        # Create new container
        new_inst = self._create_instance(service, instance.replica_index)
        new_inst.restart_count = instance.restart_count

        # Replace in the instance list
        instances = self._instances.get(service.name, [])
        try:
            idx = instances.index(instance)
            instances[idx] = new_inst
        except ValueError:
            instances.append(new_inst)
