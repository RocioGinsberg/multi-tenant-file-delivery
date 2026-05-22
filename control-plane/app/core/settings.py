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

    # ── Auth / actor context (Phase 6) ──
    auth_allow_dev_headers: bool = True
    auth_default_actor_enabled: bool = True
    auth_default_tenant_id: str = "hq"
    auth_default_user_id: str = "local-user"
    auth_default_role: str = "hq_uploader"
    auth_actor_tenant_header: str = "X-Actor-Tenant"
    auth_actor_user_header: str = "X-Actor-User"
    auth_actor_role_header: str = "X-Actor-Role"

    # ── S3-compatible object storage ──
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket_name: str = "auto-upload-dev"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"
    staging_bucket_name: str = "auto-upload-staging"
    workspace_download_url_ttl_seconds: int = 300

    # ── Worker concurrency (used by 1.8 task_runner) ──
    worker_max_target_concurrent: int = 3
    worker_max_file_concurrent: int = 5
    worker_auto_adjust_concurrent: bool = True

    # ── Task working directory (folder extraction and internal archive staging) ──
    task_dir_base: str = "/tmp/auto_upload_tasks"

    # ── Delivery backend (Phase 2 bridge) ──
    # "python" keeps the Phase 1 in-process uploader.
    # "go-worker" writes a task message into a durable outbox for the Go worker.
    delivery_backend: str = "python"
    delivery_outbox_base: str = "/tmp/auto_upload_outbox"
    delivery_transport: str = "file"
    delivery_source_mode: str = "file"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_task_topic: str = "delivery.tasks.v1"
    kafka_result_topic: str = "delivery.results.v1"
    kafka_result_group_id: str = "control-plane-results"

    # ── Redis capability layer (Phase 4) ──
    redis_url: str = "redis://localhost:6379/0"
    progress_backend: str = "memory"
    redis_socket_timeout_seconds: float = 1.0
    redis_healthcheck_enabled: bool = False
    redis_idempotency_enabled: bool = False
    redis_idempotency_ttl_seconds: int = 60
    redis_lease_enabled: bool = False
    redis_lease_ttl_seconds: int = 30

    # ── Observability (Phase 5) ──
    observability_enabled: bool = False
    service_name: str = "control-plane"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    metrics_enabled: bool = False
    metrics_path: str = "/metrics"

    # ── Classification profile (used by 1.5 classifier) ──
    classification_profile_path: str = "../profiles/hq_subsidiary_reports_v1/profile.json"

    # ── Folder upload / internal archive limits ──
    max_internal_archive_bytes: int = 524_288_000  # 500 MB
    max_folder_payload_bytes: int = 1_073_741_824  # 1 GB
    max_file_count: int = 5000

    # ── CORS (used by 1.9 / main.py) ──
    # Accepts "*" or comma-separated origins; consumers call .split(",")
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton; call get_settings.cache_clear() in tests."""
    return Settings()
