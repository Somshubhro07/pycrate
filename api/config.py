"""
API Configuration
==================

Loads settings from environment variables with the PYCRATE_ prefix.
Uses pydantic-settings for type-safe config with validation and defaults.

All config values are documented in .env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Every field maps to an environment variable with the PYCRATE_ prefix.
    Example: PYCRATE_API_KEY -> api_key
    """

    model_config = SettingsConfigDict(
        env_prefix="PYCRATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # -- API Security --
    api_key: str = "change-me-to-a-secure-random-string"

    # -- Admin Panel --
    admin_key: str = "change-me-to-admin-key"
    cookie_secret: str = "change-me-to-cookie-secret"

    # -- MongoDB --
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "pycrate"

    # -- Server --
    host: str = "0.0.0.0"
    port: int = 8000

    # -- CORS --
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # -- Engine --
    data_dir: str = "/var/lib/pycrate"
    max_containers: int = 4
    alpine_version: str = "3.19"

    # -- Logging --
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. Loaded once, reused everywhere.

    Using lru_cache instead of a global so settings can be overridden
    in tests by clearing the cache.
    """
    return Settings()
