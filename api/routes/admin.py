"""
Admin Routes
==============

Cookie-based admin authentication for the admin panel.

Flow:
1. POST /api/admin/login  — validates admin_key, sets HttpOnly cookie
2. GET  /api/admin/me      — checks if current cookie is valid
3. POST /api/admin/logout  — clears the cookie
4. GET  /api/admin/suggestions — lists all suggestions (requires auth)
5. PATCH /api/admin/suggestions/{id}/read — marks a suggestion read
6. DELETE /api/admin/suggestions/{id} — deletes a suggestion
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from api.config import Settings, get_settings
from api.database import (
    count_suggestions,
    delete_suggestion,
    list_suggestions,
    mark_suggestion_read,
)
from api.dependencies import verify_admin_cookie
from api.schemas import AdminLoginRequest, SuggestionListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _sign_key(admin_key: str, cookie_secret: str) -> str:
    """Create a signed cookie value: {key}:{hmac_signature}."""
    sig = hmac.new(
        cookie_secret.encode(),
        admin_key.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{admin_key}:{sig}"


@router.post("/login", summary="Authenticate as admin")
async def admin_login(
    body: AdminLoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Validate the admin key and set an HttpOnly session cookie.

    Security:
    - Constant-time comparison to prevent timing attacks
    - HttpOnly cookie: not accessible via JavaScript (XSS protection)
    - SameSite=Lax: CSRF protection
    - Secure flag set in production (HTTPS only)
    """
    if not hmac.compare_digest(body.admin_key, settings.admin_key):
        logger.warning(
            "Failed admin login attempt from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
        )

    # Build signed cookie
    cookie_value = _sign_key(settings.admin_key, settings.cookie_secret)

    # Determine if we're behind HTTPS
    is_secure = request.url.scheme == "https"

    response = JSONResponse(
        content={"authenticated": True, "message": "Login successful"},
    )
    response.set_cookie(
        key="admin_session",
        value=cookie_value,
        httponly=True,       # XSS protection: JS can't read this
        samesite="lax",      # CSRF protection
        secure=is_secure,    # Only send over HTTPS in production
        max_age=86400,       # 24 hours
        path="/",
    )

    client_ip = request.client.host if request.client else "unknown"
    logger.info("Admin login successful from %s", client_ip)

    return response


@router.post("/logout", summary="Clear admin session")
async def admin_logout():
    """Clear the admin session cookie."""
    response = JSONResponse(content={"authenticated": False})
    response.delete_cookie(
        key="admin_session",
        path="/",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/me", summary="Check admin authentication status")
async def admin_me(
    _admin: str = Depends(verify_admin_cookie),
):
    """Returns 200 if the admin cookie is valid, 401 otherwise."""
    return {"authenticated": True}


@router.get(
    "/suggestions",
    response_model=SuggestionListResponse,
    summary="List all suggestions (admin only)",
)
async def get_suggestions(
    is_read: bool | None = None,
    limit: int = 100,
    _admin: str = Depends(verify_admin_cookie),
):
    """Retrieve suggestions with optional read/unread filter."""
    suggestions = await list_suggestions(is_read=is_read, limit=limit)
    total = await count_suggestions()
    unread = await count_suggestions(is_read=False)

    return SuggestionListResponse(
        suggestions=suggestions,
        total=total,
        unread=unread,
    )


@router.patch(
    "/suggestions/{suggestion_id}/read",
    summary="Mark a suggestion as read",
)
async def mark_read(
    suggestion_id: str,
    _admin: str = Depends(verify_admin_cookie),
):
    """Mark a specific suggestion as read."""
    found = await mark_suggestion_read(suggestion_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )
    return {"id": suggestion_id, "is_read": True}


@router.delete(
    "/suggestions/{suggestion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a suggestion",
)
async def remove_suggestion(
    suggestion_id: str,
    _admin: str = Depends(verify_admin_cookie),
):
    """Permanently delete a suggestion."""
    found = await delete_suggestion(suggestion_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )
