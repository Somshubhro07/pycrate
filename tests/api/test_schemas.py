"""
Tests for API schemas (request validation).
"""

import pytest
from pydantic import ValidationError

from api.schemas import CreateContainerRequest, StopContainerRequest


class TestCreateContainerRequest:
    """Validates that pydantic catches bad input before it reaches the engine."""

    def test_valid_minimal(self):
        req = CreateContainerRequest(name="test")
        assert req.name == "test"
        assert req.command == ["/bin/sh"]
        assert req.cpu_limit_percent == 50
        assert req.memory_limit_mb == 64

    def test_valid_full(self):
        req = CreateContainerRequest(
            name="web-server",
            command=["/bin/sleep", "3600"],
            cpu_limit_percent=75,
            memory_limit_mb=128,
            env={"APP_ENV": "production"},
            hostname="web-01",
            image="alpine",
        )
        assert req.hostname == "web-01"

    def test_invalid_empty_name(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="")

    def test_invalid_name_special_chars(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="bad name!")

    def test_invalid_name_starts_with_dot(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name=".invalid")

    def test_valid_name_with_dots_hyphens(self):
        req = CreateContainerRequest(name="my-app.v2")
        assert req.name == "my-app.v2"

    def test_invalid_cpu_zero(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="test", cpu_limit_percent=0)

    def test_invalid_cpu_over_100(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="test", cpu_limit_percent=101)

    def test_invalid_memory_too_low(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="test", memory_limit_mb=2)

    def test_invalid_memory_too_high(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="test", memory_limit_mb=1024)

    def test_invalid_empty_command(self):
        with pytest.raises(ValidationError):
            CreateContainerRequest(name="test", command=[])


class TestStopContainerRequest:

    def test_default_timeout(self):
        req = StopContainerRequest()
        assert req.timeout == 10

    def test_custom_timeout(self):
        req = StopContainerRequest(timeout=30)
        assert req.timeout == 30

    def test_invalid_timeout_too_low(self):
        with pytest.raises(ValidationError):
            StopContainerRequest(timeout=0)

    def test_invalid_timeout_too_high(self):
        with pytest.raises(ValidationError):
            StopContainerRequest(timeout=120)
