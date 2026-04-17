"""
Cluster CLI Commands
======================

Commands for multi-node cluster management:
    pycrate cluster init          — Initialize a master node
    pycrate cluster join <url>    — Join a worker to the cluster
    pycrate cluster nodes         — List cluster nodes
    pycrate cluster status        — Full cluster state
    pycrate deploy                — Deploy a service to the cluster
    pycrate undeploy              — Remove a deployment
    pycrate scale                 — Scale a service
    pycrate rollout               — Rolling update (new image)
"""

from __future__ import annotations

import os
import sys
import time
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


# ---------------------------------------------------------------------------
# Cluster management
# ---------------------------------------------------------------------------

@app.command("init")
def cluster_init(
    port: int = typer.Option(
        9000, "--port", "-p",
        help="Port for the master API server",
    ),
    node_id: str = typer.Option(
        None, "--node-id",
        help="Custom node ID (auto-generated if not set)",
    ),
) -> None:
    """Initialize this machine as a cluster master node."""
    _check_prereqs()

    from cluster.master import MasterNode

    print_success("Initializing PyCrate cluster master")
    print_info(f"API server starting on port {port}")
    print_info(f"Workers can join with: pycrate cluster join http://<this-ip>:{port}")
    print_info("Press Ctrl+C to stop")

    master = MasterNode(host="0.0.0.0", port=port, node_id=node_id)
    master.start()


@app.command("join")
def cluster_join(
    master_url: str = typer.Argument(
        ...,
        help="Master node URL (e.g., http://10.0.1.1:9000)",
    ),
    node_id: str = typer.Option(
        None, "--node-id",
        help="Custom node ID (auto-generated if not set)",
    ),
) -> None:
    """Join this machine as a worker node."""
    _check_prereqs()

    from cluster.agent import Agent

    print_success(f"Joining cluster at {master_url}")
    print_info("This node will poll for work assignments every 5 seconds")
    print_info("Press Ctrl+C to leave the cluster")

    agent = Agent(master_url=master_url, node_id=node_id)
    agent.run()


@app.command("nodes")
def cluster_nodes(
    master_url: str = typer.Option(
        "http://localhost:9000", "--master", "-m",
        help="Master node URL",
    ),
) -> None:
    """List all nodes in the cluster."""
    import json
    from urllib import request as urllib_request

    try:
        req = urllib_request.Request(f"{master_url}/api/v1/nodes")
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Cannot reach master: {e}")
        raise typer.Exit(1)

    from rich.table import Table
    from rich import box

    table = Table(
        title="Cluster Nodes",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
    )
    table.add_column("NODE ID", style="cyan", no_wrap=True)
    table.add_column("ADDRESS", style="white")
    table.add_column("ROLE", style="blue")
    table.add_column("STATUS", no_wrap=True)
    table.add_column("CPU", justify="right")
    table.add_column("MEMORY", justify="right")
    table.add_column("HEARTBEAT", style="dim")

    for node in data.get("nodes", []):
        status = node["status"]
        status_color = "green" if status == "healthy" else "red"
        hb = node.get("last_heartbeat", 0)
        ago = f"{int(time.time() - hb)}s ago" if hb else "never"

        table.add_row(
            node["node_id"],
            node["address"],
            node["role"],
            f"[{status_color}]{status}[/{status_color}]",
            node["cpu"],
            node["memory"],
            ago,
        )

    console.print(table)


