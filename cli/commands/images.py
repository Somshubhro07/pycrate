"""
Image management commands — pull, images, rmi
================================================

Pull base images and manage the local image cache.
"""

from __future__ import annotations

import typer

from cli.output import (
    console,
    print_error,
    print_images_table,
    print_info,
    print_success,
)

app = typer.Typer()


@app.command("pull")
def pull(
    image_ref: str = typer.Argument(
        ...,
        help="Image to pull (e.g., alpine, ubuntu:22.04, debian:bookworm)",
    ),
) -> None:
    """Pull a base image and cache it locally."""
    from engine.images import get_image_spec, parse_image_ref, pull_image, is_image_cached

    try:
        name, version = parse_image_ref(image_ref)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)

    spec = get_image_spec(name, version)

    if is_image_cached(spec):
        print_info(f"Image {spec.storage_key} is already cached")
        return

    print_info(f"Pulling {spec.storage_key} via {spec.method}...")

    if spec.method == "debootstrap":
        print_info(
            "Using debootstrap (this may take a few minutes on first pull)..."
        )

    try:
        with console.status(f"[bold]Pulling {spec.storage_key}...", spinner="dots"):
            path = pull_image(spec)
        print_success(f"Image {spec.storage_key} pulled to {path}")
    except Exception as e:
        print_error(f"Failed to pull image: {e}")
        raise typer.Exit(1)


@app.command("images")
def list_cached_images() -> None:
    """List all cached images."""
    from engine.images import list_images

    images = list_images()
    print_images_table(images)


@app.command("rmi")
def remove_image(
    image_ref: str = typer.Argument(
        ...,
        help="Image to remove (e.g., alpine:3.20, ubuntu:22.04)",
    ),
) -> None:
    """Remove a cached image from disk."""
    from engine.images import parse_image_ref
    from engine.images import remove_image as _remove_image

    try:
        name, version = parse_image_ref(image_ref)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)

    try:
        _remove_image(name, version)
        print_success(f"Image {name}-{version} removed")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)
