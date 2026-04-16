"""
Tests for API dependencies (auth, engine injection).
"""

import hmac
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from api.dependencies import get_engine, set_engine, verify_api_key


class TestVerifyApiKey:

    @pytest.mark.asyncio
    async def test_valid_key(self):
        settings = MagicMock()
        settings.api_key = "test-key-12345"
        result = await verify_api_key(x_api_key="test-key-12345", settings=settings)
        assert result == "test-key-12345"

    @pytest.mark.asyncio
    async def test_invalid_key_raises_401(self):
        settings = MagicMock()
        settings.api_key = "correct-key"
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(x_api_key="wrong-key", settings=settings)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_key_raises_401(self):
        settings = MagicMock()
        settings.api_key = "correct-key"
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(x_api_key="", settings=settings)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_timing_safe_comparison(self):
        """Verify we use constant-time comparison, not == ."""
        settings = MagicMock()
        settings.api_key = "secret"
        with patch("hmac.compare_digest", return_value=True) as mock_compare:
            await verify_api_key(x_api_key="secret", settings=settings)
            mock_compare.assert_called_once_with("secret", "secret")


class TestGetEngine:

    def test_returns_engine_when_set(self):
        mock_engine = MagicMock()
        set_engine(mock_engine)
        assert get_engine() is mock_engine

    def test_raises_503_when_not_initialized(self):
        set_engine(None)
        with pytest.raises(HTTPException) as exc_info:
            get_engine()
        assert exc_info.value.status_code == 503
