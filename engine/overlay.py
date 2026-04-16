"""
OverlayFS Storage Driver
=========================

Copy-on-write filesystem isolation using Linux OverlayFS. This is the same
storage mechanism Docker uses by default on modern Linux systems.

Instead of extracting a full copy of the base image for every container,
OverlayFS layers a writable directory (upperdir) on top of a shared read-only
base image (lowerdir). The container sees a unified filesystem (merged), but
writes only go to its own upper layer.

Benefits:
    - Disk savings: 10 containers from the same image share one copy of the base.
    - Fast creation: No tarball extraction per container, just mkdir + mount.
    - Clean diffs: The upperdir shows exactly what the container changed.

Layout per container:
    /var/lib/pycrate/containers/{id}/
        overlay/
            lower   -> symlink to /var/lib/pycrate/images/{image}/
            upper/  -> writable layer (container's changes)
            work/   -> OverlayFS internal workdir (kernel requirement)
            merged/ -> unified view (this is the container's rootfs)

Requires:
    - Linux kernel 3.18+ with OverlayFS support (standard on Ubuntu 18.04+)
    - The upper and work directories must be on the same filesystem
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from engine.exceptions import RootfsError
from engine.syscalls import mount, umount2, MNT_DETACH

logger = logging.getLogger(__name__)

PYCRATE_DATA_DIR = Path(os.environ.get("PYCRATE_DATA_DIR", "/var/lib/pycrate"))
CONTAINERS_DIR = PYCRATE_DATA_DIR / "containers"


def setup_overlay(container_id: str, image_path: Path) -> Path:
    """Create an OverlayFS mount for a container.

    Args:
        container_id: Unique container identifier.
        image_path: Path to the base image rootfs (becomes lowerdir).

    Returns:
        Path to the merged directory (the container's effective rootfs).

    Raises:
        RootfsError: If the overlay mount fails.
    """
    container_dir = CONTAINERS_DIR / container_id / "overlay"
    lower = image_path
    upper = container_dir / "upper"
    work = container_dir / "work"
    merged = container_dir / "merged"

    # Create all required directories
    for d in [upper, work, merged]:
        d.mkdir(parents=True, exist_ok=True)

    # Create a symlink to the image for reference
    lower_link = container_dir / "lower"
    if not lower_link.exists():
        os.symlink(str(lower), str(lower_link))

    # Construct the OverlayFS mount options
    # lowerdir = read-only base image
    # upperdir = writable container layer
    # workdir  = kernel scratch space (must be on same fs as upper)
    mount_data = (
        f"lowerdir={lower},"
        f"upperdir={upper},"
        f"workdir={work}"
    )

    logger.info(
        "Mounting OverlayFS for %s (lower=%s, upper=%s, merged=%s)",
        container_id, lower, upper, merged,
    )

    try:
        mount(
            "overlay",
            str(merged),
            fstype="overlay",
            data=mount_data,
        )
    except Exception as e:
        raise RootfsError(
            f"OverlayFS mount failed for {container_id}: {e}. "
            "Ensure the kernel supports OverlayFS (modprobe overlay)."
        ) from e

    logger.info("OverlayFS mounted for %s at %s", container_id, merged)

    # Create essential directories in the merged rootfs
    # that the engine expects for pivot_root
    for subdir in ["proc", "sys", "dev", "tmp", "root", "oldroot"]:
        (merged / subdir).mkdir(exist_ok=True)

    return merged


def cleanup_overlay(container_id: str) -> None:
    """Unmount and remove a container's OverlayFS.

    Performs a lazy unmount (MNT_DETACH) to avoid "device busy" errors,
    then removes the overlay directories. The shared base image in
    /var/lib/pycrate/images/ is NOT touched.

    Args:
        container_id: Container whose overlay to clean up.
    """
    container_dir = CONTAINERS_DIR / container_id / "overlay"
    merged = container_dir / "merged"

    # Unmount the overlay
    if merged.exists() and merged.is_mount():
        try:
            umount2(str(merged), MNT_DETACH)
            logger.info("Unmounted OverlayFS for %s", container_id)
        except Exception as e:
            logger.warning(
                "Failed to unmount OverlayFS for %s (may already be unmounted): %s",
                container_id, e,
            )

    # Remove overlay directories (upper, work, merged)
    # but NOT the base image
    import shutil
    overlay_dir = CONTAINERS_DIR / container_id
    if overlay_dir.exists():
        shutil.rmtree(overlay_dir, ignore_errors=True)
        logger.info("Cleaned up overlay directories for %s", container_id)


def get_overlay_diff(container_id: str) -> list[str]:
    """List files that the container has modified or created.

    Reads the upperdir to see what the container changed relative to
    the base image. Useful for debugging and container inspection.

    Args:
        container_id: Container to inspect.

    Returns:
        List of file paths relative to the container root that were
        added or modified.
    """
    upper = CONTAINERS_DIR / container_id / "overlay" / "upper"
    if not upper.exists():
        return []

    changes = []
    for root, _dirs, files in os.walk(upper):
        for f in files:
            full_path = Path(root) / f
            rel_path = full_path.relative_to(upper)
            changes.append(f"/{rel_path}")

    return sorted(changes)
