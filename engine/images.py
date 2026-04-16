"""
Image Registry & Management
=============================

Centralized image management for PyCrate. Replaces the Alpine-only download
logic with a multi-image system supporting Alpine (HTTP tarball), Ubuntu
(debootstrap), and Debian (debootstrap).

Image storage layout:
    /var/lib/pycrate/images/
        alpine-3.19/           # Extracted Alpine miniroot
        alpine-3.20/
        ubuntu-22.04/          # Debootstrapped Ubuntu
        debian-bookworm/       # Debootstrapped Debian

Each image directory is a complete rootfs that can be used as the read-only
lower layer in an OverlayFS mount.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from engine.exceptions import ImageNotFoundError, RootfsError

logger = logging.getLogger(__name__)

PYCRATE_DATA_DIR = Path(os.environ.get("PYCRATE_DATA_DIR", "/var/lib/pycrate"))
IMAGES_DIR = PYCRATE_DATA_DIR / "images"

ALPINE_MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
ALPINE_ARCH = "x86_64"


# ---------------------------------------------------------------------------
# Image Registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageSpec:
    """Definition of a pullable base image."""

    name: str
    version: str
    method: str          # "http" or "debootstrap"
    url: str = ""        # For HTTP method
    suite: str = ""      # For debootstrap method (e.g., "jammy", "bookworm")
    mirror: str = ""     # For debootstrap method

    @property
    def storage_key(self) -> str:
        """Directory name under IMAGES_DIR."""
        return f"{self.name}-{self.version}"


# All supported images. This is the single source of truth.
IMAGE_REGISTRY: dict[str, dict[str, ImageSpec]] = {
    "alpine": {
        "3.19": ImageSpec(
            name="alpine", version="3.19", method="http",
            url=f"{ALPINE_MIRROR}/v3.19/releases/{ALPINE_ARCH}/alpine-minirootfs-3.19.0-{ALPINE_ARCH}.tar.gz",
        ),
        "3.20": ImageSpec(
            name="alpine", version="3.20", method="http",
            url=f"{ALPINE_MIRROR}/v3.20/releases/{ALPINE_ARCH}/alpine-minirootfs-3.20.0-{ALPINE_ARCH}.tar.gz",
        ),
        "latest": ImageSpec(
            name="alpine", version="3.20", method="http",
            url=f"{ALPINE_MIRROR}/v3.20/releases/{ALPINE_ARCH}/alpine-minirootfs-3.20.0-{ALPINE_ARCH}.tar.gz",
        ),
    },
    "ubuntu": {
        "22.04": ImageSpec(
            name="ubuntu", version="22.04", method="debootstrap",
            suite="jammy", mirror="http://archive.ubuntu.com/ubuntu",
        ),
        "24.04": ImageSpec(
            name="ubuntu", version="24.04", method="debootstrap",
            suite="noble", mirror="http://archive.ubuntu.com/ubuntu",
        ),
        "latest": ImageSpec(
            name="ubuntu", version="24.04", method="debootstrap",
            suite="noble", mirror="http://archive.ubuntu.com/ubuntu",
        ),
    },
    "debian": {
        "bookworm": ImageSpec(
            name="debian", version="bookworm", method="debootstrap",
            suite="bookworm", mirror="http://deb.debian.org/debian",
        ),
        "bullseye": ImageSpec(
            name="debian", version="bullseye", method="debootstrap",
            suite="bullseye", mirror="http://deb.debian.org/debian",
        ),
        "latest": ImageSpec(
            name="debian", version="bookworm", method="debootstrap",
            suite="bookworm", mirror="http://deb.debian.org/debian",
        ),
    },
}

# Default image when none is specified
DEFAULT_IMAGE = "alpine"
DEFAULT_VERSION = "3.20"


def parse_image_ref(ref: str) -> tuple[str, str]:
    """Parse an image reference like 'ubuntu:22.04' into (name, version).

    Supports formats:
        'alpine'         -> ('alpine', 'latest')
        'alpine:3.19'    -> ('alpine', '3.19')
        'ubuntu:22.04'   -> ('ubuntu', '22.04')

    Args:
        ref: Image reference string.

    Returns:
        Tuple of (image_name, version).

    Raises:
        ImageNotFoundError: If the image name is not in the registry.
    """
    if ":" in ref:
        name, version = ref.split(":", 1)
    else:
        name = ref
        version = "latest"

    name = name.lower().strip()
    version = version.strip()

    if name not in IMAGE_REGISTRY:
        available = ", ".join(IMAGE_REGISTRY.keys())
        raise ImageNotFoundError(
            f"Unknown image '{name}'. Available images: {available}"
        )

    if version not in IMAGE_REGISTRY[name]:
        available = ", ".join(
            v for v in IMAGE_REGISTRY[name].keys() if v != "latest"
        )
        raise ImageNotFoundError(
            f"Unknown version '{version}' for image '{name}'. "
            f"Available versions: {available}"
        )

    return name, version


def get_image_spec(name: str, version: str) -> ImageSpec:
    """Look up an ImageSpec from the registry."""
    return IMAGE_REGISTRY[name][version]


def get_image_path(spec: ImageSpec) -> Path:
    """Get the filesystem path where an image's rootfs is stored."""
    return IMAGES_DIR / spec.storage_key


def is_image_cached(spec: ImageSpec) -> bool:
    """Check if an image has already been pulled and cached."""
    image_path = get_image_path(spec)
    # Check for a marker file that indicates a complete pull
    return (image_path / ".pycrate-pulled").exists()


