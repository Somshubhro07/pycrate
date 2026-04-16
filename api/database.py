"""
MongoDB Database Client
========================

Async MongoDB connection via Motor (the async driver built on top of PyMongo).
Motor is designed for use with asyncio frameworks like FastAPI.

Connection lifecycle:
    - connect() is called during FastAPI startup (lifespan)
    - disconnect() is called during FastAPI shutdown
    - get_database() provides the database handle for route handlers

Collections:
    - containers: Container metadata and state
    - events: Container lifecycle events (created, started, stopped, etc.)
"""

from __future__ import annotations

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from api.config import get_settings

logger = logging.getLogger(__name__)

# Module-level client and database references.
# Initialized during app startup, cleaned up during shutdown.
_client: AsyncIOMotorClient | None = None
_database: AsyncIOMotorDatabase | None = None


async def connect() -> None:
    """Establish the MongoDB connection.

    Called once during FastAPI lifespan startup. Uses the connection URI
    from settings (PYCRATE_MONGODB_URI).
    """
    global _client, _database
    settings = get_settings()

    logger.info("Connecting to MongoDB at %s", _mask_uri(settings.mongodb_uri))

    _client = AsyncIOMotorClient(
        settings.mongodb_uri,
        # Connection pool settings tuned for a small single-host deployment
        maxPoolSize=10,
        minPoolSize=1,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    _database = _client[settings.mongodb_db_name]

    # Verify the connection works
    try:
        await _client.admin.command("ping")
        logger.info("MongoDB connection established (db=%s)", settings.mongodb_db_name)
    except Exception as e:
        logger.error("MongoDB connection failed: %s", e)
        raise

    # Ensure indexes exist
    await _ensure_indexes()


async def disconnect() -> None:
    """Close the MongoDB connection.

    Called during FastAPI lifespan shutdown.
    """
    global _client, _database
    if _client:
        _client.close()
        logger.info("MongoDB connection closed")
    _client = None
    _database = None


def get_database() -> AsyncIOMotorDatabase:
    """Get the active database handle.

    Returns:
        The Motor database instance.

    Raises:
        RuntimeError: If called before connect().
    """
    if _database is None:
        raise RuntimeError(
            "Database not initialized. Call connect() during app startup."
        )
    return _database


async def _ensure_indexes() -> None:
    """Create indexes if they don't exist.

    Called once during startup. MongoDB's createIndex is idempotent,
    so it's safe to call on every startup.
    """
    db = get_database()

    # containers: unique container_id, index on status for filtering
    await db.containers.create_index("container_id", unique=True)
    await db.containers.create_index("status")

    # events: compound index for querying events by container + time
    await db.events.create_index([("container_id", 1), ("timestamp", -1)])
    # TTL index: auto-delete events older than 7 days to keep Atlas M0 under 512MB
    await db.events.create_index("timestamp", expireAfterSeconds=7 * 24 * 3600)

    # suggestions: index on created_at for sorting, is_read for filtering
    await db.suggestions.create_index("created_at")
    await db.suggestions.create_index("is_read")

    logger.debug("Database indexes verified")


async def save_container(container_data: dict[str, Any]) -> None:
    """Upsert a container document.

    Uses container_id as the unique key. Updates the full document
    if it already exists.

    Args:
        container_data: Container state dictionary from Container.to_dict().
    """
    db = get_database()
    await db.containers.update_one(
        {"container_id": container_data["container_id"]},
        {"$set": container_data},
        upsert=True,
    )


async def get_container_doc(container_id: str) -> dict[str, Any] | None:
    """Retrieve a container document by ID.

    Args:
        container_id: The container's unique identifier.

    Returns:
        Container document dict, or None if not found.
    """
    db = get_database()
    doc = await db.containers.find_one(
        {"container_id": container_id},
        {"_id": 0},  # Exclude MongoDB's internal _id field
    )
    return doc


async def list_container_docs(
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """List all container documents, optionally filtered by status.

    Args:
        status_filter: If set, only return containers with this status.

    Returns:
        List of container document dicts.
    """
    db = get_database()
    query = {}
    if status_filter:
        query["status"] = status_filter

    cursor = db.containers.find(query, {"_id": 0})
    return await cursor.to_list(length=100)


async def delete_container_doc(container_id: str) -> None:
    """Delete a container document.

    Args:
        container_id: The container's unique identifier.
    """
    db = get_database()
    await db.containers.delete_one({"container_id": container_id})


async def insert_event(event: dict[str, Any]) -> None:
    """Insert a container lifecycle event.

    Events are time-stamped records of container state changes.
    Auto-deleted after 7 days via the TTL index.

    Args:
        event: Event document with container_id, event_type, message, timestamp.
    """
    db = get_database()
    await db.events.insert_one(event)


async def get_events(
    container_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve recent events for a container.

    Args:
        container_id: Container to get events for.
        limit: Maximum number of events to return.

    Returns:
        List of event documents, newest first.
    """
    db = get_database()
    cursor = db.events.find(
        {"container_id": container_id},
        {"_id": 0},
    ).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


def _mask_uri(uri: str) -> str:
    """Mask sensitive parts of the MongoDB URI for logging.

    Replaces the password portion with asterisks so URIs can be logged
    safely.
    """
    if "@" in uri and "://" in uri:
        prefix, rest = uri.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{prefix}://{user}:****@{host}"
    return uri


# ---------------------------------------------------------------------------
# Suggestions CRUD
# ---------------------------------------------------------------------------


async def insert_suggestion(suggestion: dict[str, Any]) -> str:
    """Insert a new suggestion and return its string ID."""
    db = get_database()
    result = await db.suggestions.insert_one(suggestion)
    return str(result.inserted_id)


async def list_suggestions(
    is_read: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List suggestions, optionally filtered by read status."""
    db = get_database()
    query: dict[str, Any] = {}
    if is_read is not None:
        query["is_read"] = is_read

    cursor = db.suggestions.find(query).sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        results.append(doc)
    return results


async def mark_suggestion_read(suggestion_id: str) -> bool:
    """Mark a suggestion as read. Returns True if found."""
    from bson import ObjectId
    db = get_database()
    result = await db.suggestions.update_one(
        {"_id": ObjectId(suggestion_id)},
        {"$set": {"is_read": True}},
    )
    return result.modified_count > 0


async def delete_suggestion(suggestion_id: str) -> bool:
    """Delete a suggestion. Returns True if found."""
    from bson import ObjectId
    db = get_database()
    result = await db.suggestions.delete_one({"_id": ObjectId(suggestion_id)})
    return result.deleted_count > 0


async def count_suggestions(is_read: bool | None = None) -> int:
    """Count suggestions, optionally filtered by read status."""
    db = get_database()
    query: dict[str, Any] = {}
    if is_read is not None:
        query["is_read"] = is_read
    return await db.suggestions.count_documents(query)

