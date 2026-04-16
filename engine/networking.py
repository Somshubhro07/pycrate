"""
Container Networking
=====================

Sets up network isolation for containers using virtual ethernet (veth) pairs
and a Linux bridge. This gives each container its own IP address and network
stack while allowing connectivity to the host and external networks.

Network architecture:

    Host namespace                 Container namespace
    ┌─────────────────────┐       ┌──────────────────────┐
    │                     │       │                      │
    │  pycrate0 (bridge)  │       │  eth0 (veth-peer)    │
    │  10.0.0.1/24        │       │  10.0.0.{N}/24       │
    │       │             │       │                      │
    │  veth-{id} ─────────┼───────┼─ (paired)            │
    │                     │       │                      │
    │  eth0 (host NIC)    │       │  default gw 10.0.0.1 │
    │  NAT (iptables)     │       │                      │
    └─────────────────────┘       └──────────────────────┘

Each container gets:
    - A veth pair (one end on host, one end in container)
    - An IP address from the 10.0.0.0/24 subnet
    - A default route through the host bridge (10.0.0.1)
    - NAT via iptables for external connectivity

This is a simplified version of Docker's bridge networking mode.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from engine.exceptions import NetworkError

logger = logging.getLogger(__name__)

BRIDGE_NAME = "pycrate0"
BRIDGE_IP = "10.0.0.1"
BRIDGE_SUBNET = "10.0.0.0/24"
BRIDGE_NETMASK = "24"

# IP allocation range: 10.0.0.2 - 10.0.0.254
# (10.0.0.1 is the bridge, 10.0.0.0 is network, 10.0.0.255 is broadcast)
IP_RANGE_START = 2
IP_RANGE_END = 254


@dataclass
class NetworkConfig:
    """Network configuration assigned to a container."""

    container_ip: str          # e.g., "10.0.0.2"
    bridge_ip: str = BRIDGE_IP
    veth_host: str = ""        # e.g., "veth-a7f3b2" (host side)
    veth_container: str = "eth0"  # Always "eth0" inside the container


def _run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Execute a system command for network configuration.

    Uses subprocess instead of ctypes because networking setup involves
    the `ip` command and `iptables`, which are userspace tools rather
    than raw syscalls.

    Args:
        args: Command and arguments.
        check: Raise on non-zero exit code.

    Returns:
        Completed process result.

    Raises:
        NetworkError: If the command fails.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=check,
            timeout=10,
        )
        return result
    except subprocess.CalledProcessError as e:
        raise NetworkError(
            f"Command failed: {' '.join(args)}\n"
            f"  stdout: {e.stdout}\n"
            f"  stderr: {e.stderr}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise NetworkError(f"Command timed out: {' '.join(args)}") from e


def setup_bridge() -> None:
    """Create the PyCrate network bridge if it doesn't exist.

    The bridge acts as a virtual switch connecting all container veth
    interfaces. It also serves as the default gateway for containers.

    This is called once during engine initialization, not per-container.

    Raises:
        NetworkError: If bridge creation fails.
    """
    # Check if bridge already exists
    result = _run_cmd(["ip", "link", "show", BRIDGE_NAME], check=False)
    if result.returncode == 0:
        logger.debug("Bridge %s already exists", BRIDGE_NAME)
        return

    logger.info("Creating network bridge %s (%s/%s)", BRIDGE_NAME, BRIDGE_IP, BRIDGE_NETMASK)

    # Create the bridge interface
    _run_cmd(["ip", "link", "add", BRIDGE_NAME, "type", "bridge"])

    # Assign IP to the bridge (this is the containers' default gateway)
    _run_cmd(["ip", "addr", "add", f"{BRIDGE_IP}/{BRIDGE_NETMASK}", "dev", BRIDGE_NAME])

    # Bring the bridge up
    _run_cmd(["ip", "link", "set", BRIDGE_NAME, "up"])

    # Enable IP forwarding so packets can flow between containers and host
    _enable_ip_forwarding()

    # Set up NAT so containers can reach the internet
    _setup_nat()


def create_veth_pair(container_id: str, container_pid: int) -> NetworkConfig:
    """Create a veth pair and connect one end to the container's network namespace.

    A veth (virtual ethernet) pair is like a virtual cable with two ends:
    - One end stays in the host namespace, attached to the bridge
    - The other end is moved into the container's network namespace

    Args:
        container_id: Container identifier (used to name the veth interface).
        container_pid: PID of the container process (for namespace reference).

    Returns:
        NetworkConfig with the assigned IP and interface names.

    Raises:
        NetworkError: If veth creation or configuration fails.
    """
    # Generate interface names (max 15 chars for Linux interface names)
    short_id = container_id.replace("crate-", "")[:6]
    veth_host = f"veth-{short_id}"
    veth_container = f"veth-c-{short_id}"

    # Allocate an IP for this container
    container_ip = _allocate_ip(container_id)

    logger.info(
        "Setting up networking for %s: %s (host) <-> eth0 (container), IP %s",
        container_id, veth_host, container_ip,
    )

    # Create the veth pair
    _run_cmd([
        "ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_container,
    ])

    # Attach the host-side veth to the bridge
    _run_cmd(["ip", "link", "set", veth_host, "master", BRIDGE_NAME])
    _run_cmd(["ip", "link", "set", veth_host, "up"])

    # Move the container-side veth into the container's network namespace
    _run_cmd(["ip", "link", "set", veth_container, "netns", str(container_pid)])

    # Configure networking inside the container namespace using nsenter
    # (We run ip commands in the container's network namespace context)
    ns_prefix = ["nsenter", f"--net=/proc/{container_pid}/ns/net", "--"]

    # Rename the interface to eth0 inside the container
    _run_cmd(ns_prefix + ["ip", "link", "set", veth_container, "name", "eth0"])

    # Assign the IP address
    _run_cmd(ns_prefix + ["ip", "addr", "add", f"{container_ip}/{BRIDGE_NETMASK}", "dev", "eth0"])

    # Bring up loopback and eth0
    _run_cmd(ns_prefix + ["ip", "link", "set", "lo", "up"])
    _run_cmd(ns_prefix + ["ip", "link", "set", "eth0", "up"])

    # Set default route through the bridge
    _run_cmd(ns_prefix + ["ip", "route", "add", "default", "via", BRIDGE_IP])

    return NetworkConfig(
        container_ip=container_ip,
        bridge_ip=BRIDGE_IP,
        veth_host=veth_host,
        veth_container="eth0",
    )


def cleanup_networking(container_id: str, veth_host: str) -> None:
    """Remove a container's network interfaces.

    Deleting the host-side veth automatically removes its peer in the
    container namespace.

    Args:
        container_id: Container identifier (for logging).
        veth_host: Host-side veth interface name.
    """
    try:
        _run_cmd(["ip", "link", "delete", veth_host], check=False)
        logger.info("Cleaned up networking for %s", container_id)
    except NetworkError:
        pass  # Interface may already be gone if container crashed


def _allocate_ip(container_id: str) -> str:
    """Allocate an IP address for a container.

    Uses a deterministic approach: hash the container ID to an IP in the
    10.0.0.2-254 range. This avoids needing a persistent IP allocation
    table for a small-scale project.

    For a production runtime, you'd use an IPAM (IP Address Management)
    module with a database-backed allocation table.

    Args:
        container_id: Container identifier.

    Returns:
        IP address string (e.g., "10.0.0.42").
    """
    import hashlib
    hash_val = int(hashlib.sha256(container_id.encode()).hexdigest(), 16)
    host_part = (hash_val % (IP_RANGE_END - IP_RANGE_START + 1)) + IP_RANGE_START
    return f"10.0.0.{host_part}"


def _enable_ip_forwarding() -> None:
    """Enable IPv4 forwarding in the kernel.

    Without this, the kernel drops packets that aren't addressed to the host,
    which would prevent containers from reaching the internet via NAT.
    """
    try:
        Path("/proc/sys/net/ipv4/ip_forward").write_text("1")
        logger.debug("Enabled IP forwarding")
    except OSError as e:
        raise NetworkError(f"Failed to enable IP forwarding: {e}") from e


def _setup_nat() -> None:
    """Configure iptables NAT for container internet access.

    Sets up masquerading (source NAT) so that outgoing packets from the
    container subnet appear to come from the host's public IP.

    This is the same iptables rule Docker creates for bridge networking.
    """
    try:
        # Check if rule already exists
        result = _run_cmd(
            ["iptables", "-t", "nat", "-C", "POSTROUTING", "-s", BRIDGE_SUBNET, "-j", "MASQUERADE"],
            check=False,
        )
        if result.returncode == 0:
            return  # Rule already exists

        _run_cmd([
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-s", BRIDGE_SUBNET,
            "-j", "MASQUERADE",
        ])
        logger.info("NAT configured for subnet %s", BRIDGE_SUBNET)
    except NetworkError as e:
        logger.warning("Could not set up NAT (iptables may not be available): %s", e)
