"""
Manifest Parser
=================

Parses pycrate.yml manifest files that define multi-container applications.
The manifest format is inspired by Docker Compose but simplified for PyCrate.

Example manifest:
    version: 1
    services:
      web:
        image: alpine:3.20
        command: ["python3", "-m", "http.server", "8080"]
        cpu: 25
        memory: 128
        ports:
          - "8080:8080"
        restart: always
        health_check:
          exec: "wget -qO- http://localhost:8080/health"
          interval: 10
          retries: 3
        env:
          NODE_ENV: production
        depends_on:
          - redis
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.exceptions import PyCrateError

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "pycrate.yml"
SUPPORTED_VERSIONS = {1}


class ManifestError(PyCrateError):
    """Raised when a manifest file is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Manifest error: {message}", code="MANIFEST_ERROR")


@dataclass
class HealthCheckConfig:
    """Health check configuration for a service."""

    exec_cmd: str = ""           # Command to run inside the container
    http_url: str = ""           # HTTP endpoint to probe
    tcp_port: int = 0            # TCP port to probe
    interval: int = 10           # Seconds between checks
    timeout: int = 5             # Seconds before check is considered failed
    retries: int = 3             # Failures before marking unhealthy
    start_period: int = 15       # Grace period after start before checks begin


@dataclass
class PortMapping:
    """Host:container port mapping."""

    host_port: int
    container_port: int
    protocol: str = "tcp"

    @classmethod
    def parse(cls, spec: str) -> PortMapping:
        """Parse a port mapping string like '8080:80' or '8080:80/udp'."""
        protocol = "tcp"
        if "/" in spec:
            spec, protocol = spec.rsplit("/", 1)

        parts = spec.split(":")
        if len(parts) == 2:
            return cls(
                host_port=int(parts[0]),
                container_port=int(parts[1]),
                protocol=protocol,
            )
        elif len(parts) == 1:
            port = int(parts[0])
            return cls(host_port=port, container_port=port, protocol=protocol)
        else:
            raise ManifestError(f"Invalid port mapping: '{spec}'")


@dataclass
class ServiceConfig:
    """Configuration for a single service in the manifest."""

    name: str
    image: str = "alpine:3.20"
    command: list[str] = field(default_factory=lambda: ["/bin/sh"])
    cpu: int = 50
    memory: int = 64
    env: dict[str, str] = field(default_factory=dict)
    ports: list[PortMapping] = field(default_factory=list)
    restart: str = "no"          # "no", "always", "on-failure"
    depends_on: list[str] = field(default_factory=list)
    health_check: HealthCheckConfig | None = None
    replicas: int = 1

    def validate(self) -> None:
        """Validate the service configuration."""
        if not self.name:
            raise ManifestError("Service name cannot be empty")
        if self.cpu < 1 or self.cpu > 100:
            raise ManifestError(
                f"Service '{self.name}': cpu must be 1-100, got {self.cpu}"
            )
        if self.memory < 4:
            raise ManifestError(
                f"Service '{self.name}': memory must be >= 4MB, got {self.memory}"
            )
        if self.restart not in ("no", "always", "on-failure"):
            raise ManifestError(
                f"Service '{self.name}': restart must be 'no', 'always', "
                f"or 'on-failure', got '{self.restart}'"
            )
        if self.replicas < 1:
            raise ManifestError(
                f"Service '{self.name}': replicas must be >= 1, got {self.replicas}"
            )


@dataclass
class Manifest:
    """Parsed pycrate.yml manifest."""

    version: int = 1
    services: dict[str, ServiceConfig] = field(default_factory=dict)
    source_path: Path | None = None

    def get_start_order(self) -> list[str]:
        """Return service names in dependency-resolved order.

        Uses topological sort to ensure services start after their
        dependencies. Raises ManifestError on circular dependencies.
        """
        visited: set[str] = set()
        order: list[str] = []
        visiting: set[str] = set()  # For cycle detection

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ManifestError(
                    f"Circular dependency detected involving service '{name}'"
                )
            visiting.add(name)

            service = self.services.get(name)
            if service is None:
                raise ManifestError(f"Unknown service '{name}' in depends_on")

            for dep in service.depends_on:
                visit(dep)

            visiting.discard(name)
            visited.add(name)
            order.append(name)

        for name in self.services:
            visit(name)

        return order


