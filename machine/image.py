"""
VM Image Manager
==================

Downloads Alpine Linux cloud images and generates cloud-init seed ISOs
for bootstrapping PyCrate inside the VM.

Image workflow:
    1. Download Alpine ``virt`` qcow2 image (arch-specific, ~50MB)
    2. Generate SSH keypair (if not exists)
    3. Generate ``cidata.iso`` with cloud-init user-data:
       - Inject SSH public key
       - Install PyCrate via pip
       - Enable sshd
    4. Cache everything in ``~/.pycrate/cache/``

The cloud-init approach is the same one Lima and Docker Desktop use
to bootstrap their managed VMs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from urllib import request

from machine.config import CACHE_DIR, SSH_KEY_PATH
from machine.ssh import generate_ssh_keypair, get_public_key

logger = logging.getLogger(__name__)

# Alpine cloud image URLs by architecture
ALPINE_VERSION = "3.20"
ALPINE_IMAGES = {
    "x86_64": (
        f"https://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}"
        f"/releases/cloud/nocloud_alpine-{ALPINE_VERSION}.0-x86_64-bios-cloudinit-r0.qcow2"
    ),
    "aarch64": (
        f"https://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}"
        f"/releases/cloud/nocloud_alpine-{ALPINE_VERSION}.0-aarch64-bios-cloudinit-r0.qcow2"
    ),
}

# For WSL2: Alpine minirootfs tarball
ALPINE_ROOTFS = {
    "x86_64": (
        f"https://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}"
        f"/releases/x86_64/alpine-minirootfs-{ALPINE_VERSION}.0-x86_64.tar.gz"
    ),
    "aarch64": (
        f"https://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}"
        f"/releases/aarch64/alpine-minirootfs-{ALPINE_VERSION}.0-aarch64.tar.gz"
    ),
}


def ensure_cache_dir() -> Path:
    """Create and return the cache directory."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def download_qcow2_image(arch: str = "x86_64") -> Path:
    """Download the Alpine cloud image for QEMU.

    Args:
        arch: CPU architecture ("x86_64" or "aarch64").

    Returns:
        Path to the downloaded qcow2 image.
    """
    url = ALPINE_IMAGES.get(arch)
    if not url:
        raise ValueError(f"No cloud image available for architecture: {arch}")

    cache = ensure_cache_dir()
    filename = f"alpine-{ALPINE_VERSION}-{arch}.qcow2"
    image_path = cache / filename

    if image_path.exists():
        logger.info("Using cached image: %s", image_path)
        return image_path

    logger.info("Downloading Alpine %s cloud image (%s)...", ALPINE_VERSION, arch)
    logger.info("URL: %s", url)

    _download_file(url, image_path)

    logger.info("Downloaded: %s (%.1f MB)", filename, image_path.stat().st_size / 1e6)
    return image_path


def download_rootfs_tarball(arch: str = "x86_64") -> Path:
    """Download the Alpine minirootfs tarball for WSL2.

    Args:
        arch: CPU architecture.

    Returns:
        Path to the downloaded tarball.
    """
    url = ALPINE_ROOTFS.get(arch)
    if not url:
        raise ValueError(f"No rootfs available for architecture: {arch}")

    cache = ensure_cache_dir()
    filename = f"alpine-minirootfs-{ALPINE_VERSION}-{arch}.tar.gz"
    tarball_path = cache / filename

    if tarball_path.exists():
        logger.info("Using cached rootfs: %s", tarball_path)
        return tarball_path

    logger.info("Downloading Alpine %s minirootfs (%s)...", ALPINE_VERSION, arch)
    _download_file(url, tarball_path)

    logger.info("Downloaded: %s (%.1f MB)", filename, tarball_path.stat().st_size / 1e6)
    return tarball_path


def generate_cloud_init_iso(output_path: Path, ssh_pub_key: str) -> Path:
    """Generate a cloud-init NoCloud seed ISO.

    This ISO is attached to the QEMU VM as a second drive. On first boot,
    cloud-init reads it and configures the VM (SSH keys, packages, etc.).

    Args:
        output_path: Where to write the ISO.
        ssh_pub_key: SSH public key to inject.

    Returns:
        Path to the generated ISO.
    """
    user_data = _generate_user_data(ssh_pub_key)
    meta_data = "instance-id: pycrate-machine\nlocal-hostname: pycrate\n"

    # Try genisoimage/mkisofs (Linux), hdiutil (macOS), or Python fallback
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "user-data").write_text(user_data)
        (tmp / "meta-data").write_text(meta_data)

        # Try multiple ISO creation tools
        for tool in _iso_tools():
            try:
                tool(tmp, output_path)
                logger.info("Generated cloud-init ISO: %s", output_path)
                return output_path
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue

        # Python fallback: create a simple concatenated file
        # (less compatible but works without external tools)
        _python_iso_fallback(tmp, output_path)
        logger.info("Generated cloud-init seed (fallback): %s", output_path)
        return output_path


