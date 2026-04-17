"""
CLI Commands: pycrate machine
================================

Manages the PyCrate Machine — a lightweight Linux VM that lets PyCrate
run containers on Windows and macOS.

Commands:
    pycrate machine init      — Create the machine (download image, generate keys)
    pycrate machine start     — Boot the machine
    pycrate machine stop      — Graceful shutdown
    pycrate machine destroy   — Remove machine and all data
    pycrate machine status    — Show machine state and info
    pycrate machine ssh       — Open a shell inside the machine
"""

from __future__ import annotations

import platform
import sys

import typer
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="machine",
    help="Manage the PyCrate Machine (Linux VM for Windows/macOS).",
    no_args_is_help=True,
)


def _get_console():
    """Lazy import to avoid circular imports."""
    from cli.output import console
    return console


def _get_backend():
    """Initialize the machine backend from config or defaults."""
    from machine.config import MachineConfig
    from machine.backend import get_backend

    if MachineConfig.exists():
        config = MachineConfig.load()
    else:
        config = MachineConfig(backend="auto")

    return get_backend(config), config


@app.command()
def init(
    cpus: int = typer.Option(2, "--cpus", help="Number of virtual CPUs"),
    memory: int = typer.Option(2048, "--memory", help="Memory in MB"),
    disk: int = typer.Option(20, "--disk", help="Disk size in GB"),
    backend: str = typer.Option("auto", "--backend", help="Backend: auto, wsl2, qemu"),
) -> None:
    """Create the PyCrate Machine.

    Downloads a lightweight Alpine Linux image and configures a VM
    for running containers on non-Linux platforms.
    """
    console = _get_console()

    from machine.config import MachineConfig
    from machine.backend import get_backend

    system = platform.system()
    if system == "Linux":
        console.print(
            "[green]✓[/green] Linux detected — no machine needed. "
            "PyCrate runs natively on this platform."
        )
        return

    # Create config
    config = MachineConfig(
        backend=backend,
        cpus=cpus,
        memory_mb=memory,
        disk_gb=disk,
    )

    resolved = MachineConfig.resolve_backend()
    console.print(f"[bold]Platform:[/bold]    {system}")
    console.print(f"[bold]Backend:[/bold]     {resolved}")
    console.print(f"[bold]CPUs:[/bold]        {cpus}")
    console.print(f"[bold]Memory:[/bold]      {memory} MB")
    console.print(f"[bold]Disk:[/bold]        {disk} GB")
    console.print()

    with console.status("[bold cyan]Creating PyCrate Machine...", spinner="dots"):
        be = get_backend(config)
        be.create()
        config.save()

    console.print("\n[bold green]✓ PyCrate Machine created![/bold green]")
    console.print("  Run [cyan]pycrate machine start[/cyan] to boot it.")


@app.command()
def start() -> None:
    """Start the PyCrate Machine."""
    console = _get_console()

    if platform.system() == "Linux":
        console.print("[green]✓[/green] Linux — PyCrate runs natively.")
        return

    be, config = _get_backend()

    with console.status("[bold cyan]Starting PyCrate Machine...", spinner="dots"):
        be.start()

    console.print("[bold green]✓ PyCrate Machine is running![/bold green]")
    console.print(
        "  All [cyan]pycrate[/cyan] commands now execute "
        "inside the Linux VM transparently."
    )


@app.command()
def stop() -> None:
    """Stop the PyCrate Machine."""
    console = _get_console()

    if platform.system() == "Linux":
        console.print("[dim]Nothing to stop — PyCrate runs natively on Linux.[/dim]")
        return

    be, _ = _get_backend()

    with console.status("[bold cyan]Stopping PyCrate Machine...", spinner="dots"):
        be.stop()

    console.print("[bold green]✓ PyCrate Machine stopped.[/bold green]")


@app.command()
def destroy(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove the PyCrate Machine and all its data."""
    console = _get_console()

    if platform.system() == "Linux":
        console.print("[dim]Nothing to destroy — PyCrate runs natively on Linux.[/dim]")
        return

    if not force:
        confirm = typer.confirm(
            "This will delete the VM and all container data inside it. Continue?"
        )
        if not confirm:
            raise typer.Abort()

    be, _ = _get_backend()

    with console.status("[bold red]Destroying PyCrate Machine...", spinner="dots"):
        be.destroy()

    # Remove config file
    from machine.config import CONFIG_FILE
    CONFIG_FILE.unlink(missing_ok=True)

    console.print("[bold green]✓ PyCrate Machine destroyed.[/bold green]")


@app.command()
def status() -> None:
    """Show PyCrate Machine status and resource info."""
    console = _get_console()

    system = platform.system()
    if system == "Linux":
        console.print(
            Panel(
                "[green]● Native Linux[/green]\n"
                "PyCrate runs containers directly on this host.\n"
                "No VM required.",
                title="[bold cyan]PyCrate Machine[/bold cyan]",
                border_style="green",
            )
        )
        return

    be, config = _get_backend()
    info = be.get_info()

    state = info.get("state", "unknown")
    state_color = "green" if state == "running" else "red" if state == "error" else "yellow"

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("Key", style="bold", width=14)
    table.add_column("Value")

    table.add_row("State", f"[{state_color}]● {state}[/{state_color}]")
    table.add_row("Backend", info.get("backend", "unknown"))
    table.add_row("Platform", system)
    table.add_row("Arch", info.get("arch", config.arch))

    if "cpus" in info:
        table.add_row("CPUs", str(info["cpus"]))
    if "memory_mb" in info:
        table.add_row("Memory", f"{info['memory_mb']} MB")
    if "ssh_port" in info:
        table.add_row("SSH Port", str(info["ssh_port"]))
    if "disk_size_mb" in info:
        table.add_row("Disk Used", f"{info['disk_size_mb']} MB")

    console.print(Panel(table, title="[bold cyan]PyCrate Machine[/bold cyan]",
                        border_style="cyan"))


@app.command()
def ssh() -> None:
    """Open a shell inside the PyCrate Machine."""
    console = _get_console()

    if platform.system() == "Linux":
        console.print("[dim]You're already on Linux. No VM to shell into.[/dim]")
        return

    be, config = _get_backend()
    from machine.config import MachineState

    if be.status() != MachineState.RUNNING:
        console.print("[red]Machine is not running.[/red] Start with: pycrate machine start")
        raise typer.Exit(1)

    console.print("[dim]Connecting to PyCrate Machine...[/dim]")
    exit_code = be.exec_stream("/bin/sh -l")
    raise typer.Exit(exit_code)
