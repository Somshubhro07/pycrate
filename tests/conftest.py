"""
Shared test fixtures for the PyCrate test suite.

These fixtures provide mock/stub versions of Linux-specific subsystems
so tests can run on any platform (including CI runners and local dev
machines that aren't running Linux).
"""

from __future__ import annotations

import pytest

from engine.config import ContainerConfig


@pytest.fixture
def sample_config() -> ContainerConfig:
    """A valid ContainerConfig for testing."""
    return ContainerConfig(
        name="test-container",
        command=["/bin/sh"],
        cpu_limit_percent=50,
        memory_limit_mb=64,
        container_id="crate-test01",
    )


@pytest.fixture
def minimal_config() -> ContainerConfig:
    """A minimal ContainerConfig with defaults."""
    return ContainerConfig(name="minimal")


@pytest.fixture
def high_resource_config() -> ContainerConfig:
    """A ContainerConfig with maximum resource limits."""
    return ContainerConfig(
        name="heavy",
        cpu_limit_percent=100,
        memory_limit_mb=512,
        command=["/bin/sleep", "3600"],
        env={"APP_ENV": "test", "DEBUG": "1"},
    )
