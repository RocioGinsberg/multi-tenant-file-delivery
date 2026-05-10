from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Runtime ──
    env: str = "development"

    # ── Database ──
    database_url: str = "sqlite+aiosqlite:///./control_plane.db"

    # ── S3-compatible object storage ──
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket_name: str = "auto-upload-dev"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"

    # ── Worker concurrency (used by 1.8 task_runner) ──
    worker_max_team_concurrent: int = 3
    worker_max_file_concurrent: int = 5
    worker_auto_adjust_concurrent: bool = True

    # ── Task working directory (zip extraction; used by 1.5 / 1.8) ──
    task_dir_base: str = "/tmp/auto_upload_tasks"

    # ── Zip receive limits (used by 1.5 / 1.9) ──
    max_zip_bytes: int = 524_288_000  # 500 MB
    max_unzipped_bytes: int = 1_073_741_824  # 1 GB
    max_file_count: int = 5000

    # ── CORS (used by 1.9 / main.py) ──
    # Accepts "*" or comma-separated origins; consumers call .split(",")
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton; call get_settings.cache_clear() in tests."""
    return Settings()
