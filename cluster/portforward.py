"""
Port Forwarding — Host-to-Container DNAT Rules
================================================

Manages iptables DNAT (Destination NAT) rules to expose container ports
externally. When an agent creates a container with port mappings, this
module adds iptables rules that forward traffic from the host port to
the container's internal IP.

Architecture:
    External -> host:8080 -> iptables DNAT -> container 10.0.0.N:80

Rules are:
    1. PREROUTING  (nat table) — rewrite destination for incoming packets
    2. OUTPUT      (nat table) — rewrite for localhost connections
    3. FORWARD     (filter)    — allow forwarded traffic

Cleanup is critical: every add_rule must have a matching remove_rule
when the container stops. The agent calls remove_all_for_container on
container stop/destroy.

Usage:
    from cluster.portforward import PortForwarder

    pf = PortForwarder()
    pf.add_rule("crate-a1b2c3", host_port=8080,
                container_ip="10.0.0.42", container_port=80)
    pf.remove_all_for_container("crate-a1b2c3")
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PortMapping:
    """A single port forwarding rule."""
    container_id: str
    host_port: int
    container_ip: str
    container_port: int
    protocol: str = "tcp"  # "tcp" | "udp"


class PortForwarder:
    """Manages iptables DNAT rules for container port forwarding.

    Thread-safe: each iptables command is atomic. The _active_rules
    list is only modified from the agent's main loop (single thread).
    """

    def __init__(self) -> None:
        self._active_rules: list[PortMapping] = []

    @property
    def active_rules(self) -> list[PortMapping]:
        return list(self._active_rules)

    def add_rule(
        self,
        container_id: str,
        host_port: int,
        container_ip: str,
        container_port: int,
        protocol: str = "tcp",
    ) -> PortMapping:
        """Add a port forwarding rule.

        Creates three iptables rules:
        1. PREROUTING DNAT for external traffic
        2. OUTPUT DNAT for localhost traffic (so `curl localhost:8080` works)
        3. FORWARD ACCEPT for the forwarded packets

        Args:
            container_id: Container this rule belongs to.
            host_port: Port on the host to listen on.
            container_ip: Container's internal IP address.
            container_port: Port inside the container.
            protocol: "tcp" or "udp".

        Returns:
            The created PortMapping.
        """
        mapping = PortMapping(
            container_id=container_id,
            host_port=host_port,
            container_ip=container_ip,
            container_port=container_port,
            protocol=protocol,
        )

        dest = f"{container_ip}:{container_port}"

        # Rule 1: PREROUTING DNAT — catches packets from external sources
        self._iptables([
            "-t", "nat", "-A", "PREROUTING",
            "-p", protocol,
            "--dport", str(host_port),
            "-j", "DNAT",
            "--to-destination", dest,
        ])

        # Rule 2: OUTPUT DNAT — catches packets from localhost
        self._iptables([
            "-t", "nat", "-A", "OUTPUT",
            "-p", protocol,
            "--dport", str(host_port),
            "-j", "DNAT",
            "--to-destination", dest,
        ])

        # Rule 3: FORWARD — allow packets through the filter table
        self._iptables([
            "-A", "FORWARD",
            "-p", protocol,
            "-d", container_ip,
            "--dport", str(container_port),
            "-j", "ACCEPT",
        ])

        self._active_rules.append(mapping)
        logger.info(
            "Port forward: %s:%d -> %s (container %s)",
            protocol, host_port, dest, container_id[:12],
        )

        return mapping

    def remove_rule(self, mapping: PortMapping) -> None:
        """Remove a single port forwarding rule.

        Reverses the three iptables rules added by add_rule.
        """
        dest = f"{mapping.container_ip}:{mapping.container_port}"
        proto = mapping.protocol

        # Remove in reverse order — FORWARD, OUTPUT, PREROUTING
        self._iptables([
            "-D", "FORWARD",
            "-p", proto,
            "-d", mapping.container_ip,
            "--dport", str(mapping.container_port),
            "-j", "ACCEPT",
        ], check=False)

        self._iptables([
            "-t", "nat", "-D", "OUTPUT",
            "-p", proto,
            "--dport", str(mapping.host_port),
            "-j", "DNAT",
            "--to-destination", dest,
        ], check=False)

        self._iptables([
            "-t", "nat", "-D", "PREROUTING",
            "-p", proto,
            "--dport", str(mapping.host_port),
            "-j", "DNAT",
            "--to-destination", dest,
        ], check=False)

        if mapping in self._active_rules:
            self._active_rules.remove(mapping)

        logger.info(
            "Removed port forward: %s:%d -> %s (container %s)",
            proto, mapping.host_port, dest, mapping.container_id[:12],
        )

    def remove_all_for_container(self, container_id: str) -> int:
        """Remove all port forwarding rules for a container.

        Called by the agent when stopping/destroying a container.

        Returns:
            Number of rules removed.
        """
        rules_to_remove = [
            r for r in self._active_rules
            if r.container_id == container_id
        ]

        for rule in rules_to_remove:
            self.remove_rule(rule)

        if rules_to_remove:
            logger.info(
                "Cleaned up %d port forward rule(s) for %s",
                len(rules_to_remove), container_id[:12],
            )

        return len(rules_to_remove)

    def list_rules(self) -> list[dict]:
        """Get a list of active port forwarding rules for display."""
        return [
            {
                "container_id": r.container_id,
                "host_port": r.host_port,
                "container_ip": r.container_ip,
                "container_port": r.container_port,
                "protocol": r.protocol,
                "display": f"{r.host_port}->{r.container_ip}:{r.container_port}/{r.protocol}",
            }
            for r in self._active_rules
        ]

    def _iptables(self, args: list[str], check: bool = True) -> None:
        """Execute an iptables command."""
        cmd = ["iptables"] + args
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check,
                timeout=10,
            )
        except subprocess.CalledProcessError as e:
            if check:
                logger.error(
                    "iptables failed: %s\n  stderr: %s",
                    " ".join(cmd), e.stderr,
                )
                raise
        except FileNotFoundError:
            logger.warning(
                "iptables not found. Port forwarding requires iptables. "
                "Install with: apt-get install iptables"
            )
