"""
Container management commands — ps, stop, rm, logs, inspect
==============================================================

Lifecycle commands that operate on existing containers.
"""

from __future__ import annotations

import os
import sys

import typer

from cli.output import (
    console,
    print_container_detail,
    print_container_table,
    print_error,
    print_info,
    print_success,
)

app = typer.Typer()

# Shared manager instance (initialized lazily)
_manager = None


def _get_manager():
    """Get or create the ContainerManager singleton."""
    global _manager
    if _manager is None:
        if sys.platform != "linux":
            print_error("PyCrate requires Linux.")
            raise typer.Exit(1)
        if os.geteuid() != 0:
            print_error("PyCrate requires root privileges.")
            raise typer.Exit(1)

        from engine.container import ContainerManager
        _manager = ContainerManager()
        _manager.initialize()
    return _manager


@app.command("ps")
def list_containers(
    all_containers: bool = typer.Option(
        True, "--all", "-a",
        help="Show all containers (including stopped)",
    ),
    status: str = typer.Option(
        None, "--status", "-s",
        help="Filter by status: created, running, stopped, error",
    ),
) -> None:
    """List containers."""
    manager = _get_manager()

    from engine.container import ContainerStatus

    status_filter = None
    if status:
        try:
            status_filter = ContainerStatus(status)
        except ValueError:
            print_error(f"Invalid status: '{status}'. Use: created, running, stopped, error")
            raise typer.Exit(1)

    containers = manager.list_containers(status_filter=status_filter)
    container_dicts = [c.to_dict() for c in containers]
    print_container_table(container_dicts)


@app.command("stop")
def stop_container(
    container_id: str = typer.Argument(..., help="Container ID or name"),
    timeout: int = typer.Option(10, "--timeout", "-t", help="Seconds before SIGKILL"),
) -> None:
    """Stop a running container."""
    manager = _get_manager()
    container = _find_container(manager, container_id)

    print_info(f"Stopping {container.name} ({container.container_id})...")
    try:
        container.stop(timeout=timeout)
        print_success(f"Container {container.name} stopped")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@app.command("rm")
def remove_container(
    container_id: str = typer.Argument(..., help="Container ID or name"),
    force: bool = typer.Option(False, "--force", "-f", help="Force remove (stop if running)"),
) -> None:
    """Remove a container and its resources."""
    manager = _get_manager()
    container = _find_container(manager, container_id)

    if container.is_running and not force:
        print_error(
            f"Container {container.name} is still running. "
            "Use --force to stop and remove."
        )
        raise typer.Exit(1)

    print_info(f"Removing {container.name} ({container.container_id})...")
    manager.remove_container(container.container_id)
    print_success(f"Container {container.name} removed")


@app.command("logs")
def container_logs(
    container_id: str = typer.Argument(..., help="Container ID or name"),
    tail: int = typer.Option(None, "--tail", "-n", help="Number of lines to show"),
) -> None:
    """View container logs."""
    manager = _get_manager()
    container = _find_container(manager, container_id)

    logs = container.get_logs(tail=tail)
    if not logs:
        console.print("[dim]No logs available.[/dim]")
        return

    for line in logs:
        console.print(line, highlight=False)


@app.command("inspect")
def inspect_container(
    container_id: str = typer.Argument(..., help="Container ID or name"),
) -> None:
    """Show detailed container information."""
    manager = _get_manager()
    container = _find_container(manager, container_id)
    print_container_detail(container.to_dict())


def _find_container(manager, identifier: str):
    """Find a container by ID or name."""
    # Try exact ID match first
    try:
        return manager.get_container(identifier)
    except Exception:
        pass

    # Try name match
    for c in manager.list_containers():
        if c.name == identifier:
            return c

    # Try prefix match on ID
    for c in manager.list_containers():
        if c.container_id.startswith(identifier):
            return c

    print_error(f"Container '{identifier}' not found")
    raise typer.Exit(1)