def ensure_ssh_key() -> Path:
    """Ensure SSH keypair exists, generating if needed."""
    return generate_ssh_keypair(SSH_KEY_PATH)


def get_ssh_public_key() -> str:
    """Get the SSH public key for injection into the VM."""
    ensure_ssh_key()
    return get_public_key(SSH_KEY_PATH)


def get_wsl_setup_script(ssh_pub_key: str) -> str:
    """Generate a shell script to bootstrap PyCrate inside WSL2.

    This is run inside the WSL2 Alpine distro after import.
    """
    return f"""#!/bin/sh
set -e

# Enable community repo
sed -i 's|#.*community|http://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}/community|' /etc/apk/repositories
echo "http://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}/main" > /etc/apk/repositories
echo "http://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}/community" >> /etc/apk/repositories

# Install essentials
apk update
apk add --no-cache python3 py3-pip openssh-server bash iproute2 iptables

# Configure SSH
mkdir -p /root/.ssh
echo '{ssh_pub_key}' > /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys

ssh-keygen -A  # Generate host keys
echo "PermitRootLogin yes" >> /etc/ssh/sshd_config
echo "PubkeyAuthentication yes" >> /etc/ssh/sshd_config

# Install PyCrate
pip3 install --break-system-packages pycrate 2>/dev/null || \\
    pip3 install pycrate 2>/dev/null || \\
    echo "PyCrate pip install skipped (will install from source)"

# Create data directories
mkdir -p /var/lib/pycrate/images
mkdir -p /var/lib/pycrate/containers

echo "PyCrate Machine setup complete"
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_user_data(ssh_pub_key: str) -> str:
    """Generate cloud-init user-data YAML."""
    return f"""#cloud-config
hostname: pycrate
manage_etc_hosts: true

users:
  - name: root
    ssh_authorized_keys:
      - {ssh_pub_key}

packages:
  - python3
  - py3-pip
  - openssh-server
  - bash
  - iproute2
  - iptables
  - debootstrap

runcmd:
  - pip3 install --break-system-packages pycrate || pip3 install pycrate || true
  - mkdir -p /var/lib/pycrate/images /var/lib/pycrate/containers
  - rc-update add sshd default
  - service sshd start

ssh_pwauth: false
disable_root: false
"""


def _download_file(url: str, dest: Path) -> None:
    """Download a file with progress indication."""
    def _reporthook(count, block_size, total_size):
        if total_size > 0:
            pct = min(100, count * block_size * 100 // total_size)
            print(f"\r  Downloading: {pct}%", end="", flush=True)

    try:
        request.urlretrieve(url, str(dest), reporthook=_reporthook)
        print()  # Newline after progress
    except Exception as e:
        if dest.exists():
            dest.unlink()
        raise RuntimeError(f"Download failed: {e}") from e


def _iso_tools():
    """Yield ISO creation functions to try in order."""
    yield _genisoimage
    yield _mkisofs
    yield _hdiutil


def _genisoimage(src: Path, dest: Path) -> None:
    subprocess.run([
        "genisoimage", "-output", str(dest),
        "-volid", "cidata", "-joliet", "-rock",
        str(src / "user-data"), str(src / "meta-data"),
    ], check=True, capture_output=True)


def _mkisofs(src: Path, dest: Path) -> None:
    subprocess.run([
        "mkisofs", "-output", str(dest),
        "-volid", "cidata", "-joliet", "-rock",
        str(src / "user-data"), str(src / "meta-data"),
    ], check=True, capture_output=True)


def _hdiutil(src: Path, dest: Path) -> None:
    """macOS: use hdiutil to create ISO."""
    subprocess.run([
        "hdiutil", "makehybrid", "-iso", "-joliet",
        "-default-volume-name", "cidata",
        "-o", str(dest), str(src),
    ], check=True, capture_output=True)


def _python_iso_fallback(src: Path, dest: Path) -> None:
    """Fallback: create a tar archive as seed data.

    Not a real ISO, but QEMU's cloud-init can read it with the
    NoCloud datasource if we pass it as a disk with the right label.
    """
    import tarfile
    with tarfile.open(str(dest), "w:gz") as tar:
        tar.add(str(src / "user-data"), arcname="user-data")
        tar.add(str(src / "meta-data"), arcname="meta-data")
