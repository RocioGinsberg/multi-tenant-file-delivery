"""Tests for app.core.settings — Settings class and get_settings() singleton."""

from __future__ import annotations

import pytest

from app.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the lru_cache before and after each test to ensure isolation."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestDefaultValues:
    """Settings falls back to documented defaults when no .env or env vars are set."""

    def test_defaults_are_correct(self, monkeypatch, tmp_path):
        # Point env_file at a non-existent path so no .env is loaded
        monkeypatch.chdir(tmp_path)

        # Remove env vars that might be set in the shell
        for var in (
            "ENV",
            "DATABASE_URL",
            "AUTH_ALLOW_DEV_HEADERS",
            "AUTH_DEFAULT_ACTOR_ENABLED",
            "AUTH_DEFAULT_TENANT_ID",
            "AUTH_DEFAULT_USER_ID",
            "AUTH_DEFAULT_ROLE",
            "AUTH_ACTOR_TENANT_HEADER",
            "AUTH_ACTOR_USER_HEADER",
            "AUTH_ACTOR_ROLE_HEADER",
            "S3_ENDPOINT_URL",
            "S3_BUCKET_NAME",
            "S3_REGION",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "WORKER_MAX_TARGET_CONCURRENT",
            "WORKER_MAX_FILE_CONCURRENT",
            "WORKER_AUTO_ADJUST_CONCURRENT",
            "TASK_DIR_BASE",
            "DELIVERY_BACKEND",
            "DELIVERY_OUTBOX_BASE",
            "REDIS_URL",
            "PROGRESS_BACKEND",
            "REDIS_SOCKET_TIMEOUT_SECONDS",
            "REDIS_HEALTHCHECK_ENABLED",
            "REDIS_IDEMPOTENCY_ENABLED",
            "REDIS_IDEMPOTENCY_TTL_SECONDS",
            "REDIS_LEASE_ENABLED",
            "REDIS_LEASE_TTL_SECONDS",
            "OBSERVABILITY_ENABLED",
            "SERVICE_NAME",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "METRICS_ENABLED",
            "METRICS_PATH",
            "CLASSIFICATION_PROFILE_PATH",
            "MAX_INTERNAL_ARCHIVE_BYTES",
            "MAX_FOLDER_PAYLOAD_BYTES",
            "MAX_FILE_COUNT",
            "CORS_ORIGINS",
        ):
            monkeypatch.delenv(var, raising=False)

        s = Settings()

        assert s.env == "development"
        assert s.database_url == "sqlite+aiosqlite:///./control_plane.db"
        assert s.auth_allow_dev_headers is True
        assert s.auth_default_actor_enabled is True
        assert s.auth_default_tenant_id == "hq"
        assert s.auth_default_user_id == "local-user"
        assert s.auth_default_role == "hq_uploader"
        assert s.auth_actor_tenant_header == "X-Actor-Tenant"
        assert s.auth_actor_user_header == "X-Actor-User"
        assert s.auth_actor_role_header == "X-Actor-Role"
        assert s.s3_endpoint_url == "http://localhost:9000"
        assert s.s3_bucket_name == "auto-upload-dev"
        assert s.s3_region == "us-east-1"
        assert s.s3_access_key_id == "minioadmin"
        assert s.s3_secret_access_key == "minioadmin"
        assert s.worker_max_target_concurrent == 3
        assert s.worker_max_file_concurrent == 5
        assert s.worker_auto_adjust_concurrent is True
        assert s.task_dir_base == "/tmp/auto_upload_tasks"
        assert s.classification_profile_path == "../profiles/hq_subsidiary_reports_v1/profile.json"
        assert s.max_internal_archive_bytes == 524_288_000
        assert s.max_folder_payload_bytes == 1_073_741_824
        assert s.max_file_count == 5000
        assert s.cors_origins == "*"
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.progress_backend == "memory"
        assert s.redis_socket_timeout_seconds == 1.0
        assert s.redis_healthcheck_enabled is False
        assert s.redis_idempotency_enabled is False
        assert s.redis_idempotency_ttl_seconds == 60
        assert s.redis_lease_enabled is False
        assert s.redis_lease_ttl_seconds == 30
        assert s.observability_enabled is False
        assert s.service_name == "control-plane"
        assert s.otel_exporter_otlp_endpoint == "http://localhost:4318"
        assert s.metrics_enabled is False
        assert s.metrics_path == "/metrics"


