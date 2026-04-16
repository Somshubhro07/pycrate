"""
PyCrate CLI — Output Formatting
=================================

Rich terminal output utilities for the CLI. Provides consistent, readable
formatting for container listings, status displays, and progress indicators.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()
error_console = Console(stderr=True)

# Status color mapping
STATUS_COLORS = {
    "created": "yellow",
    "running": "green",
    "stopped": "red",
    "error": "bright_red",
}


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    error_console.print(f"[bold red]error:[/bold red] {message}")


def print_warning(message: str) -> None:
    """Print a warning message to stderr."""
    error_console.print(f"[bold yellow]warning:[/bold yellow] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[bold green]ok:[/bold green] {message}")


def print_info(message: str) -> None:
    """Print an informational message."""
    console.print(f"[dim]>[/dim] {message}")


def format_status(status: str) -> str:
    """Format a status string with color markup."""
    color = STATUS_COLORS.get(status, "white")
    return f"[{color}]{status}[/{color}]"


def print_container_table(containers: list[dict]) -> None:
    """Print a formatted table of containers."""
    if not containers:
        console.print("[dim]No containers found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAVY, show_edge=False)
    table.add_column("CONTAINER ID", style="cyan", no_wrap=True)
    table.add_column("NAME", style="white")
    table.add_column("IMAGE", style="blue")
    table.add_column("STATUS", no_wrap=True)
    table.add_column("CPU", justify="right", style="yellow")
    table.add_column("MEMORY", justify="right", style="yellow")
    table.add_column("PID", justify="right", style="dim")

    for c in containers:
        status = c.get("status", "unknown")
        color = STATUS_COLORS.get(status, "white")

        table.add_row(
            c.get("container_id", ""),
            c.get("name", ""),
            c.get("image", ""),
            f"[{color}]{status}[/{color}]",
            f"{c.get('config', {}).get('cpu_limit_percent', '')}%",
            f"{c.get('config', {}).get('memory_limit_mb', '')}MB",
            str(c.get("pid", "") or ""),
        )

    console.print(table)


def print_images_table(images: list[dict]) -> None:
    """Print a formatted table of cached images."""
    if not images:
        console.print("[dim]No images cached. Run: pycrate pull <image>[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAVY, show_edge=False)
    table.add_column("IMAGE", style="cyan")
    table.add_column("VERSION", style="white")
    table.add_column("SIZE", justify="right", style="yellow")
    table.add_column("PATH", style="dim")

    for img in images:
        table.add_row(
            img["name"],
            img["version"],
            f"{img['size_mb']}MB",
            img["path"],
        )

    console.print(table)


def print_container_detail(container: dict) -> None:
    """Print detailed container information."""
    status = container.get("status", "unknown")
    color = STATUS_COLORS.get(status, "white")

    lines = []
    lines.append(f"[bold]Container:[/bold]  {container.get('container_id', '')}")
    lines.append(f"[bold]Name:[/bold]       {container.get('name', '')}")
    lines.append(f"[bold]Image:[/bold]      {container.get('image', '')}")
    lines.append(f"[bold]Status:[/bold]     [{color}]{status}[/{color}]")
    lines.append(f"[bold]PID:[/bold]        {container.get('pid', 'none')}")

    config = container.get("config", {})
    lines.append(f"[bold]CPU Limit:[/bold]  {config.get('cpu_limit_percent', '')}%")
    lines.append(f"[bold]Memory:[/bold]     {config.get('memory_limit_mb', '')}MB")
    lines.append(f"[bold]Command:[/bold]    {' '.join(config.get('command', []))}")

    if container.get("error"):
        lines.append(f"[bold red]Error:[/bold red]      {container['error']}")

    if container.get("network"):
        net = container["network"]
        lines.append(f"[bold]IP:[/bold]         {net.get('ip_address', '')}")

    lines.append(f"[bold]Created:[/bold]    {container.get('created_at', '')}")
    if container.get("started_at"):
        lines.append(f"[bold]Started:[/bold]    {container['started_at']}")
    if container.get("stopped_at"):
        lines.append(f"[bold]Stopped:[/bold]    {container['stopped_at']}")

    panel = Panel(
        "\n".join(lines),
        title=f"[bold]{container.get('name', 'container')}[/bold]",
        border_style="cyan",
    )
    console.print(panel)
