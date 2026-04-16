"""
PyCrate CLI
============

Command-line interface for the PyCrate container runtime.
Provides a Docker-like experience for managing containers directly
from the terminal without requiring an API server.

Usage:
    pycrate run alpine /bin/sh --name web --cpu 50 --memory 64
    pycrate ps
    pycrate stop web
    pycrate rm web
    pycrate pull ubuntu:22.04
    pycrate images
    pycrate dashboard
"""

from __future__ import annotations

import typer

from cli.commands import containers, dashboard, images, run

__version__ = "0.2.0"

app = typer.Typer(
    name="pycrate",
    help="PyCrate -- A container runtime built from scratch in Python.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=True,
)

# Register command groups
app.add_typer(run.app, name="run", help="Create and start a container")
app.add_typer(containers.app, name="containers", hidden=True)
app.add_typer(images.app, name="image", help="Manage images")
app.add_typer(dashboard.app, name="dashboard", help="Launch the web dashboard")

# Promote frequently-used commands to top level
# so users can type `pycrate ps` instead of `pycrate containers ps`
app.command("ps")(containers.list_containers)
app.command("stop")(containers.stop_container)
app.command("rm")(containers.remove_container)
app.command("logs")(containers.container_logs)
app.command("inspect")(containers.inspect_container)
app.command("pull")(images.pull)
app.command("images")(images.list_cached_images)


@app.command()
def version() -> None:
    """Show PyCrate version and engine information."""
    from rich.panel import Panel
    from cli.output import console

    info_lines = [
        f"[bold]PyCrate[/bold]  v{__version__}",
        f"[bold]Engine[/bold]   v{__version__}",
        "",
        "[dim]Container runtime built from scratch in Python[/dim]",
        "[dim]using Linux kernel primitives via ctypes.[/dim]",
        "",
        "[bold]Kernel features:[/bold]",
        "  Namespaces   clone(), unshare(), setns()",
        "  cgroups v2   CPU + memory limits",
        "  Filesystem   OverlayFS + pivot_root",
        "  Networking   veth pairs + bridge",
        "  Security     seccomp BPF + capability dropping",
    ]

    panel = Panel(
        "\n".join(info_lines),
        title="[bold cyan]PyCrate[/bold cyan]",
        border_style="cyan",
    )
    console.print(panel)


def main() -> None:
    """Entry point for the `pycrate` console script."""
    app()


if __name__ == "__main__":
    main()