class TestDotEnvOverride:
    """Values written to a temporary .env file override the built-in defaults."""

    def test_dotenv_values_are_loaded(self, monkeypatch, tmp_path):
        # Remove any ambient env vars so only the .env file speaks
        for var in (
            "ENV",
            "AUTH_ALLOW_DEV_HEADERS",
            "AUTH_DEFAULT_ACTOR_ENABLED",
            "AUTH_DEFAULT_TENANT_ID",
            "AUTH_DEFAULT_USER_ID",
            "AUTH_DEFAULT_ROLE",
            "S3_BUCKET_NAME",
            "WORKER_MAX_TARGET_CONCURRENT",
            "REDIS_URL",
            "PROGRESS_BACKEND",
            "REDIS_SOCKET_TIMEOUT_SECONDS",
            "REDIS_HEALTHCHECK_ENABLED",
            "REDIS_IDEMPOTENCY_ENABLED",
            "REDIS_IDEMPOTENCY_TTL_SECONDS",
            "REDIS_LEASE_ENABLED",
            "REDIS_LEASE_TTL_SECONDS",
            "OBSERVABILITY_ENABLED",
            "SERVICE_NAME",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "METRICS_ENABLED",
            "METRICS_PATH",
            "CLASSIFICATION_PROFILE_PATH",
            "CORS_ORIGINS",
        ):
            monkeypatch.delenv(var, raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "ENV=staging\n"
            "AUTH_ALLOW_DEV_HEADERS=false\n"
            "AUTH_DEFAULT_ACTOR_ENABLED=false\n"
            "AUTH_DEFAULT_TENANT_ID=subsidiary-a\n"
            "AUTH_DEFAULT_USER_ID=sub-user\n"
            "AUTH_DEFAULT_ROLE=subsidiary_viewer\n"
            "S3_BUCKET_NAME=my-staging-bucket\n"
            "WORKER_MAX_TARGET_CONCURRENT=10\n"
            "REDIS_URL=redis://redis.example.com:6379/2\n"
            "PROGRESS_BACKEND=redis\n"
            "REDIS_SOCKET_TIMEOUT_SECONDS=2.5\n"
            "REDIS_HEALTHCHECK_ENABLED=true\n"
            "REDIS_IDEMPOTENCY_ENABLED=true\n"
            "REDIS_IDEMPOTENCY_TTL_SECONDS=30\n"
            "REDIS_LEASE_ENABLED=true\n"
            "REDIS_LEASE_TTL_SECONDS=45\n"
            "OBSERVABILITY_ENABLED=true\n"
            "SERVICE_NAME=control-plane-test\n"
            "OTEL_EXPORTER_OTLP_ENDPOINT=http://otel:4318\n"
            "METRICS_ENABLED=true\n"
            "METRICS_PATH=/internal/metrics\n"
            "CLASSIFICATION_PROFILE_PATH=../profiles/custom/profile.json\n"
            "CORS_ORIGINS=http://example.com,http://app.example.com\n"
        )
        monkeypatch.chdir(tmp_path)

        s = Settings()

        assert s.env == "staging"
        assert s.auth_allow_dev_headers is False
        assert s.auth_default_actor_enabled is False
        assert s.auth_default_tenant_id == "subsidiary-a"
        assert s.auth_default_user_id == "sub-user"
        assert s.auth_default_role == "subsidiary_viewer"
        assert s.s3_bucket_name == "my-staging-bucket"
        assert s.worker_max_target_concurrent == 10
        assert s.redis_url == "redis://redis.example.com:6379/2"
        assert s.progress_backend == "redis"
        assert s.redis_socket_timeout_seconds == 2.5
        assert s.redis_healthcheck_enabled is True
        assert s.redis_idempotency_enabled is True
        assert s.redis_idempotency_ttl_seconds == 30
        assert s.redis_lease_enabled is True
        assert s.redis_lease_ttl_seconds == 45
        assert s.observability_enabled is True
        assert s.service_name == "control-plane-test"
        assert s.otel_exporter_otlp_endpoint == "http://otel:4318"
        assert s.metrics_enabled is True
        assert s.metrics_path == "/internal/metrics"
        assert s.classification_profile_path == "../profiles/custom/profile.json"
        assert s.cors_origins == "http://example.com,http://app.example.com"


class TestEnvVarPriority:
    """Actual environment variables take precedence over .env file values."""

    def test_env_var_overrides_dotenv(self, monkeypatch, tmp_path):
        # Write a .env file with one value …
        env_file = tmp_path / ".env"
        env_file.write_text("ENV=from_dotenv\nS3_BUCKET_NAME=dotenv-bucket\n")
        monkeypatch.chdir(tmp_path)

        # … then set a real env var with a different value
        monkeypatch.setenv("ENV", "from_env_var")
        monkeypatch.setenv("S3_BUCKET_NAME", "envvar-bucket")

        s = Settings()

        assert s.env == "from_env_var"
        assert s.s3_bucket_name == "envvar-bucket"

    def test_folder_archive_limit_env_names(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MAX_INTERNAL_ARCHIVE_BYTES", "123")
        monkeypatch.setenv("MAX_FOLDER_PAYLOAD_BYTES", "456")
        monkeypatch.setenv("MAX_ZIP_BYTES", "789")
        monkeypatch.setenv("MAX_UNZIPPED_BYTES", "999")

        s = Settings()

        assert s.max_internal_archive_bytes == 123
        assert s.max_folder_payload_bytes == 456
        assert not hasattr(s, "max_zip_bytes")
        assert not hasattr(s, "max_unzipped_bytes")


class TestGetSettingsSingleton:
    """get_settings() returns the same object on repeated calls (lru_cache)."""

    def test_singleton_identity(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        first = get_settings()
        second = get_settings()

        assert first is second

    def test_cache_clear_produces_new_instance(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        first = get_settings()
        get_settings.cache_clear()
        second = get_settings()

        # After clearing, a brand-new object is created
        assert first is not second
