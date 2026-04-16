"""
Suggestions Routes
===================

Public endpoint for submitting suggestions/feedback.
Rate-limited by IP address to prevent abuse.
Honeypot field rejects bot submissions silently.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status

from api.database import insert_suggestion
from api.schemas import SuggestionCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["suggestions"])

# In-memory rate limiter: IP -> list of timestamps
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX_REQUESTS = 5  # max 5 submissions per window


def _check_rate_limit(client_ip: str) -> None:
    """Enforce per-IP rate limiting on suggestion submissions."""
    now = time.time()
    # Prune old entries
    _rate_limit_store[client_ip] = [
        ts for ts in _rate_limit_store[client_ip]
        if now - ts < RATE_LIMIT_WINDOW
    ]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many submissions. Please try again later.",
        )
    _rate_limit_store[client_ip].append(now)


@router.post(
    "/suggestions",
    status_code=status.HTTP_201_CREATED,
    summary="Submit a suggestion or feedback",
)
async def submit_suggestion(
    body: SuggestionCreate,
    request: Request,
):
    """Public endpoint — no API key required.

    Security layers:
    1. Honeypot field: 'website' must be empty (bots auto-fill it)
    2. Rate limiting: max 5 submissions per IP per 5 minutes
    3. Input sanitization: HTML tags stripped, whitespace normalized
    4. Field-level validation: min/max lengths enforced by Pydantic
    """
    # Honeypot check - silently reject bots (return 201 to not tip them off)
    if body.website:
        logger.warning(
            "Honeypot triggered from IP %s",
            request.client.host if request.client else "unknown",
        )
        return {"id": "ok", "message": "Thank you for your feedback!"}

    # Rate limit check
    client_ip = request.client.host if request.client else "0.0.0.0"
    _check_rate_limit(client_ip)

    # Validate category
    allowed_categories = {"bug", "feature", "general", "other"}
    category = body.category.lower().strip()
    if category not in allowed_categories:
        category = "general"

    # Build the document
    doc = {
        "name": body.name,
        "email": body.email,
        "category": category,
        "message": body.message,
        "is_read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ip_address": client_ip,  # stored for abuse tracking, never exposed via API
    }

    suggestion_id = await insert_suggestion(doc)
    logger.info("New suggestion %s from %s (category: %s)", suggestion_id, client_ip, category)

    return {"id": suggestion_id, "message": "Thank you for your feedback!"}
