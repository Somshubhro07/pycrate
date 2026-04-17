"""
QEMU Backend — macOS / Fallback
==================================

Manages a PyCrate Machine as a QEMU virtual machine. Primary backend
for macOS, fallback for Windows when WSL2 is unavailable.

Architecture:
    1. ``create()`` downloads Alpine cloud image + generates cloud-init ISO
    2. ``start()`` boots QEMU with KVM/HVF/WHPX acceleration
    3. Commands are forwarded via SSH (paramiko)
    4. ``stop()`` sends ACPI shutdown via QEMU monitor
    5. ``destroy()`` kills process + removes disk

Acceleration:
    - Linux:  KVM  (``-accel kvm``)
    - macOS:  HVF  (``-accel hvf``)
    - Windows: WHPX (``-accel whpx``) or TCG (software, slow)

Port forwarding uses QEMU's ``-net user,hostfwd=...`` which maps
host ports to guest ports through QEMU's built-in NAT.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import signal
import subprocess
import time
from pathlib import Path

from machine.backend import MachineBackend
from machine.config import MachineConfig, MachineState, PYCRATE_HOME, SSH_KEY_PATH
from machine.ssh import SSHClient

logger = logging.getLogger(__name__)

QEMU_DIR = PYCRATE_HOME / "qemu"
PID_FILE = QEMU_DIR / "qemu.pid"


class QEMUBackend(MachineBackend):
    """QEMU backend for macOS and Windows fallback."""

    def __init__(self, config: MachineConfig) -> None:
        super().__init__(config)
        self._qemu_dir = QEMU_DIR
        self._disk_path = QEMU_DIR / "pycrate-disk.qcow2"
        self._cidata_path = QEMU_DIR / "cidata.iso"
        self._ssh = SSHClient(
            port=config.ssh_port,
            key_path=SSH_KEY_PATH,
        )

    def create(self) -> None:
        """Download image, generate cloud-init ISO, create disk."""
        self._qemu_dir.mkdir(parents=True, exist_ok=True)

        if self._disk_path.exists():
            logger.info("QEMU disk already exists at %s", self._disk_path)
            return

        from machine.image import (
            download_qcow2_image,
            generate_cloud_init_iso,
            get_ssh_public_key,
            ensure_ssh_key,
        )

        ensure_ssh_key()

        # Download base image
        base_image = download_qcow2_image(self.config.arch)

        # Create a copy for our VM (so the cached base stays clean)
        logger.info("Creating VM disk (%.0fGB)...", self.config.disk_gb)
        self._run_qemu_img([
            "create", "-f", "qcow2",
            "-b", str(base_image), "-F", "qcow2",
            str(self._disk_path),
            f"{self.config.disk_gb}G",
        ])

        # Generate cloud-init seed
        ssh_pub_key = get_ssh_public_key()
        generate_cloud_init_iso(self._cidata_path, ssh_pub_key)

        logger.info("PyCrate Machine (QEMU) created successfully")

    def start(self) -> None:
        """Boot the QEMU VM and wait for SSH."""
        if self.status() == MachineState.RUNNING:
            logger.info("PyCrate Machine is already running")
            return

        if not self._disk_path.exists():
            raise RuntimeError(
                "VM disk not found. Run 'pycrate machine init' first."
            )

        qemu_bin = self._find_qemu_binary()
        accel = self._detect_accelerator()

        cmd = [
            qemu_bin,
            "-accel", accel,
            "-m", str(self.config.memory_mb),
            "-smp", str(self.config.cpus),
            "-drive", f"file={self._disk_path},if=virtio,format=qcow2",
            "-net", "nic,model=virtio",
            "-net", f"user,hostfwd=tcp::{self.config.ssh_port}-:22",
            "-display", "none",
            "-daemonize",
            "-pidfile", str(PID_FILE),
        ]

        # Attach cloud-init seed if it exists (first boot only)
        if self._cidata_path.exists():
            cmd.extend([
                "-drive", f"file={self._cidata_path},format=raw,if=virtio,readonly=on",
            ])

        logger.info("Starting QEMU VM (%s, %d CPUs, %dMB RAM)...",
                     accel, self.config.cpus, self.config.memory_mb)
        logger.debug("Command: %s", " ".join(cmd))

        subprocess.run(cmd, check=True, capture_output=True, text=True)

        # Wait for SSH to be ready
        logger.info("Waiting for VM to boot...")
        self._ssh.connect(retries=12)  # Up to 60s

        logger.info("PyCrate Machine (QEMU) is running (SSH on port %d)",
                     self.config.ssh_port)

    def stop(self) -> None:
        """Stop the QEMU VM."""
        pid = self._get_pid()
        if pid is None:
            logger.info("PyCrate Machine is not running")
            return

        self._ssh.close()

        # Try graceful shutdown first
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
                # Wait for process to exit
                for _ in range(10):
                    try:
                        os.kill(pid, 0)
                        time.sleep(1)
                    except OSError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("Error stopping QEMU: %s", e)

        if PID_FILE.exists():
            PID_FILE.unlink()

        logger.info("PyCrate Machine (QEMU) stopped")

    def destroy(self) -> None:
        """Stop and remove all QEMU data."""
        self.stop()

        import shutil
        if self._qemu_dir.exists():
            shutil.rmtree(self._qemu_dir, ignore_errors=True)

        logger.info("PyCrate Machine (QEMU) destroyed")

    def status(self) -> MachineState:
        """Check if the QEMU process is running."""
        if not self._disk_path.exists():
            return MachineState.NOT_CREATED

        pid = self._get_pid()
        if pid is None:
            return MachineState.STOPPED

        # Check if process is actually alive
        if self._process_alive(pid):
            return MachineState.RUNNING

        # Stale PID file
        PID_FILE.unlink(missing_ok=True)
        return MachineState.STOPPED

    def exec_command(self, command: str) -> tuple[int, str, str]:
        """Execute a command via SSH."""
        return self._ssh.exec_command(command)

    def exec_stream(self, command: str) -> int:
        """Execute a command with live streaming via SSH."""
        return self._ssh.exec_stream(command)

    def get_info(self) -> dict:
        """Get machine information."""
        state = self.status()

        info = {
            "backend": "qemu",
            "state": state.value,
            "arch": self.config.arch,
            "accelerator": self._detect_accelerator(),
            "disk": str(self._disk_path),
            "ssh_port": self.config.ssh_port,
            "cpus": self.config.cpus,
            "memory_mb": self.config.memory_mb,
        }

        if self._disk_path.exists():
            info["disk_size_mb"] = round(
                self._disk_path.stat().st_size / 1e6, 1
            )

        return info

    # -- Internal helpers --

    def _find_qemu_binary(self) -> str:
        """Find the correct QEMU binary for the host architecture."""
        arch = self.config.arch
        binary = f"qemu-system-{arch}"

        # Check if it's in PATH
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return binary

        # Common install locations
        candidates = [
            f"/usr/bin/{binary}",
            f"/usr/local/bin/{binary}",
            f"/opt/homebrew/bin/{binary}",
            f"C:\\Program Files\\qemu\\{binary}.exe",
        ]

        for path in candidates:
            if Path(path).exists():
                return path

        raise FileNotFoundError(
            f"QEMU not found. Install with:\n"
            f"  macOS:   brew install qemu\n"
            f"  Ubuntu:  sudo apt-get install qemu-system\n"
            f"  Windows: choco install qemu"
        )

    def _detect_accelerator(self) -> str:
        """Detect the best hardware accelerator."""
        system = platform.system()

        if system == "Linux":
            if Path("/dev/kvm").exists():
                return "kvm"
        elif system == "Darwin":
            return "hvf"
        elif system == "Windows":
            return "whpx"

        logger.warning(
            "No hardware accelerator found, using TCG (software). "
            "Performance will be significantly slower."
        )
        return "tcg"

    def _get_pid(self) -> int | None:
        """Read the QEMU process ID from the PID file."""
        if not PID_FILE.exists():
            return None
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None

    def _process_alive(self, pid: int) -> bool:
        """Check if a process is running."""
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True,
            )
            return str(pid) in result.stdout
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def _run_qemu_img(self, args: list[str]) -> None:
        """Run a qemu-img command."""
        subprocess.run(
            ["qemu-img"] + args,
            check=True, capture_output=True, text=True,
        )
