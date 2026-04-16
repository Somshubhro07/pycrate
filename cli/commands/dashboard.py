"""
pycrate dashboard — Launch the web dashboard
================================================

Starts the FastAPI server and opens the dashboard in the default browser.
The server runs in the foreground until Ctrl+C.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

import typer

from cli.output import console, print_error, print_info, print_success

app = typer.Typer()


@app.command()
def dashboard(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser"),
) -> None:
    """Start the PyCrate API server and open the dashboard."""
    if sys.platform != "linux":
        print_error("PyCrate requires Linux. Use WSL2 on Windows.")
        raise typer.Exit(1)

    if os.geteuid() != 0:
        print_error("Dashboard mode requires root (for container engine).")
        raise typer.Exit(1)

    print_info(f"Starting PyCrate API server on {host}:{port}")

    # Open browser after a short delay to let the server start
    if not no_browser:
        def _open_browser():
            time.sleep(2)
            url = f"http://localhost:{port}/docs"
            print_info(f"Opening dashboard at {url}")
            webbrowser.open(url)

        browser_thread = threading.Thread(target=_open_browser, daemon=True)
        browser_thread.start()

    # Start uvicorn synchronously (blocks until Ctrl+C)
    try:
        import uvicorn
        print_success(f"PyCrate dashboard running at http://localhost:{port}")
        print_info("Press Ctrl+C to stop")

        uvicorn.run(
            "api.main:app",
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
    except KeyboardInterrupt:
        print_info("Shutting down...")
    except ImportError:
        print_error(
            "uvicorn not installed. Install with: pip install pycrate[server]"
        )
        raise typer.Exit(1)
