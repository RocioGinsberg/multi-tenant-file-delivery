"""Settings for the standalone CosDrive local service."""

from __future__ import annotations

import os

from pydantic import BaseModel


class Settings(BaseModel):
    app_env: str = os.getenv("APP_ENV", "local").strip().lower() or "local"
    git_sha: str = os.getenv("GIT_SHA", "dev")
    service_name: str = os.getenv("COSDRIVE_SERVICE_NAME", "cosdrive-local-service")

    cors_origins: list[str] = [
        item.strip()
        for item in os.getenv("CORS_ORIGINS", "*").split(",")
        if item.strip()
    ]
    auth_mode: str = os.getenv("AUTH_MODE", "header").strip().lower() or "header"
    prefect_ui_base_url: str = os.getenv("PREFECT_UI_BASE_URL", "").rstrip("/")

    portal_db_host: str = os.getenv("PORTAL_DB_HOST", "portal-db")
    portal_db_port: int = int(os.getenv("PORTAL_DB_PORT", "5432"))
    portal_db_name: str = os.getenv("PORTAL_DB_NAME", "portal")
    portal_db_user: str = os.getenv("PORTAL_DB_USER", "portal")
    portal_db_password: str = os.getenv("PORTAL_DB_PASSWORD", "portal")

    @property
    def portal_database_url(self) -> str:
        return os.getenv("PORTAL_DATABASE_URL") or (
            f"postgresql://{self.portal_db_user}:{self.portal_db_password}"
            f"@{self.portal_db_host}:{self.portal_db_port}/{self.portal_db_name}"
        )

    cosdrive_enabled: bool = os.getenv("COSDRIVE_ENABLED", "true").lower() == "true"
    cosdrive_allowed_prefixes: list[str] = [
        item.strip()
        for item in os.getenv("COSDRIVE_ALLOWED_PREFIXES", "/交付/,/团队共享/,/存档/").split(",")
        if item.strip()
    ]
    cosdrive_job_dir: str = os.getenv("COSDRIVE_JOB_DIR", "/tmp/cosdrive_jobs")
    cosdrive_dispatch_mode: str = (
        os.getenv("COSDRIVE_DISPATCH_MODE", "local-process").strip().lower() or "local-process"
    )
    legend_app_root: str = os.getenv(
        "LEGEND_APP_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    )

    smh_enabled: bool = os.getenv("SMH_ENABLED", "true").lower() == "true"
    smh_app_id: str = os.getenv("SMH_APP_ID", "")
    smh_org_id: str = os.getenv("SMH_ORG_ID", "")
    smh_library_id: str = os.getenv("SMH_LIBRARY_ID", "")
    smh_private_key_file: str = os.getenv("SMH_PRIVATE_KEY_FILE", "")
    smh_country_code: str = os.getenv("SMH_COUNTRY_CODE", "+86")
    smh_phone_number: str = os.getenv("SMH_PHONE_NUMBER", "")
    smh_token_ttl_seconds: int = int(os.getenv("SMH_TOKEN_TTL_SECONDS", "300"))
    smh_api_base_public: str = (
        os.getenv("SMH_API_BASE_PUBLIC", "https://api.tencentsmh.cn").rstrip("/")
    )
    smh_api_base_app: str = (
        os.getenv("SMH_API_BASE_APP", "https://api.tencentsmh.cn/api/v1").rstrip("/")
    )


settings = Settings()
