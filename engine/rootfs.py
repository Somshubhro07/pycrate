"""
Root Filesystem Management
============================

Sets up the container's isolated filesystem. Each container gets its own
root directory tree based on Alpine Linux's miniroot tarball (~3MB).

The process:
    1. Download Alpine miniroot tarball (cached after first pull)
    2. Extract to /var/lib/pycrate/containers/{id}/rootfs/
    3. Mount essential kernel filesystems (/proc, /sys, /dev)
    4. pivot_root() — swap the process's root to the new rootfs
    5. Unmount the old root — container can no longer access host filesystem

After this, the container process sees:
    /           <- Alpine Linux root (was /var/lib/pycrate/containers/{id}/rootfs/)
    /proc       <- Container's own process list
    /sys        <- Kernel sysfs (read-only)
    /dev        <- Device nodes
    /etc        <- Alpine's /etc
    /bin, /sbin <- BusyBox-based utilities

The host filesystem is completely inaccessible.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
from pathlib import Path

from engine.exceptions import ImageNotFoundError, RootfsError
from engine.syscalls import (
    MNT_DETACH,
    MS_BIND,
    MS_NODEV,
    MS_NOEXEC,
    MS_NOSUID,
    MS_PRIVATE,
    MS_RDONLY,
    MS_REC,
    mount,
    pivot_root,
    umount2,
)

logger = logging.getLogger(__name__)

# Where PyCrate stores container data on the host filesystem
PYCRATE_DATA_DIR = Path("/var/lib/pycrate")
CONTAINERS_DIR = PYCRATE_DATA_DIR / "containers"
IMAGES_DIR = PYCRATE_DATA_DIR / "images"

# Alpine miniroot tarball URL template
# Architecture is x86_64 for EC2 instances
ALPINE_MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
ALPINE_ARCH = "x86_64"


def get_alpine_url(version: str = "3.19") -> str:
    """Construct the download URL for an Alpine miniroot tarball.

    Args:
        version: Alpine version (e.g., "3.19", "3.20").

    Returns:
        Full URL to the miniroot tarball.
    """
    major_minor = f"v{version}"
    filename = f"alpine-minirootfs-{version}.0-{ALPINE_ARCH}.tar.gz"
    return f"{ALPINE_MIRROR}/{major_minor}/releases/{ALPINE_ARCH}/{filename}"


def pull_image(image: str = "alpine", version: str = "3.19") -> Path:
    """Download and cache the base image tarball.

    The tarball is stored in /var/lib/pycrate/images/ and reused for
    all containers using the same image. Only downloads once.

    Args:
        image: Image name (currently only "alpine" is supported).
        version: Image version.

    Returns:
        Path to the cached tarball.

    Raises:
        RootfsError: If download fails.
    """
    if image != "alpine":
        raise ImageNotFoundError(image)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    tarball_path = IMAGES_DIR / f"alpine-{version}-{ALPINE_ARCH}.tar.gz"

    if tarball_path.exists():
        logger.debug("Image already cached at %s", tarball_path)
        return tarball_path

    url = get_alpine_url(version)
    logger.info("Pulling image from %s", url)

    try:
        import urllib.request
        urllib.request.urlretrieve(url, tarball_path)
        logger.info("Image cached at %s (%d bytes)", tarball_path, tarball_path.stat().st_size)
    except Exception as e:
        # Clean up partial download
        tarball_path.unlink(missing_ok=True)
        raise RootfsError(f"Failed to download image from {url}: {e}") from e

    return tarball_path


def prepare_rootfs(container_id: str, image: str = "alpine", version: str = "3.19") -> Path:
    """Create a container's root filesystem by extracting the base image.

    Each container gets a fresh copy of the rootfs. In a production runtime
    you'd use overlayfs for copy-on-write, but for clarity we extract
    a full copy per container.

    Args:
        container_id: Unique container identifier.
        image: Base image name.
        version: Base image version.

    Returns:
        Path to the container's rootfs directory.

    Raises:
        RootfsError: If extraction fails.
    """
    rootfs_dir = CONTAINERS_DIR / container_id / "rootfs"

    if rootfs_dir.exists():
        logger.debug("Rootfs already exists at %s", rootfs_dir)
        return rootfs_dir

    tarball_path = pull_image(image, version)
    rootfs_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting rootfs for %s to %s", container_id, rootfs_dir)

    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=rootfs_dir)
    except Exception as e:
        # Clean up failed extraction
        shutil.rmtree(rootfs_dir, ignore_errors=True)
        raise RootfsError(f"Failed to extract rootfs: {e}") from e

    # Create essential directories that might not exist in the tarball
    for subdir in ["proc", "sys", "dev", "tmp", "root", "oldroot"]:
        (rootfs_dir / subdir).mkdir(exist_ok=True)

    # Set correct permissions on /tmp
    os.chmod(rootfs_dir / "tmp", 0o1777)

    # Write a minimal resolv.conf so DNS works inside the container
    resolv_conf = rootfs_dir / "etc" / "resolv.conf"
    resolv_conf.write_text("nameserver 8.8.8.8\nnameserver 8.8.4.4\n")

    logger.info("Rootfs prepared for %s", container_id)
    return rootfs_dir


def setup_mounts(rootfs: Path) -> None:
    """Mount essential kernel filesystems inside the container's rootfs.

    Called inside the child process (in the new mount namespace) before
    pivot_root. These mounts give the container access to:
        /proc  - Process information (ps, top, etc.)
        /sys   - Kernel sysfs (read-only for security)
        /dev   - Device nodes (minimal set)

    Args:
        rootfs: Path to the container's rootfs directory.

    Raises:
        NamespaceError: If any mount operation fails.
    """
    rootfs_str = str(rootfs)

    # CRITICAL: Make the ENTIRE inherited mount tree private before doing
    # anything else. Without this, mount events (especially sysfs) propagate
    # back to the host via shared mount propagation and can unmount the
    # host's cgroup2 filesystem. This must happen before any other mount().
    mount("none", "/", flags=MS_PRIVATE | MS_REC)

    # Make the rootfs a bind mount of itself.
    # pivot_root requires both new_root and put_old to be mount points.
    mount(rootfs_str, rootfs_str, flags=MS_BIND | MS_REC)

    # Mount /proc — gives the container its own process view
    # (only sees processes in its PID namespace)
    proc_path = str(rootfs / "proc")
    mount("proc", proc_path, fstype="proc", flags=MS_NOSUID | MS_NODEV | MS_NOEXEC)

    # Mount /sys — kernel sysfs, read-only for security
    sys_path = str(rootfs / "sys")
    mount("sysfs", sys_path, fstype="sysfs", flags=MS_NOSUID | MS_NODEV | MS_NOEXEC | MS_RDONLY)

    # Mount /dev as tmpfs — we'll create device nodes manually
    dev_path = str(rootfs / "dev")
    mount("tmpfs", dev_path, fstype="tmpfs", flags=MS_NOSUID)

    # Create essential device nodes in /dev
    _setup_dev_nodes(rootfs / "dev")

    logger.debug("Mounted essential filesystems in %s", rootfs)


def _setup_dev_nodes(dev_path: Path) -> None:
    """Create minimal /dev entries that processes expect to exist.

    Instead of full device node creation (which requires mknod and specific
    major/minor numbers), we bind-mount from the host's /dev for the
    essentials and create symlinks for the rest.

    Args:
        dev_path: Path to the container's /dev directory.
    """
    # Create /dev/pts for pseudo-terminals
    pts_path = dev_path / "pts"
    pts_path.mkdir(exist_ok=True)
    try:
        mount("devpts", str(pts_path), fstype="devpts", flags=MS_NOSUID | MS_NOEXEC)
    except Exception:
        logger.debug("Could not mount devpts (non-critical)")

    # Create /dev/shm for shared memory
    shm_path = dev_path / "shm"
    shm_path.mkdir(exist_ok=True)
    try:
        mount("tmpfs", str(shm_path), fstype="tmpfs", flags=MS_NOSUID | MS_NODEV)
    except Exception:
        logger.debug("Could not mount /dev/shm (non-critical)")

    # Symlink standard file descriptors
    for name, target in [
        ("stdin", "/proc/self/fd/0"),
        ("stdout", "/proc/self/fd/1"),
        ("stderr", "/proc/self/fd/2"),
        ("fd", "/proc/self/fd"),
    ]:
        link_path = dev_path / name
        if not link_path.exists():
            os.symlink(target, link_path)

    # Create /dev/null, /dev/zero, /dev/random, /dev/urandom
    # These are bind-mounted from the host for simplicity
    for dev_name in ["null", "zero", "random", "urandom", "tty"]:
        host_dev = Path(f"/dev/{dev_name}")
        container_dev = dev_path / dev_name
        if host_dev.exists():
            container_dev.touch(exist_ok=True)
            try:
                mount(str(host_dev), str(container_dev), flags=MS_BIND)
            except Exception:
                logger.debug("Could not bind-mount /dev/%s (non-critical)", dev_name)


def do_pivot_root(rootfs: Path) -> None:
    """Execute pivot_root to make the container's rootfs the actual root.

    After this call:
        - The container sees rootfs as /
        - The old root is moved to /oldroot inside the new root
        - We unmount /oldroot so the host filesystem is inaccessible

    This is the moment the container becomes truly isolated from the
    host filesystem.

    Args:
        rootfs: Path to the prepared rootfs (already has mounts set up).

    Raises:
        NamespaceError: If pivot_root or subsequent cleanup fails.
    """
    old_root = rootfs / "oldroot"
    old_root.mkdir(exist_ok=True)

    # pivot_root: new_root becomes /, old root moves to put_old
    pivot_root(str(rootfs), str(old_root))

    # Change working directory to new root
    os.chdir("/")

    # Unmount the old root (lazy unmount) — this severs access to the host
    umount2("/oldroot", MNT_DETACH)

    # Remove the oldroot directory (it's now empty)
    try:
        os.rmdir("/oldroot")
    except OSError:
        pass  # May fail if still busy, not critical

    logger.debug("pivot_root complete — container is now in its own root filesystem")


def cleanup_rootfs(container_id: str) -> None:
    """Remove a container's rootfs directory from disk.

    Called during container removal to free disk space.

    Args:
        container_id: Container whose rootfs to remove.
    """
    container_dir = CONTAINERS_DIR / container_id
    if container_dir.exists():
        shutil.rmtree(container_dir, ignore_errors=True)
        logger.info("Cleaned up rootfs for %s", container_id)
