"""
Health Check System
=====================

Monitors container health via HTTP probes, TCP connections, or exec commands.
Runs in a background thread and reports health status back to the orchestrator.

Health states:
    - starting: Within start_period, checks not yet running
    - healthy: Last N checks passed
    - unhealthy: N consecutive failures exceeded retries threshold

The orchestrator uses health status to trigger restart policies.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Container health states."""
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    NONE = "none"  # No health check configured


@dataclass
class HealthResult:
    """Result of a single health check."""
    passed: bool
    message: str
    duration_ms: float
    timestamp: float = field(default_factory=time.time)


class HealthChecker:
    """Runs periodic health checks for a single container.

    Spawned as a daemon thread by the orchestrator. Calls the on_status_change
    callback when health transitions (healthy -> unhealthy or vice versa).
    """

    def __init__(
        self,
        container_id: str,
        container_pid: int,
        exec_cmd: str = "",
        http_url: str = "",
        tcp_port: int = 0,
        interval: int = 10,
        timeout: int = 5,
        retries: int = 3,
        start_period: int = 15,
        on_status_change: Callable[[str, HealthStatus], None] | None = None,
    ) -> None:
        self.container_id = container_id
        self.container_pid = container_pid
        self.exec_cmd = exec_cmd
        self.http_url = http_url
        self.tcp_port = tcp_port
        self.interval = interval
        self.timeout = timeout
        self.retries = retries
        self.start_period = start_period
        self.on_status_change = on_status_change

        self._status = HealthStatus.STARTING
        self._consecutive_failures = 0
        self._results: list[HealthResult] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = time.time()

    @property
    def status(self) -> HealthStatus:
        return self._status

    @property
    def last_result(self) -> HealthResult | None:
        return self._results[-1] if self._results else None

    def start(self) -> None:
        """Start the health check loop in a background thread."""
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"health-{self.container_id}",
            daemon=True,
        )
        self._thread.start()
        logger.debug("Health checker started for %s", self.container_id)

    def stop(self) -> None:
        """Stop the health check loop."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.timeout + 1)
        logger.debug("Health checker stopped for %s", self.container_id)

    def _run_loop(self) -> None:
        """Main health check loop running in a background thread."""
        # Wait for start period before beginning checks
        elapsed = time.time() - self._started_at
        remaining_start = max(0, self.start_period - elapsed)
        if remaining_start > 0:
            if self._stop_event.wait(timeout=remaining_start):
                return

        while not self._stop_event.is_set():
            result = self._perform_check()
            self._results.append(result)

            # Keep only last 50 results
            if len(self._results) > 50:
                self._results = self._results[-50:]

            old_status = self._status

            if result.passed:
                self._consecutive_failures = 0
                self._status = HealthStatus.HEALTHY
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.retries:
                    self._status = HealthStatus.UNHEALTHY

            # Notify on status change
            if old_status != self._status and self.on_status_change:
                try:
                    self.on_status_change(self.container_id, self._status)
                except Exception as e:
                    logger.warning(
                        "Health status callback failed for %s: %s",
                        self.container_id, e,
                    )

            # Wait for next interval
            if self._stop_event.wait(timeout=self.interval):
                break

    def _perform_check(self) -> HealthResult:
        """Execute one health check based on configured method."""
        start = time.monotonic()

        try:
            if self.http_url:
                passed, message = self._check_http()
            elif self.tcp_port:
                passed, message = self._check_tcp()
            elif self.exec_cmd:
                passed, message = self._check_exec()
            else:
                return HealthResult(
                    passed=True, message="no check configured", duration_ms=0
                )
        except Exception as e:
            passed = False
            message = str(e)

        duration_ms = (time.monotonic() - start) * 1000

        level = logging.DEBUG if passed else logging.WARNING
        logger.log(
            level,
            "Health check %s: %s (%.0fms) - %s",
            self.container_id,
            "PASS" if passed else "FAIL",
            duration_ms,
            message,
        )

        return HealthResult(passed=passed, message=message, duration_ms=duration_ms)

    def _check_http(self) -> tuple[bool, str]:
        """HTTP health check -- GET the URL, expect 2xx response."""
        import urllib.request
        import urllib.error

        try:
            req = urllib.request.Request(self.http_url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                if 200 <= status < 300:
                    return True, f"HTTP {status}"
                return False, f"HTTP {status} (expected 2xx)"
        except urllib.error.URLError as e:
            return False, f"HTTP error: {e.reason}"
        except Exception as e:
            return False, f"HTTP error: {e}"

    def _check_tcp(self) -> tuple[bool, str]:
        """TCP health check -- attempt to connect to the port."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex(("127.0.0.1", self.tcp_port))
            sock.close()

            if result == 0:
                return True, f"TCP port {self.tcp_port} open"
            return False, f"TCP port {self.tcp_port} refused (errno={result})"
        except Exception as e:
            return False, f"TCP error: {e}"

    def _check_exec(self) -> tuple[bool, str]:
        """Exec health check -- run a command via nsenter into the container."""
        try:
            result = subprocess.run(
                [
                    "nsenter",
                    "--target", str(self.container_pid),
                    "--mount", "--pid", "--net",
                    "--", "sh", "-c", self.exec_cmd,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode == 0:
                return True, f"exec exit 0"
            return False, f"exec exit {result.returncode}: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, f"exec timed out ({self.timeout}s)"
        except Exception as e:
            return False, f"exec error: {e}"
