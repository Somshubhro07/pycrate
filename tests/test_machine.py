"""
Tests for the machine package — config, backend detection, and image generation.

These tests run on all platforms (no VM or WSL2 required).
"""

from __future__ import annotations

import json
import platform
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# MachineConfig
# ---------------------------------------------------------------------------

class TestMachineConfig:
    """Tests for machine config dataclass and persistence."""

    def test_defaults(self):
        from machine.config import MachineConfig

        config = MachineConfig()
        assert config.cpus == 2
        assert config.memory_mb == 2048
        assert config.disk_gb == 20
        assert config.ssh_port == 2222
        assert config.backend == "auto"
        assert config.name == "pycrate"

    def test_save_and_load(self, tmp_path):
        from machine.config import MachineConfig

        config = MachineConfig(cpus=4, memory_mb=4096, backend="wsl2")
        config_path = tmp_path / "machine.json"
        config.save(config_path)

        loaded = MachineConfig.load(config_path)
        assert loaded.cpus == 4
        assert loaded.memory_mb == 4096
        assert loaded.backend == "wsl2"

    def test_load_missing_raises(self, tmp_path):
        from machine.config import MachineConfig

        with pytest.raises(FileNotFoundError):
            MachineConfig.load(tmp_path / "nonexistent.json")

    def test_exists(self, tmp_path):
        from machine.config import MachineConfig

        path = tmp_path / "machine.json"
        assert not MachineConfig.exists(path)

        MachineConfig().save(path)
        assert MachineConfig.exists(path)

    def test_json_roundtrip(self, tmp_path):
        from machine.config import MachineConfig

        config = MachineConfig(
            cpus=8, memory_mb=8192, disk_gb=50,
            ssh_port=3333, backend="qemu",
        )
        path = tmp_path / "machine.json"
        config.save(path)

        raw = json.loads(path.read_text())
        assert raw["cpus"] == 8
        assert raw["backend"] == "qemu"

    def test_arch_detection(self):
        from machine.config import MachineConfig

        config = MachineConfig()
        assert config.arch in ("x86_64", "aarch64", platform.machine().lower())


# ---------------------------------------------------------------------------
# MachineState
# ---------------------------------------------------------------------------

class TestMachineState:
    def test_state_values(self):
        from machine.config import MachineState

        assert MachineState.NOT_CREATED == "not_created"
        assert MachineState.RUNNING == "running"
        assert MachineState.STOPPED == "stopped"


# ---------------------------------------------------------------------------
# Backend Detection
# ---------------------------------------------------------------------------

class TestBackendDetection:
    """Test the backend factory and platform detection."""

    def test_resolve_linux_native(self):
        from machine.config import MachineConfig

        with mock.patch("platform.system", return_value="Linux"):
            assert MachineConfig.resolve_backend() == "native"

    def test_resolve_darwin_qemu(self):
        from machine.config import MachineConfig

        with mock.patch("platform.system", return_value="Darwin"):
            assert MachineConfig.resolve_backend() == "qemu"

    def test_resolve_windows_wsl2(self):
        from machine.config import MachineConfig

        with mock.patch("platform.system", return_value="Windows"):
            with mock.patch("machine.config._wsl2_available", return_value=True):
                assert MachineConfig.resolve_backend() == "wsl2"

    def test_resolve_windows_fallback_qemu(self):
        from machine.config import MachineConfig

        with mock.patch("platform.system", return_value="Windows"):
            with mock.patch("machine.config._wsl2_available", return_value=False):
                assert MachineConfig.resolve_backend() == "qemu"

    def test_get_native_backend(self):
        from machine.config import MachineConfig
        from machine.backend import get_backend, NativeBackend

        config = MachineConfig(backend="native")
        be = get_backend(config)
        assert isinstance(be, NativeBackend)

    def test_native_backend_always_running(self):
        from machine.config import MachineConfig, MachineState
        from machine.backend import NativeBackend

        be = NativeBackend(MachineConfig())
        assert be.status() == MachineState.RUNNING


# ---------------------------------------------------------------------------
# NativeBackend
# ---------------------------------------------------------------------------

class TestNativeBackend:
    """Test the Linux native backend (no VM)."""

    def test_exec_command(self):
        from machine.config import MachineConfig
        from machine.backend import NativeBackend

        be = NativeBackend(MachineConfig())
        code, stdout, stderr = be.exec_command("echo hello")
        assert code == 0
        assert "hello" in stdout

    def test_get_info(self):
        from machine.config import MachineConfig
        from machine.backend import NativeBackend

        be = NativeBackend(MachineConfig())
        info = be.get_info()
        assert info["backend"] == "native"
        assert info["state"] == "running"

    def test_lifecycle_noop(self):
        from machine.config import MachineConfig
        from machine.backend import NativeBackend

        be = NativeBackend(MachineConfig())
        be.create()   # no-op
        be.start()    # no-op
        be.stop()     # no-op
        be.destroy()  # no-op


# ---------------------------------------------------------------------------
# Image Management
# ---------------------------------------------------------------------------

class TestImageManagement:
    """Test cloud-init generation (no network calls)."""

    def test_cloud_init_user_data(self):
        from machine.image import _generate_user_data

        user_data = _generate_user_data("ssh-ed25519 AAAA... test")
        assert "#cloud-config" in user_data
        assert "ssh-ed25519 AAAA..." in user_data
        assert "python3" in user_data
        assert "pycrate" in user_data

    def test_wsl_setup_script(self):
        from machine.image import get_wsl_setup_script

        script = get_wsl_setup_script("ssh-ed25519 AAAA... test")
        assert "apk update" in script
        assert "ssh-ed25519 AAAA..." in script
        assert "authorized_keys" in script
        assert "pycrate" in script

    def test_ensure_cache_dir(self, tmp_path):
        from machine.image import CACHE_DIR

        # Just verify the function exists and returns a Path
        from machine.image import ensure_cache_dir
        result = ensure_cache_dir()
        assert isinstance(result, Path)
