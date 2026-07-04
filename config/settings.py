import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Load from .env if present
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Database Configuration
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/datalake"
    SYNC_DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/datalake"

    # Redis Configuration
    REDIS_URL: str = "redis://localhost:6379/0"

    # App Configuration
    ENV: str = "development"
    API_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    LOG_ROTATION: str = "10 MB"
    LOG_RETENTION: str = "14 days"

    # Crawler Settings
    CRAWLER_USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    API_RATE_LIMIT_DELAY: float = 0.5
    PROXY_URL: Optional[str] = None

    # Backup Settings
    BACKUP_DIR: str = "./backups"
    BACKUP_INTERVAL_HOURS: int = 24

    # Mercado Livre App OAuth Credentials
    ML_CLIENT_ID: str = ""
    ML_CLIENT_SECRET: str = ""
    ML_REDIRECT_URI: str = "https://brasilices.tech/selldata/api/auth/callback"

    # IntelligentCollector tuning
    COLLECTOR_MAX_CONCURRENCY: int = 5
    COLLECTOR_MAX_RETRIES: int = 5
    COLLECTOR_BACKOFF_BASE: float = 2.0
    COLLECTOR_MAX_WAIT: float = 60.0
    COLLECTOR_INCREMENTAL_THRESHOLD_HOURS: int = 6

    # Intelligence Engine
    INTELLIGENCE_ANALYSIS_INTERVAL_HOURS: int = 12
    INTELLIGENCE_ANALYSIS_DAYS: int = 30
    INTELLIGENCE_MAX_PRODUCTS_PER_CYCLE: int = 20

    # Cache TTLs
    CACHE_HASH_TTL_SECONDS: int = 3600
    CACHE_PAYLOAD_TTL_SECONDS: int = 300

# Instantiate settings
settings = Settings()
