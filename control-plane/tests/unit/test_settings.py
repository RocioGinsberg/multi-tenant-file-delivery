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
            "CLASSIFICATION_PROFILE_PATH",
            "MAX_ZIP_BYTES",
            "MAX_UNZIPPED_BYTES",
            "MAX_FILE_COUNT",
            "CORS_ORIGINS",
        ):
            monkeypatch.delenv(var, raising=False)

        s = Settings()

        assert s.env == "development"
        assert s.database_url == "sqlite+aiosqlite:///./control_plane.db"
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
        assert s.max_zip_bytes == 524_288_000
        assert s.max_unzipped_bytes == 1_073_741_824
        assert s.max_file_count == 5000
        assert s.cors_origins == "*"
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.progress_backend == "memory"
        assert s.redis_socket_timeout_seconds == 1.0
        assert s.redis_healthcheck_enabled is False


class TestDotEnvOverride:
    """Values written to a temporary .env file override the built-in defaults."""

    def test_dotenv_values_are_loaded(self, monkeypatch, tmp_path):
        # Remove any ambient env vars so only the .env file speaks
        for var in (
            "ENV",
            "S3_BUCKET_NAME",
            "WORKER_MAX_TARGET_CONCURRENT",
            "REDIS_URL",
            "PROGRESS_BACKEND",
            "REDIS_SOCKET_TIMEOUT_SECONDS",
            "REDIS_HEALTHCHECK_ENABLED",
            "CLASSIFICATION_PROFILE_PATH",
            "CORS_ORIGINS",
        ):
            monkeypatch.delenv(var, raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "ENV=staging\n"
            "S3_BUCKET_NAME=my-staging-bucket\n"
            "WORKER_MAX_TARGET_CONCURRENT=10\n"
            "REDIS_URL=redis://redis.example.com:6379/2\n"
            "PROGRESS_BACKEND=redis\n"
            "REDIS_SOCKET_TIMEOUT_SECONDS=2.5\n"
            "REDIS_HEALTHCHECK_ENABLED=true\n"
            "CLASSIFICATION_PROFILE_PATH=../profiles/custom/profile.json\n"
            "CORS_ORIGINS=http://example.com,http://app.example.com\n"
        )
        monkeypatch.chdir(tmp_path)

        s = Settings()

        assert s.env == "staging"
        assert s.s3_bucket_name == "my-staging-bucket"
        assert s.worker_max_target_concurrent == 10
        assert s.redis_url == "redis://redis.example.com:6379/2"
        assert s.progress_backend == "redis"
        assert s.redis_socket_timeout_seconds == 2.5
        assert s.redis_healthcheck_enabled is True
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