def parse_manifest(path: Path | str) -> Manifest:
    """Parse a pycrate.yml manifest file.

    Args:
        path: Path to the manifest file.

    Returns:
        Parsed Manifest object.

    Raises:
        ManifestError: If the file is invalid or missing.
    """
    import yaml

    path = Path(path)
    if not path.exists():
        raise ManifestError(f"Manifest file not found: {path}")

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        raise ManifestError(f"Failed to parse YAML: {e}") from e

    if not isinstance(data, dict):
        raise ManifestError("Manifest must be a YAML mapping (dict)")

    return _build_manifest(data, path)


def _build_manifest(data: dict[str, Any], source: Path) -> Manifest:
    """Build a Manifest from parsed YAML data."""
    version = data.get("version", 1)
    if version not in SUPPORTED_VERSIONS:
        raise ManifestError(
            f"Unsupported manifest version: {version}. "
            f"Supported: {SUPPORTED_VERSIONS}"
        )

    services_data = data.get("services", {})
    if not services_data:
        raise ManifestError("Manifest must define at least one service")

    services = {}
    for name, svc_data in services_data.items():
        services[name] = _parse_service(name, svc_data)

    manifest = Manifest(
        version=version,
        services=services,
        source_path=source,
    )

    # Validate dependency references
    for name, svc in manifest.services.items():
        for dep in svc.depends_on:
            if dep not in manifest.services:
                raise ManifestError(
                    f"Service '{name}' depends on unknown service '{dep}'"
                )

    # Validate all services
    for svc in manifest.services.values():
        svc.validate()

    # Test for circular deps
    manifest.get_start_order()

    return manifest


def _parse_service(name: str, data: dict[str, Any]) -> ServiceConfig:
    """Parse a single service definition."""
    if not isinstance(data, dict):
        raise ManifestError(f"Service '{name}' must be a mapping")

    # Parse command (accept string or list)
    command = data.get("command", ["/bin/sh"])
    if isinstance(command, str):
        command = command.split()

    # Parse ports
    ports = []
    for port_spec in data.get("ports", []):
        ports.append(PortMapping.parse(str(port_spec)))

    # Parse health check
    health_check = None
    hc_data = data.get("health_check")
    if hc_data and isinstance(hc_data, dict):
        health_check = HealthCheckConfig(
            exec_cmd=hc_data.get("exec", ""),
            http_url=hc_data.get("http", ""),
            tcp_port=int(hc_data.get("tcp", 0)),
            interval=int(hc_data.get("interval", 10)),
            timeout=int(hc_data.get("timeout", 5)),
            retries=int(hc_data.get("retries", 3)),
            start_period=int(hc_data.get("start_period", 15)),
        )

    # Parse env vars
    env = {}
    env_data = data.get("env", {})
    if isinstance(env_data, dict):
        env = {str(k): str(v) for k, v in env_data.items()}
    elif isinstance(env_data, list):
        for item in env_data:
            if "=" in str(item):
                k, v = str(item).split("=", 1)
                env[k] = v

    return ServiceConfig(
        name=name,
        image=data.get("image", "alpine:3.20"),
        command=command,
        cpu=int(data.get("cpu", 50)),
        memory=int(data.get("memory", 64)),
        env=env,
        ports=ports,
        restart=data.get("restart", "no"),
        depends_on=data.get("depends_on", []),
        health_check=health_check,
        replicas=int(data.get("replicas", 1)),
    )
