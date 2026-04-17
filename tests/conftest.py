"""
Shared test fixtures for the PyCrate test suite.

These fixtures provide mock/stub versions of Linux-specific subsystems
so tests can run on any platform (including CI runners and local dev
machines that aren't running Linux).
"""

from __future__ import annotations

import pytest

try:
    from engine.config import ContainerConfig
    _HAS_ENGINE = True
except ImportError:
    _HAS_ENGINE = False
    ContainerConfig = None  # type: ignore


@pytest.fixture
def sample_config():
    """A valid ContainerConfig for testing."""
    if not _HAS_ENGINE:
        pytest.skip("engine module not available on this platform")
    return ContainerConfig(
        name="test-container",
        command=["/bin/sh"],
        cpu_limit_percent=50,
        memory_limit_mb=64,
        container_id="crate-test01",
    )


@pytest.fixture
def minimal_config():
    """A minimal ContainerConfig with defaults."""
    if not _HAS_ENGINE:
        pytest.skip("engine module not available on this platform")
    return ContainerConfig(name="minimal")


@pytest.fixture
def high_resource_config():
    """A ContainerConfig with maximum resource limits."""
    if not _HAS_ENGINE:
        pytest.skip("engine module not available on this platform")
    return ContainerConfig(
        name="heavy",
        cpu_limit_percent=100,
        memory_limit_mb=512,
        command=["/bin/sleep", "3600"],
        env={"APP_ENV": "test", "DEBUG": "1"},
    )
