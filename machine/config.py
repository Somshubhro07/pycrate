"""
Machine Configuration
======================

Immutable configuration for a PyCrate Machine instance. Persisted as JSON
in ``~/.pycrate/machine.json`` so the CLI can resume state across invocations.

The config is created once during ``pycrate machine init`` and read by every
subsequent command. Users can override defaults via CLI flags.
"""

from __future__ import annotations

import json
import logging
import platform
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# Default paths
PYCRATE_HOME = Path.home() / ".pycrate"
CONFIG_FILE = PYCRATE_HOME / "machine.json"
SSH_KEY_PATH = PYCRATE_HOME / "machine_rsa"
CACHE_DIR = PYCRATE_HOME / "cache"


class MachineState(str, Enum):
    """Lifecycle states for a PyCrate Machine."""
    NOT_CREATED = "not_created"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class MachineConfig:
    """Configuration for a PyCrate Machine VM.

    Attributes:
        backend: Virtualization backend ("wsl2" | "qemu" | "auto").
        cpus: Number of virtual CPUs.
        memory_mb: Memory allocation in megabytes.
        disk_gb: Disk size in gigabytes.
        ssh_port: Host port for SSH tunnel to the VM.
        image: Base image identifier (e.g., "alpine-virt-3.20").
        auto_start: Start machine automatically on first pycrate command.
        arch: CPU architecture ("x86_64" | "aarch64"). Auto-detected.
        name: Machine instance name.
    """

    backend: str = "auto"
    cpus: int = 2
    memory_mb: int = 2048
    disk_gb: int = 20
    ssh_port: int = 2222
    image: str = "alpine-virt-3.20"
    auto_start: bool = True
    arch: str = field(default_factory=lambda: _detect_arch())
    name: str = "pycrate"

    def save(self, path: Path = CONFIG_FILE) -> None:
        """Persist config to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
        logger.debug("Saved machine config to %s", path)

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> MachineConfig:
        """Load config from JSON file."""
        if not path.exists():
            raise FileNotFoundError(
                f"No machine config found at {path}. "
                "Run 'pycrate machine init' first."
            )
        data = json.loads(path.read_text())
        return cls(**data)

    @classmethod
    def exists(cls, path: Path = CONFIG_FILE) -> bool:
        """Check if a machine config exists."""
        return path.exists()

    @classmethod
    def resolve_backend(cls) -> str:
        """Auto-detect the best backend for the current platform.

        Returns:
            "native" on Linux, "wsl2" on Windows (if available),
            "qemu" on macOS or as fallback.
        """
        system = platform.system()

        if system == "Linux":
            return "native"
        elif system == "Windows":
            if _wsl2_available():
                return "wsl2"
            return "qemu"
        elif system == "Darwin":
            return "qemu"
        else:
            logger.warning("Unknown platform %s, falling back to QEMU", system)
            return "qemu"


def _detect_arch() -> str:
    """Detect host CPU architecture."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    elif machine in ("arm64", "aarch64"):
        return "aarch64"
    return machine


def _wsl2_available() -> bool:
    """Check if WSL2 is available on Windows.

    Note: ``wsl --status`` outputs UTF-16 LE on some Windows versions,
    so we strip null bytes before checking.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["wsl", "--status"],
            capture_output=True, text=False, timeout=5,
        )
        # Decode as UTF-16 LE, fallback to UTF-8 with null-byte stripping
        try:
            output = result.stdout.decode("utf-16-le")
        except (UnicodeDecodeError, ValueError):
            output = result.stdout.decode("utf-8", errors="replace").replace("\x00", "")

        return "Default Version: 2" in output or "WSL 2" in output
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
