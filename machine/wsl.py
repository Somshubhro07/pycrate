"""
WSL2 Backend — Windows
========================

Manages a PyCrate Machine as a WSL2 distribution on Windows.

Architecture:
    1. ``create()`` imports an Alpine minirootfs tarball as a WSL2 distro
    2. ``start()`` boots the distro and runs the setup script
    3. Commands are forwarded via ``wsl.exe -d pycrate -e ...``
    4. ``stop()`` terminates the distro
    5. ``destroy()`` unregisters and removes files

WSL2 is the fastest backend — it shares the Windows kernel's built-in
Linux compatibility layer, giving near-native performance with ~200MB
overhead. Available by default on Windows 11.

No QEMU, no SSH needed for command execution (we use ``wsl.exe -e``
directly). SSH is only used for the cluster agent, not for CLI forwarding.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from machine.backend import MachineBackend
from machine.config import MachineConfig, MachineState, PYCRATE_HOME

logger = logging.getLogger(__name__)

DISTRO_NAME = "pycrate"
WSL_DATA_DIR = PYCRATE_HOME / "wsl"


def _decode_wsl_output(raw: bytes) -> str:
    """Decode WSL output which may be UTF-16 LE or UTF-8.

    ``wsl.exe`` on Windows outputs UTF-16 LE for its own messages
    (like ``--list``, ``--status``) but UTF-8 for command output.
    """
    if not raw:
        return ""
    try:
        return raw.decode("utf-16-le")
    except (UnicodeDecodeError, ValueError):
        return raw.decode("utf-8", errors="replace").replace("\x00", "")


class WSL2Backend(MachineBackend):
    """WSL2 backend for Windows."""

    def __init__(self, config: MachineConfig) -> None:
        super().__init__(config)
        self._data_dir = WSL_DATA_DIR

    def create(self) -> None:
        """Import the Alpine rootfs as a WSL2 distribution."""
        if self._distro_exists():
            logger.info("WSL2 distro '%s' already exists", DISTRO_NAME)
            return

        from machine.image import download_rootfs_tarball, get_ssh_public_key

        tarball = download_rootfs_tarball(self.config.arch)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Importing Alpine rootfs into WSL2 as '%s'...", DISTRO_NAME)

        self._wsl([
            "--import", DISTRO_NAME,
            str(self._data_dir),
            str(tarball),
            "--version", "2",
        ])

        logger.info("WSL2 distro created. Running setup...")

        # Write setup script to a temp file inside WSL, then execute it.
        # This avoids shell quoting issues with inline scripts.
        from machine.image import get_wsl_setup_script
        ssh_pub_key = get_ssh_public_key()
        setup_script = get_wsl_setup_script(ssh_pub_key)

        # Write the script to the WSL filesystem via stdin
        script_path = "/tmp/pycrate_setup.sh"
        self._write_file_to_distro(script_path, setup_script)
        self._exec_in_distro(f"chmod +x {script_path} && {script_path}", timeout=120)

        logger.info("PyCrate Machine (WSL2) created successfully")

    def start(self) -> None:
        """Start the WSL2 distribution.

        WSL2 distros start automatically when you run a command in them,
        so "starting" just means verifying it's responsive.
        """
        if not self._distro_exists():
            raise RuntimeError(
                f"WSL2 distro '{DISTRO_NAME}' not found. "
                "Run 'pycrate machine init' first."
            )

        # Run a trivial command to boot the distro
        code, out, err = self.exec_command("echo ready")
        if code != 0:
            raise RuntimeError(f"WSL2 distro failed to start: {err}")

        logger.info("PyCrate Machine (WSL2) is running")

    def stop(self) -> None:
        """Terminate the WSL2 distribution."""
        self._wsl(["--terminate", DISTRO_NAME], check=False)
        logger.info("PyCrate Machine (WSL2) stopped")

    def destroy(self) -> None:
        """Unregister the WSL2 distribution and remove data."""
        self._wsl(["--unregister", DISTRO_NAME], check=False)

        if self._data_dir.exists():
            import shutil
            shutil.rmtree(self._data_dir, ignore_errors=True)

        logger.info("PyCrate Machine (WSL2) destroyed")

    def status(self) -> MachineState:
        """Check if the WSL2 distro is running."""
        if not self._distro_exists():
            return MachineState.NOT_CREATED

        result = subprocess.run(
            ["wsl", "-l", "--running"],
            capture_output=True, text=False, timeout=5,
        )
        output = _decode_wsl_output(result.stdout)

        if DISTRO_NAME in output:
            return MachineState.RUNNING
        return MachineState.STOPPED

    def exec_command(self, command: str) -> tuple[int, str, str]:
        """Execute a command inside the WSL2 distro."""
        result = subprocess.run(
            ["wsl", "-d", DISTRO_NAME, "-e", "sh", "-c", command],
            capture_output=True, text=False, timeout=60,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        return result.returncode, stdout, stderr

    def exec_stream(self, command: str) -> int:
        """Execute a command with live output streaming."""
        result = subprocess.run(
            ["wsl", "-d", DISTRO_NAME, "-e", "sh", "-c", command],
            timeout=300,
        )
        return result.returncode

    def get_info(self) -> dict:
        """Get machine information."""
        state = self.status()

        info = {
            "backend": "wsl2",
            "distro": DISTRO_NAME,
            "state": state.value,
            "arch": self.config.arch,
            "data_dir": str(self._data_dir),
        }

        if state == MachineState.RUNNING:
            code, out, _ = self.exec_command(
                "cat /proc/meminfo | grep MemTotal | awk '{print $2}'"
            )
            if code == 0 and out.strip():
                info["memory_mb"] = int(out.strip()) // 1024

            code, out, _ = self.exec_command("nproc")
            if code == 0 and out.strip():
                info["cpus"] = int(out.strip())

        return info

    # -- Internal helpers --

    def _distro_exists(self) -> bool:
        """Check if our WSL2 distro is registered."""
        result = subprocess.run(
            ["wsl", "-l", "-q"],
            capture_output=True, text=False, timeout=5,
        )
        output = _decode_wsl_output(result.stdout)
        return DISTRO_NAME in output.split()

    def _wsl(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a wsl.exe command."""
        cmd = ["wsl"] + args
        return subprocess.run(
            cmd, capture_output=True, text=False,
            timeout=120, check=check,
        )

    def _write_file_to_distro(self, path: str, content: str) -> None:
        """Write a file into the WSL2 distro via stdin pipe.

        Avoids shell quoting issues with inline heredocs.
        """
        proc = subprocess.run(
            ["wsl", "-d", DISTRO_NAME, "-e", "sh", "-c", f"cat > {path}"],
            input=content.encode("utf-8"),
            capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"Failed to write {path} in distro: {stderr}")

    def _exec_in_distro(self, command: str, timeout: int = 60) -> tuple[int, str]:
        """Execute a command in the distro, returning (code, combined output)."""
        result = subprocess.run(
            ["wsl", "-d", DISTRO_NAME, "-e", "sh", "-c", command],
            capture_output=True, text=False, timeout=timeout,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        combined = stdout + stderr
        if result.returncode != 0:
            logger.warning("Command failed (code %d): %s\n%s", result.returncode, command[:80], combined[:500])
        return result.returncode, combined
