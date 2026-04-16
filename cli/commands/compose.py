"""
Compose CLI Commands — up, down, status, scale
=================================================

Multi-container application management via pycrate.yml manifests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from cli.output import console, print_error, print_info, print_success

app = typer.Typer()


def _check_prereqs() -> None:
    if sys.platform != "linux":
        print_error("PyCrate requires Linux.")
        raise typer.Exit(1)
    if os.geteuid() != 0:
        print_error("PyCrate requires root privileges.")
        raise typer.Exit(1)


@app.command("up")
def up(
    file: str = typer.Option(
        "pycrate.yml", "--file", "-f",
        help="Path to the manifest file",
    ),
    detach: bool = typer.Option(
        False, "--detach", "-d",
        help="Run in background",
    ),
) -> None:
    """Start all services defined in the manifest."""
    _check_prereqs()

    manifest_path = Path(file)
    if not manifest_path.exists():
        print_error(f"Manifest not found: {manifest_path}")
        print_info("Create a pycrate.yml file or specify one with --file")
        raise typer.Exit(1)

    from orchestrator.manifest import parse_manifest, ManifestError
    from orchestrator.compose import ComposeEngine

    try:
        manifest = parse_manifest(manifest_path)
    except ManifestError as e:
        print_error(str(e))
        raise typer.Exit(1)

    service_count = len(manifest.services)
    total_replicas = sum(s.replicas for s in manifest.services.values())

    print_info(f"Parsed {manifest_path}: {service_count} services, {total_replicas} total replicas")

    start_order = manifest.get_start_order()
    print_info(f"Start order: {' -> '.join(start_order)}")

    engine = ComposeEngine(manifest)

    try:
        with console.status("[bold]Starting services...", spinner="dots"):
            engine.up()

        # Print status table
        _print_status(engine)
        print_success("All services started")

        if detach:
            print_info("Running in background. Use 'pycrate down' to stop.")
            # Store engine reference for later down command
            _save_compose_state(manifest_path)
            return

        # Foreground mode: block until Ctrl+C
        print_info("Press Ctrl+C to stop all services")
        import time
        try:
            while True:
                time.sleep(2)
        except KeyboardInterrupt:
            print_info("Stopping all services...")
            engine.down()
            print_success("All services stopped")

    except Exception as e:
        print_error(f"Failed to start services: {e}")
        engine.down()
        raise typer.Exit(1)


@app.command("down")
def down(
    file: str = typer.Option(
        "pycrate.yml", "--file", "-f",
        help="Path to the manifest file",
    ),
) -> None:
    """Stop all services defined in the manifest."""
    _check_prereqs()

    manifest_path = Path(file)
    if not manifest_path.exists():
        print_error(f"Manifest not found: {manifest_path}")
        raise typer.Exit(1)

    from orchestrator.manifest import parse_manifest, ManifestError
    from orchestrator.compose import ComposeEngine

    try:
        manifest = parse_manifest(manifest_path)
    except ManifestError as e:
        print_error(str(e))
        raise typer.Exit(1)

    engine = ComposeEngine(manifest)

    with console.status("[bold]Stopping services...", spinner="dots"):
        engine.down()

    print_success("All services stopped")


@app.command("status")
def status(
    file: str = typer.Option(
        "pycrate.yml", "--file", "-f",
        help="Path to the manifest file",
    ),
) -> None:
    """Show status of all services."""
    _check_prereqs()

    manifest_path = Path(file)
    if not manifest_path.exists():
        print_error(f"Manifest not found: {manifest_path}")
        raise typer.Exit(1)

    from orchestrator.manifest import parse_manifest, ManifestError
    from orchestrator.compose import ComposeEngine

    try:
        manifest = parse_manifest(manifest_path)
    except ManifestError as e:
        print_error(str(e))
        raise typer.Exit(1)

    engine = ComposeEngine(manifest)
    _print_status(engine)


@app.command("scale")
def scale(
    service: str = typer.Argument(..., help="Service name"),
    replicas: int = typer.Option(..., "--replicas", "-r", help="Number of replicas"),
    file: str = typer.Option(
        "pycrate.yml", "--file", "-f",
        help="Path to the manifest file",
    ),
) -> None:
    """Scale a service to the specified number of replicas."""
    _check_prereqs()

    from orchestrator.manifest import parse_manifest
    from orchestrator.compose import ComposeEngine

    manifest = parse_manifest(Path(file))
    engine = ComposeEngine(manifest)

    try:
        engine.scale(service, replicas)
        print_success(f"Scaled {service} to {replicas} replicas")
    except ValueError as e:
        print_error(str(e))
        raise typer.Exit(1)


def _print_status(engine) -> None:
    """Print a rich table of service status."""
    from rich.table import Table
    from rich import box

    statuses = engine.status()
    if not statuses:
        console.print("[dim]No services running.[/dim]")
        return

    STATE_COLORS = {
        "running": "green",
        "starting": "yellow",
        "stopped": "red",
        "error": "bright_red",
        "unhealthy": "bright_red",
        "restarting": "yellow",
        "pending": "dim",
    }

    table = Table(box=box.SIMPLE_HEAVY, show_edge=False)
    table.add_column("SERVICE", style="cyan", no_wrap=True)
    table.add_column("REPLICA", justify="center", style="dim")
    table.add_column("CONTAINER ID", style="white")
    table.add_column("IMAGE", style="blue")
    table.add_column("STATE", no_wrap=True)
    table.add_column("HEALTH", no_wrap=True)
    table.add_column("RESTARTS", justify="right")
    table.add_column("PID", justify="right", style="dim")

    for s in statuses:
        state = s["state"]
        state_color = STATE_COLORS.get(state, "white")
        health = s["health"]
        health_color = {
            "healthy": "green", "unhealthy": "red",
            "starting": "yellow", "none": "dim",
        }.get(health, "white")

        table.add_row(
            s["service"],
            str(s["replica"]),
            s["container_id"][:12] if s["container_id"] else "",
            s["image"],
            f"[{state_color}]{state}[/{state_color}]",
            f"[{health_color}]{health}[/{health_color}]",
            str(s["restart_count"]),
            str(s["pid"] or ""),
        )

    console.print(table)


def _save_compose_state(manifest_path: Path) -> None:
    """Save the active compose state for later reference."""
    state_file = Path("/var/lib/pycrate/.compose-state")
    state_file.write_text(str(manifest_path.absolute()))