@app.command("status")
def cluster_status(
    master_url: str = typer.Option(
        "http://localhost:9000", "--master", "-m",
        help="Master node URL",
    ),
) -> None:
    """Show full cluster status."""
    import json
    from urllib import request as urllib_request

    try:
        req = urllib_request.Request(f"{master_url}/api/v1/state")
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Cannot reach master: {e}")
        raise typer.Exit(1)

    from rich.panel import Panel
    from rich.columns import Columns

    nodes = data.get("nodes", {})
    deploys = data.get("deployments", {})
    containers = data.get("containers", {})
    rec = data.get("reconciler", {})

    # Summary
    summary = (
        f"[bold]Master:[/bold] {data.get('master_id', '?')}\n"
        f"[bold]Nodes:[/bold] {nodes.get('healthy', 0)} healthy / {nodes.get('total', 0)} total\n"
        f"[bold]Deployments:[/bold] {deploys.get('total', 0)}\n"
        f"[bold]Containers:[/bold] {containers.get('running', 0)} running / {containers.get('total', 0)} total\n"
        f"[bold]Reconciler:[/bold] pass #{rec.get('pass_count', 0)}, {rec.get('last_pass_ms', 0)}ms/pass"
    )
    console.print(Panel(summary, title="[bold cyan]Cluster Status[/bold cyan]", border_style="cyan"))

    # Deployments table
    from rich.table import Table
    from rich import box

    if deploys.get("list"):
        dtable = Table(
            title="Deployments",
            box=box.SIMPLE_HEAVY,
            show_edge=False,
        )
        dtable.add_column("SERVICE", style="cyan")
        dtable.add_column("IMAGE", style="blue")
        dtable.add_column("DESIRED", justify="center")
        dtable.add_column("RUNNING", justify="center")
        dtable.add_column("STATUS", no_wrap=True)

        for d in deploys["list"]:
            desired = d.get("replicas", 0)
            running = d.get("running", 0)
            if running >= desired:
                status = f"[green]✓ ready[/green]"
            elif running > 0:
                status = f"[yellow]⟳ scaling ({running}/{desired})[/yellow]"
            else:
                status = f"[red]✗ pending[/red]"

            dtable.add_row(
                d["service"],
                d["image"],
                str(desired),
                str(running),
                status,
            )

        console.print(dtable)


# ---------------------------------------------------------------------------
# Deployment commands (top-level)
# ---------------------------------------------------------------------------

deploy_app = typer.Typer()


