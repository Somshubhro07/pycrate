"""
Machine Package — Cross-Platform Container Runtime
=====================================================

Makes PyCrate work on Windows, macOS, and Linux by transparently managing
a lightweight Linux VM that hosts the container engine.

On Linux, the engine runs natively (no VM needed).
On Windows, uses WSL2 (preferred) or QEMU as a backend.
On macOS, uses QEMU with Apple HVF acceleration.

This is the same architecture Docker Desktop, Podman Machine, and Lima use.
"""

from __future__ import annotations

from machine.config import MachineConfig, MachineState
from machine.backend import MachineBackend, get_backend

__all__ = ["MachineConfig", "MachineState", "MachineBackend", "get_backend"]
