"""
API Dependencies
==================

FastAPI dependency injection for authentication and engine access.

Dependencies are injected into route handler function signatures
via FastAPI's Depends() mechanism, keeping route handlers clean.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from api.config import Settings, get_settings
from engine.container import ContainerManager

# Singleton engine manager instance.
# Initialized during app lifespan, used by all route handlers.
_engine: ContainerManager | None = None


def set_engine(engine: ContainerManager) -> None:
    """Set the engine instance. Called once during app startup."""
    global _engine
    _engine = engine


def get_engine() -> ContainerManager:
    """Dependency: provides the ContainerManager to route handlers.

    Raises:
        HTTPException: If engine is not initialized.
    """
    if _engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Engine not initialized",
        )
    return _engine


async def verify_api_key(
    x_api_key: str = Header(..., description="API key for authentication"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Dependency: validates the X-API-Key header.

    Compares the provided key against the configured PYCRATE_API_KEY.
    Uses constant-time comparison to prevent timing attacks.

    Args:
        x_api_key: Value of the X-API-Key header.
        settings: Application settings (injected).

    Returns:
        The validated API key string.

    Raises:
        HTTPException: If the key is missing or invalid.
    """
    import hmac
    if not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return x_api_key


async def verify_admin_cookie(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    """Dependency: validates the admin_session HTTP-only cookie.

    The cookie value is the raw admin key, signed by the cookie_secret
    using HMAC-SHA256. Format: {admin_key}:{signature}

    Args:
        request: The incoming HTTP request (to read cookies).
        settings: Application settings (injected).

    Returns:
        The validated admin key string.

    Raises:
        HTTPException: If the cookie is missing or invalid.
    """
    import hmac
    import hashlib

    cookie_value = request.cookies.get("admin_session")
    if not cookie_value or ":" not in cookie_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
        )

    try:
        key_part, sig_part = cookie_value.rsplit(":", 1)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed session cookie",
        )

    # Verify the key matches
    if not hmac.compare_digest(key_part, settings.admin_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin session",
        )

    # Verify the signature
    expected_sig = hmac.new(
        settings.cookie_secret.encode(),
        key_part.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(sig_part, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tampered session cookie",
        )

    return key_part