@deploy_app.command("create")
def deploy_create(
    service: str = typer.Argument(..., help="Service name"),
    image: str = typer.Option("alpine:3.20", "--image", "-i", help="Container image"),
    command: str = typer.Option("/bin/sh", "--command", "-c", help="Command to run"),
    replicas: int = typer.Option(1, "--replicas", "-r", help="Number of replicas"),
    cpu: int = typer.Option(50, "--cpu", help="CPU limit per replica (%)"),
    memory: int = typer.Option(64, "--memory", "-m", help="Memory limit per replica (MB)"),
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """Deploy a service to the cluster."""
    import json
    from urllib import request as urllib_request

    cmd_list = command.split()

    body = json.dumps({
        "service_name": service,
        "image": image,
        "command": cmd_list,
        "replicas": replicas,
        "cpu": cpu,
        "memory": memory,
        "restart": "always",
    }).encode()

    try:
        req = urllib_request.Request(
            f"{master_url}/api/v1/deploy",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Deploy failed: {e}")
        raise typer.Exit(1)

    print_success(
        f"Deployed {service} ({replicas} replicas of {image})\n"
        f"  Deployment ID: {data.get('deployment_id', '?')}"
    )


@deploy_app.command("rm")
def deploy_remove(
    service: str = typer.Argument(..., help="Service name to remove"),
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """Remove a deployment from the cluster."""
    import json
    from urllib import request as urllib_request

    try:
        req = urllib_request.Request(
            f"{master_url}/api/v1/deploy/{service}",
            method="DELETE",
        )
        with urllib_request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read())
    except Exception as e:
        print_error(f"Undeploy failed: {e}")
        raise typer.Exit(1)

    print_success(f"Deployment {service} deleted. Containers will be stopped.")


@deploy_app.command("scale")
def deploy_scale(
    service: str = typer.Argument(..., help="Service name"),
    replicas: int = typer.Option(..., "--replicas", "-r", help="Target replica count"),
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """Scale a deployment to the specified replica count."""
    import json
    from urllib import request as urllib_request

    body = json.dumps({"replicas": replicas}).encode()

    try:
        req = urllib_request.Request(
            f"{master_url}/api/v1/deploy/{service}/scale",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib_request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read())
    except Exception as e:
        print_error(f"Scale failed: {e}")
        raise typer.Exit(1)

    print_success(f"Scaled {service} to {replicas} replicas")


@deploy_app.command("ls")
def deploy_list(
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """List all deployments."""
    import json
    from urllib import request as urllib_request

    try:
        req = urllib_request.Request(f"{master_url}/api/v1/deployments")
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Cannot reach master: {e}")
        raise typer.Exit(1)

    from rich.table import Table
    from rich import box

    table = Table(box=box.SIMPLE_HEAVY, show_edge=False)
    table.add_column("SERVICE", style="cyan")
    table.add_column("IMAGE", style="blue")
    table.add_column("REPLICAS", justify="center")
    table.add_column("CPU", justify="right")
    table.add_column("MEMORY", justify="right")

    for d in data.get("deployments", []):
        table.add_row(
            d["service_name"],
            d["image"],
            str(d["replicas"]),
            f"{d['cpu']}%",
            f"{d['memory']}MB",
        )

    console.print(table)


@deploy_app.command("rollout")
def deploy_rollout(
    service: str = typer.Argument(..., help="Service name"),
    image: str = typer.Option(..., "--image", "-i", help="New container image"),
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """Perform a rolling update to a new image."""
    import json
    from urllib import request as urllib_request

    body = json.dumps({"image": image}).encode()

    try:
        req = urllib_request.Request(
            f"{master_url}/api/v1/rollout/{service}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Rollout failed: {e}")
        raise typer.Exit(1)

    print_success(
        f"Rolling update started for {service}\n"
        f"  New image: {data.get('new_image', image)}\n"
        f"  Monitor with: pycrate deploy rollout-status {service}"
    )


@deploy_app.command("rollout-status")
def deploy_rollout_status(
    service: str = typer.Argument(..., help="Service name"),
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """Check the status of a rolling update."""
    import json
    from urllib import request as urllib_request

    try:
        req = urllib_request.Request(
            f"{master_url}/api/v1/rollout/{service}",
        )
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Cannot reach master: {e}")
        raise typer.Exit(1)

    if data.get("status") == "no_active_rollout":
        print_info(f"No active rollout for {service}")
        return

    from rich.panel import Panel

    state_colors = {
        "in_progress": "yellow",
        "completed": "green",
        "failed": "red",
    }
    state = data.get("state", "unknown")
    color = state_colors.get(state, "white")

    info = (
        f"[bold]Service:[/bold] {data.get('service_name', '?')}\n"
        f"[bold]State:[/bold] [{color}]{state}[/{color}]\n"
        f"[bold]Progress:[/bold] {data.get('updated', 0)}/{data.get('total', 0)} updated\n"
        f"[bold]Image:[/bold] {data.get('old_image', '?')} → {data.get('new_image', '?')}\n"
        f"[bold]Duration:[/bold] {data.get('duration_seconds', 0)}s"
    )
    console.print(Panel(info, title="[bold cyan]Rollout Status[/bold cyan]", border_style="cyan"))

    events = data.get("events", [])
    if events:
        console.print("\n[bold]Recent Events:[/bold]")
        for event in events:
            console.print(f"  [dim]{event}[/dim]")


@deploy_app.command("events")
def deploy_events(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of events"),
    master_url: str = typer.Option(
        "http://localhost:9000", "--master",
        help="Master node URL",
    ),
) -> None:
    """Show recent cluster events."""
    import json
    from urllib import request as urllib_request

    try:
        req = urllib_request.Request(
            f"{master_url}/api/v1/events?limit={limit}",
        )
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print_error(f"Cannot reach master: {e}")
        raise typer.Exit(1)

    from rich.table import Table
    from rich import box
    from datetime import datetime

    table = Table(
        title="Cluster Events",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
    )
    table.add_column("TIME", style="dim", no_wrap=True)
    table.add_column("TYPE", style="cyan")
    table.add_column("NODE", style="blue")
    table.add_column("MESSAGE")

    for event in data.get("events", []):
        ts = event.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "?"

        table.add_row(
            time_str,
            event.get("event_type", "?"),
            event.get("node_id", "") or "-",
            event.get("message", ""),
        )

    console.print(table)
