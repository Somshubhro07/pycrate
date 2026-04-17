"""
Worker Agent — Runs on Each Node
===================================

The agent is a lightweight daemon that:
    1. Registers with the master (POST /api/v1/join)
    2. Polls for assignments (GET /api/v1/assignments/{node_id})
    3. Executes assignments (create/stop containers locally)
    4. Reports state via heartbeats (POST /api/v1/heartbeat)
    5. Acknowledges completed work (POST /api/v1/assignments/ack)

The agent reuses the existing ContainerManager engine for all container
operations. No new container management code — the agent is purely a
network shim that bridges the master's desired state to the local engine.

Resilience:
    - If the master is unreachable, the agent keeps running existing
      containers and retries on next cycle.
    - If the agent crashes, the master detects missing heartbeats and
      reschedules containers to healthy nodes.

Usage:
    agent = Agent(master_url="http://10.0.1.1:9000", node_id="worker-1")
    agent.run()  # Blocks forever (Ctrl+C to stop)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from urllib import request, error

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # Seconds between poll cycles
MAX_RETRY_DELAY = 60  # Max seconds between retries on connection failure


def _get_host_resources() -> tuple[int, int]:
    """Detect local CPU and memory capacity."""
    try:
        cpu_count = os.cpu_count() or 1
        cpu_total = cpu_count * 100

        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return cpu_total, kb // 1024
        return cpu_total, 1024
    except Exception:
        return 100, 1024


def _http_post(url: str, data: dict, timeout: int = 10) -> dict:
    """Simple HTTP POST using urllib (no external deps)."""
    body = json.dumps(data).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_get(url: str, timeout: int = 10) -> dict:
    """Simple HTTP GET using urllib (no external deps)."""
    req = request.Request(url, method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class Agent:
    """PyCrate cluster worker agent.

    Runs in a loop: heartbeat → poll → execute → ack → sleep.
    Uses only stdlib urllib for HTTP, no requests/httpx dependency.
    """

    def __init__(
        self,
        master_url: str,
        node_id: str | None = None,
        port: int = 9001,
    ) -> None:
        self.master_url = master_url.rstrip("/")
        self.node_id = node_id or f"worker-{secrets.token_hex(3)}"
        self.port = port
        self._manager = None  # Lazy-loaded ContainerManager
        self._running = True
        self._consecutive_failures = 0
        self._deployment_map: dict[str, str] = {}  # container_id -> deployment_id

    def run(self) -> None:
        """Main agent loop. Blocks until SIGINT/SIGTERM."""
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # Initialize the container engine
        self._init_engine()

        # Register with master
        self._register()

        logger.info(
            "Agent %s started. Master: %s. Polling every %ds.",
            self.node_id, self.master_url, POLL_INTERVAL,
        )

        while self._running:
            try:
                self._cycle()
                self._consecutive_failures = 0
            except (error.URLError, ConnectionError, OSError) as e:
                self._consecutive_failures += 1
                backoff = min(
                    POLL_INTERVAL * (2 ** self._consecutive_failures),
                    MAX_RETRY_DELAY,
                )
                logger.warning(
                    "Master unreachable (%s), retry in %ds (attempt %d)",
                    e, backoff, self._consecutive_failures,
                )
                time.sleep(backoff)
                continue
            except Exception as e:
                logger.error("Agent cycle error: %s", e, exc_info=True)

            time.sleep(POLL_INTERVAL)

        logger.info("Agent %s shutting down", self.node_id)

    def _init_engine(self) -> None:
        """Initialize the container engine."""
        from engine.container import ContainerManager
        self._manager = ContainerManager(max_containers=20)
        self._manager.initialize()
        logger.info("Container engine initialized")

    def _register(self) -> None:
        """Register this node with the master."""
        cpu_total, memory_total = _get_host_resources()

        try:
            resp = _http_post(f"{self.master_url}/api/v1/join", {
                "node_id": self.node_id,
                "address": f"{self._get_local_ip()}:{self.port}",
                "cpu_total": cpu_total,
                "memory_total": memory_total,
            })
            logger.info(
                "Registered with master %s (master_id=%s)",
                self.master_url, resp.get("master_id", "?"),
            )
        except Exception as e:
            logger.warning(
                "Failed to register with master: %s. Will retry on heartbeat.",
                e,
            )

    def _cycle(self) -> None:
        """One agent cycle: heartbeat → poll → execute → ack."""
        # Step 1: Report current state to master
        self._send_heartbeat()

        # Step 2: Poll for new work
        assignments = self._poll_assignments()

        if not assignments:
            return

        # Step 3: Execute assignments
        completed_ids = []
        for assignment in assignments:
            try:
                self._execute_assignment(assignment)
                completed_ids.append(assignment["assignment_id"])
            except Exception as e:
                logger.error(
                    "Failed to execute assignment %s: %s",
                    assignment.get("assignment_id"), e,
                )

        # Step 4: Acknowledge completed work
        if completed_ids:
            self._ack_assignments(completed_ids)

    def _send_heartbeat(self) -> None:
        """Report local container state and resources to master."""
        containers = []
        cpu_used = 0
        memory_used = 0

        if self._manager:
            for c in self._manager.list_containers():
                container_data = c.to_dict()
                # Inject deployment_id if we have it
                cid = c.container_id
                if cid in self._deployment_map:
                    container_data["deployment_id"] = self._deployment_map[cid]
                containers.append(container_data)

                if c.is_running:
                    cpu_used += c.config.cpu_limit_percent
                    memory_used += c.config.memory_limit_mb

        _http_post(f"{self.master_url}/api/v1/heartbeat", {
            "node_id": self.node_id,
            "containers": containers,
            "resources": {
                "cpu_total": _get_host_resources()[0],
                "cpu_used": cpu_used,
                "memory_total": _get_host_resources()[1],
                "memory_used": memory_used,
            },
        })

    def _poll_assignments(self) -> list[dict]:
        """Poll master for pending work assignments."""
        resp = _http_get(
            f"{self.master_url}/api/v1/assignments/{self.node_id}"
        )
        assignments = resp.get("assignments", [])

        if assignments:
            logger.info(
                "Received %d assignment(s) from master", len(assignments)
            )

        return assignments

    def _execute_assignment(self, assignment: dict) -> None:
        """Execute a single assignment from the master."""
        action = assignment["action"]
        assignment_id = assignment["assignment_id"]

        if action == "create":
            self._create_container(assignment)
        elif action == "stop":
            self._stop_container(assignment)
        else:
            logger.warning(
                "Unknown assignment action '%s' in %s", action, assignment_id
            )

    def _create_container(self, assignment: dict) -> None:
        """Create and start a container per master's assignment."""
        from engine.config import ContainerConfig

        service_name = assignment.get("service_name", "unnamed")
        deployment_id = assignment.get("deployment_id", "")

        logger.info(
            "Creating container for service '%s' (image=%s, cpu=%d, mem=%d)",
            service_name,
            assignment.get("image", "alpine"),
            assignment.get("cpu", 50),
            assignment.get("memory", 64),
        )

        # Build env with cluster metadata
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        env.update(assignment.get("env", {}))
        env["PYCRATE_NODE"] = self.node_id
        env["PYCRATE_SERVICE"] = service_name
        env["PYCRATE_DEPLOYMENT"] = deployment_id

        config = ContainerConfig(
            name=f"{service_name}-{secrets.token_hex(2)}",
            command=assignment.get("command", ["/bin/sh"]),
            cpu_limit_percent=assignment.get("cpu", 50),
            memory_limit_mb=assignment.get("memory", 64),
            image=assignment.get("image", "alpine:3.20"),
            env=env,
        )

        container = self._manager.create_container(config)
        self._manager.start_container(container.container_id)

        # Track which deployment this container belongs to
        self._deployment_map[container.container_id] = deployment_id

        logger.info(
            "Container %s started for %s [PID %s]",
            container.container_id, service_name, container.pid,
        )

    def _stop_container(self, assignment: dict) -> None:
        """Stop a container per master's assignment."""
        container_id = assignment.get("container_id", "")

        if not container_id:
            logger.warning("Stop assignment missing container_id")
            return

        logger.info("Stopping container %s", container_id)

        try:
            self._manager.stop_container(container_id, timeout=10)
            self._manager.remove_container(container_id)
            # Clean up deployment mapping
            self._deployment_map.pop(container_id, None)
        except Exception as e:
            logger.warning("Error stopping container %s: %s", container_id, e)

    def _ack_assignments(self, assignment_ids: list[str]) -> None:
        """Acknowledge completed assignments."""
        _http_post(f"{self.master_url}/api/v1/assignments/ack", {
            "assignment_ids": assignment_ids,
        })
        logger.debug("Acknowledged %d assignment(s)", len(assignment_ids))

    def _get_local_ip(self) -> str:
        """Best-effort detection of local IP address."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _handle_shutdown(self, signum, frame) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False
