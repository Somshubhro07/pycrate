"""
pycrate run — Create and start a container
=============================================

The primary command for running containers. Combines create + start
into a single operation, similar to `docker run`.

Examples:
    pycrate run alpine /bin/sh
    pycrate run ubuntu:22.04 /bin/bash --name web --cpu 25 --memory 128
    pycrate run alpine:3.19 /bin/sh -c "echo hello" --detach
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import typer

from cli.output import print_error, print_info, print_success

app = typer.Typer()


def _check_root() -> None:
    """Verify we're running as root (required for namespace/cgroup ops)."""
    if os.geteuid() != 0:
        print_error(
            "PyCrate requires root privileges for container operations.\n"
            "  Run with: sudo pycrate run ..."
        )
        raise typer.Exit(1)


def _check_linux() -> None:
    """Verify we're running on Linux."""
    if sys.platform != "linux":
        print_error(
            "PyCrate requires Linux (containers are a Linux kernel feature).\n"
            "  On Windows, use WSL2: wsl --install -d Ubuntu-22.04"
        )
        raise typer.Exit(1)


@app.command()
def run(
    image: str = typer.Argument(
        ...,
        help="Base image to use (e.g., alpine, ubuntu:22.04, debian:bookworm)",
    ),
    command: list[str] = typer.Argument(
        None,
        help="Command to execute inside the container",
    ),
    name: str = typer.Option(
        None, "--name", "-n",
        help="Container name (auto-generated if not provided)",
    ),
    cpu: int = typer.Option(
        50, "--cpu", "-c",
        help="CPU limit as percentage of one core (1-100)",
        min=1, max=100,
    ),
    memory: int = typer.Option(
        64, "--memory", "-m",
        help="Memory limit in megabytes (minimum 4)",
        min=4,
    ),
    detach: bool = typer.Option(
        False, "--detach", "-d",
        help="Run container in background",
    ),
    no_security: bool = typer.Option(
        False, "--no-security",
        help="Disable seccomp and capability hardening (for debugging)",
    ),
    env: list[str] = typer.Option(
        [], "--env", "-e",
        help="Set environment variables (KEY=VALUE)",
    ),
) -> None:
    """Create and start a container from the specified image."""
    _check_linux()
    _check_root()

    from engine.config import ContainerConfig
    from engine.container import ContainerManager

    # Parse command (default to /bin/sh)
    if not command:
        command = ["/bin/sh"]

    # Parse environment variables
    env_dict = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    for item in env:
        if "=" in item:
            key, value = item.split("=", 1)
            env_dict[key] = value
        else:
            print_error(f"Invalid env format: '{item}' (expected KEY=VALUE)")
            raise typer.Exit(1)

    # Generate name if not provided
    if not name:
        import secrets
        name = f"crate-{secrets.token_hex(3)}"

    print_info(f"Pulling image {image}...")

    # Create the config
    config = ContainerConfig(
        name=name,
        command=command,
        cpu_limit_percent=cpu,
        memory_limit_mb=memory,
        image=image,
        env=env_dict,
        security_enabled=not no_security,
    )

    # Initialize the engine and create container
    manager = ContainerManager()
    manager.initialize()

    print_info(f"Creating container {config.container_id}...")
    container = manager.create_container(config)

    print_info(f"Starting container {container.container_id}...")
    manager.start_container(container.container_id)

    print_success(
        f"Container {container.name} ({container.container_id}) is running "
        f"[PID {container.pid}]"
    )

    if detach:
        return

    # Foreground mode: wait for the container to exit
    print_info("Attached to container. Press Ctrl+C to stop.")
    try:
        import time
        while container.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print_info("Stopping container...")
        container.stop()

    exit_code = container.exit_code or 0
    print_info(f"Container exited with code {exit_code}")
    raise typer.Exit(exit_code)
