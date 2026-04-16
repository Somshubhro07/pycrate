"""
FastAPI Application
====================

The main application module. Assembles routes, configures middleware,
and manages the application lifecycle (startup/shutdown).

Run with:
    sudo uvicorn api.main:app --host 0.0.0.0 --port 8000

The app requires root privileges because namespace and cgroup operations
need CAP_SYS_ADMIN.
"""

from __future__ import annotations

import logging
import platform
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import get_settings
from api.database import connect as db_connect, disconnect as db_disconnect
from api.dependencies import set_engine
from api.routes import admin, containers, suggestions, system, websockets
from engine import PyCrateError, __version__

# Configure logging
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pycrate")

IS_LINUX = platform.system() == "Linux"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Startup:
        1. Connect to MongoDB
        2. Initialize the container engine (cgroups + bridge) — Linux only
        3. Set the engine singleton for dependency injection

    Shutdown:
        1. Gracefully stop all running containers
        2. Disconnect from MongoDB
    """
    logger.info("PyCrate v%s starting up", __version__)

    # Database
    await db_connect()

    # Engine — Linux only (requires namespaces, cgroups, root)
    engine = None
    if IS_LINUX:
        from engine.container import ContainerManager
        engine = ContainerManager(max_containers=settings.max_containers)
        engine.initialize()
        set_engine(engine)
    else:
        logger.warning(
            "Non-Linux platform (%s) — engine disabled. "
            "Container routes will return 503. "
            "Suggestions and admin routes are fully functional.",
            platform.system(),
        )

    logger.info("PyCrate ready on %s:%d", settings.host, settings.port)

    yield

    # Shutdown
    logger.info("PyCrate shutting down")
    if engine:
        engine.shutdown()
    await db_disconnect()


# Create the FastAPI app
app = FastAPI(
    title="PyCrate",
    description=(
        "Container runtime API. Create, manage, and monitor isolated Linux "
        "processes with namespace, cgroup, and rootfs isolation."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware for the dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(containers.router)
app.include_router(system.router)
app.include_router(websockets.router)
app.include_router(suggestions.router)
app.include_router(admin.router)


# Global exception handler for engine errors
@app.exception_handler(PyCrateError)
async def pycrate_error_handler(request: Request, exc: PyCrateError):
    """Convert engine exceptions to JSON error responses.

    Any PyCrateError that escapes a route handler (shouldn't happen
    normally, but serves as a safety net) gets a structured response.
    """
    return JSONResponse(
        status_code=500,
        content={
            "error": exc.message,
            "code": exc.code,
        },
    )


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint. Returns basic API info."""
    return {
        "name": "PyCrate",
        "version": __version__,
        "docs": "/docs",
    }
