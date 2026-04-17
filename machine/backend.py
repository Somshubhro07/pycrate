"""
Machine Backend Interface
===========================

Abstract base class for VM backends. Each backend implements the same
lifecycle: create → start → stop → destroy, with status queries and
command execution.

Backends:
    - ``NativeBackend``  — Linux (no VM, direct execution)
    - ``WSL2Backend``    — Windows (WSL2 distro import)
    - ``QEMUBackend``    — macOS / fallback (QEMU VM)

Factory function ``get_backend()`` auto-selects the best backend
for the current platform.
"""

from __future__ import annotations

import abc
import logging
import platform
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from machine.config import MachineConfig, MachineState

logger = logging.getLogger(__name__)


class MachineBackend(abc.ABC):
    """Abstract interface for a PyCrate Machine backend.

    Every backend must provide the full VM lifecycle plus command
    execution. The CLI layer calls these methods without knowing
    which backend is active.
    """

    def __init__(self, config: MachineConfig) -> None:
        self.config = config

    @abc.abstractmethod
    def create(self) -> None:
        """Create the machine (download image, configure VM).

        Idempotent: if already created, do nothing.
        """

    @abc.abstractmethod
    def start(self) -> None:
        """Start the machine and wait until SSH is ready."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Gracefully stop the machine."""

    @abc.abstractmethod
    def destroy(self) -> None:
        """Remove the machine and all associated data."""

    @abc.abstractmethod
    def status(self) -> MachineState:
        """Query the current machine state."""

    @abc.abstractmethod
    def exec_command(self, command: str) -> tuple[int, str, str]:
        """Execute a command inside the machine.

        Args:
            command: Shell command string to execute.

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """

    @abc.abstractmethod
    def exec_stream(self, command: str) -> int:
        """Execute a command and stream stdout/stderr to the terminal.

        Used for interactive commands like ``pycrate run``.

        Args:
            command: Shell command string to execute.

        Returns:
            Exit code.
        """

    @abc.abstractmethod
    def get_info(self) -> dict:
        """Get machine info (backend, state, resources, SSH port)."""


class NativeBackend(MachineBackend):
    """Linux backend — no VM needed, direct execution.

    All lifecycle methods are no-ops. ``exec_command`` runs commands
    directly via subprocess.
    """

    def create(self) -> None:
        logger.info("Native Linux — no machine needed")

    def start(self) -> None:
        pass  # Already "running"

    def stop(self) -> None:
        pass

    def destroy(self) -> None:
        pass

    def status(self) -> MachineState:
        from machine.config import MachineState
        return MachineState.RUNNING

    def exec_command(self, command: str) -> tuple[int, str, str]:
        import subprocess
        result = subprocess.run(
            command, shell=True,
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout, result.stderr

    def exec_stream(self, command: str) -> int:
        import subprocess
        result = subprocess.run(command, shell=True)
        return result.returncode

    def get_info(self) -> dict:
        return {
            "backend": "native",
            "state": "running",
            "message": "Linux detected — containers run natively",
        }


def get_backend(config: MachineConfig) -> MachineBackend:
    """Factory: return the appropriate backend for the current platform.

    Args:
        config: Machine configuration.

    Returns:
        A MachineBackend instance.
    """
    backend_name = config.backend
    if backend_name == "auto":
        from machine.config import MachineConfig as MC
        backend_name = MC.resolve_backend()

    if backend_name == "native":
        return NativeBackend(config)
    elif backend_name == "wsl2":
        from machine.wsl import WSL2Backend
        return WSL2Backend(config)
    elif backend_name == "qemu":
        from machine.qemu import QEMUBackend
        return QEMUBackend(config)
    else:
        raise ValueError(f"Unknown backend: {backend_name}")
