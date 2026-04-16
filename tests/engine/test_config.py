"""
Tests for ContainerConfig
"""

import pytest

from engine.config import ContainerConfig


class TestContainerConfig:
    """Validation and serialization tests for ContainerConfig."""

    def test_valid_config(self, sample_config):
        assert sample_config.name == "test-container"
        assert sample_config.container_id == "crate-test01"
        assert sample_config.cpu_limit_percent == 50
        assert sample_config.memory_limit_mb == 64
        assert sample_config.command == ["/bin/sh"]
        assert sample_config.image == "alpine"

    def test_auto_generated_id(self):
        config = ContainerConfig(name="auto-id")
        assert config.container_id.startswith("crate-")
        assert len(config.container_id) == 12  # "crate-" + 6 hex chars

    def test_unique_ids(self):
        ids = {ContainerConfig(name="test").container_id for _ in range(100)}
        assert len(ids) == 100  # All IDs should be unique

    def test_hostname_defaults_to_id(self):
        config = ContainerConfig(name="test", container_id="crate-abc123")
        assert config.hostname == "crate-abc123"

    def test_custom_hostname(self):
        config = ContainerConfig(name="test", hostname="myhost")
        assert config.hostname == "myhost"

    def test_cpu_quota_calculation(self):
        config = ContainerConfig(name="test", cpu_limit_percent=50)
        assert config.cpu_quota_us == 50000  # 50% of 100000us period

        config_full = ContainerConfig(name="test", cpu_limit_percent=100)
        assert config_full.cpu_quota_us == 100000

        config_low = ContainerConfig(name="test", cpu_limit_percent=1)
        assert config_low.cpu_quota_us == 1000

    def test_memory_limit_bytes(self):
        config = ContainerConfig(name="test", memory_limit_mb=64)
        assert config.memory_limit_bytes == 64 * 1024 * 1024

    def test_invalid_empty_name(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            ContainerConfig(name="")

    def test_invalid_cpu_too_low(self):
        with pytest.raises(ValueError, match="cpu_limit_percent must be 1-100"):
            ContainerConfig(name="test", cpu_limit_percent=0)

    def test_invalid_cpu_too_high(self):
        with pytest.raises(ValueError, match="cpu_limit_percent must be 1-100"):
            ContainerConfig(name="test", cpu_limit_percent=101)

    def test_invalid_memory_too_low(self):
        with pytest.raises(ValueError, match="memory_limit_mb must be at least 4MB"):
            ContainerConfig(name="test", memory_limit_mb=2)

    def test_invalid_empty_command(self):
        with pytest.raises(ValueError, match="command cannot be empty"):
            ContainerConfig(name="test", command=[])

    def test_frozen_immutability(self):
        config = ContainerConfig(name="test")
        with pytest.raises(AttributeError):
            config.name = "changed"

    def test_serialization_roundtrip(self):
        original = ContainerConfig(
            name="roundtrip",
            command=["/bin/sleep", "60"],
            cpu_limit_percent=75,
            memory_limit_mb=128,
            env={"KEY": "value"},
            hostname="myhost",
            container_id="crate-rt1234",
        )
        data = original.to_dict()
        restored = ContainerConfig.from_dict(data)

        assert restored.name == original.name
        assert restored.command == original.command
        assert restored.cpu_limit_percent == original.cpu_limit_percent
        assert restored.memory_limit_mb == original.memory_limit_mb
        assert restored.env == original.env
        assert restored.hostname == original.hostname
        assert restored.container_id == original.container_id

    def test_default_env_has_path(self):
        config = ContainerConfig(name="test")
        assert "PATH" in config.env

    def test_to_dict_structure(self, sample_config):
        data = sample_config.to_dict()
        required_keys = {
            "container_id", "name", "command", "cpu_limit_percent",
            "memory_limit_mb", "env", "hostname", "image",
        }
        assert required_keys == set(data.keys())