def pull_image(spec: ImageSpec) -> Path:
    """Pull (download/build) a base image and cache it.

    Dispatches to the appropriate pull method based on the image spec.

    Args:
        spec: Image specification from the registry.

    Returns:
        Path to the extracted/built rootfs directory.

    Raises:
        RootfsError: If the pull fails.
    """
    image_path = get_image_path(spec)

    if is_image_cached(spec):
        logger.info("Image %s already cached at %s", spec.storage_key, image_path)
        return image_path

    logger.info("Pulling image %s via %s", spec.storage_key, spec.method)

    if spec.method == "http":
        _pull_http(spec, image_path)
    elif spec.method == "debootstrap":
        _pull_debootstrap(spec, image_path)
    else:
        raise RootfsError(f"Unknown pull method: {spec.method}")

    # Write marker file to indicate successful pull
    (image_path / ".pycrate-pulled").write_text(spec.storage_key)
    logger.info("Image %s pulled successfully to %s", spec.storage_key, image_path)

    return image_path


def _pull_http(spec: ImageSpec, target: Path) -> None:
    """Pull an image via HTTP tarball download (Alpine)."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Download to a temp tarball
    tarball_path = IMAGES_DIR / f"{spec.storage_key}.tar.gz"

    try:
        import urllib.request

        logger.info("Downloading %s", spec.url)
        urllib.request.urlretrieve(spec.url, tarball_path)
        logger.info(
            "Downloaded %s (%d bytes)",
            spec.storage_key,
            tarball_path.stat().st_size,
        )
    except Exception as e:
        tarball_path.unlink(missing_ok=True)
        raise RootfsError(f"Failed to download image from {spec.url}: {e}") from e

    # Extract to the target directory
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=target)
    except Exception as e:
        shutil.rmtree(target, ignore_errors=True)
        raise RootfsError(f"Failed to extract image tarball: {e}") from e
    finally:
        # Remove the tarball to save disk space
        tarball_path.unlink(missing_ok=True)

    _finalize_rootfs(target)


def _pull_debootstrap(spec: ImageSpec, target: Path) -> None:
    """Pull an image via debootstrap (Ubuntu, Debian).

    Requires debootstrap to be installed on the host system.
    Uses --variant=minbase for minimal installation (~150MB).
    """
    # Check if debootstrap is available
    if not shutil.which("debootstrap"):
        raise RootfsError(
            "debootstrap is required for Ubuntu/Debian images but is not installed. "
            "Install it with: sudo apt-get install debootstrap"
        )

    target.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Running debootstrap --variant=minbase %s %s %s",
        spec.suite, target, spec.mirror,
    )

    try:
        result = subprocess.run(
            [
                "debootstrap",
                "--variant=minbase",
                spec.suite,
                str(target),
                spec.mirror,
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for network pulls
        )

        if result.returncode != 0:
            raise RootfsError(
                f"debootstrap failed (exit {result.returncode}):\n{result.stderr}"
            )

        logger.info("debootstrap completed for %s", spec.storage_key)

    except subprocess.TimeoutExpired:
        shutil.rmtree(target, ignore_errors=True)
        raise RootfsError(
            f"debootstrap timed out for {spec.storage_key} "
            "(600s limit, check network connectivity)"
        )
    except Exception as e:
        if not isinstance(e, RootfsError):
            shutil.rmtree(target, ignore_errors=True)
            raise RootfsError(f"debootstrap failed: {e}") from e
        raise

    _finalize_rootfs(target)


def _finalize_rootfs(rootfs: Path) -> None:
    """Common post-pull setup for all images.

    Creates essential directories and files that the engine expects
    to exist in every rootfs, regardless of the base image.
    """
    # Ensure essential directories exist
    for subdir in ["proc", "sys", "dev", "tmp", "root", "oldroot"]:
        (rootfs / subdir).mkdir(exist_ok=True)

    # Set /tmp permissions
    os.chmod(rootfs / "tmp", 0o1777)

    # Write resolv.conf for DNS resolution inside the container
    etc_dir = rootfs / "etc"
    etc_dir.mkdir(exist_ok=True)
    resolv_conf = etc_dir / "resolv.conf"
    resolv_conf.write_text("nameserver 8.8.8.8\nnameserver 8.8.4.4\n")


def list_images() -> list[dict[str, str]]:
    """List all cached images on disk.

    Returns:
        List of dicts with keys: name, version, size_mb, path.
    """
    images = []

    if not IMAGES_DIR.exists():
        return images

    for entry in sorted(IMAGES_DIR.iterdir()):
        if not entry.is_dir():
            continue

        marker = entry / ".pycrate-pulled"
        if not marker.exists():
            continue

        # Parse name-version from directory name
        parts = entry.name.rsplit("-", 1)
        if len(parts) != 2:
            continue

        name, version = parts

        # Calculate size
        total_size = sum(
            f.stat().st_size
            for f in entry.rglob("*")
            if f.is_file()
        )

        images.append({
            "name": name,
            "version": version,
            "size_mb": round(total_size / (1024 * 1024), 1),
            "path": str(entry),
        })

    return images


def remove_image(name: str, version: str) -> None:
    """Remove a cached image from disk.

    Args:
        name: Image name (e.g., "alpine").
        version: Image version (e.g., "3.20").

    Raises:
        ImageNotFoundError: If the image is not cached.
    """
    spec = get_image_spec(name, version)
    image_path = get_image_path(spec)

    if not image_path.exists():
        raise ImageNotFoundError(f"Image {spec.storage_key} is not cached")

    shutil.rmtree(image_path)
    logger.info("Removed image %s", spec.storage_key)
