"""Validated SageOps environment configuration."""
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SageOps"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    timezone: str = "Africa/Lagos"
    database_url: str | None = None
    operations_db_writes_enabled: bool = False

    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_public_chat_id: str | None = None
    telegram_core_chat_id: str | None = None
    operations_notifications_enabled: bool = False
    operations_worker_interval_seconds: int = Field(default=60, ge=15, le=3600)
    operations_kpi_reminder_weekday: int = Field(default=4, ge=0, le=6)
    operations_review_reminder_weekday: int = Field(default=5, ge=0, le=6)
    telegram_test_mode: bool = True

    admin_google_client_id: str | None = None
    admin_google_client_secret: str | None = None
    admin_google_redirect_uri: str | None = None
    admin_emails: str = ""
    admin_session_secret: str | None = None
    admin_cookie_secure: bool = True
    sentry_dsn: str | None = None

    preflight_token: str | None = None
    enable_webhooks: bool = False
    webhook_secret: str | None = None
    webhook_max_skew_seconds: int = Field(default=300, ge=30, le=3600)
    webhook_max_body_bytes: int = Field(default=262144, ge=1024, le=2097152)

    reminder_cooldown_minutes: int = Field(default=240, ge=1, le=10080)
    max_reminders_per_task_per_day: int = Field(default=2, ge=1, le=20)
    dry_run: bool = True

    # Optional growth analytics retained for deployments that use those providers.
    google_creds_path: str = "credentials/google-analytics.json"
    enable_analytics: bool = False
    ga4_property_id: str | None = None
    youtube_channel_id: str | None = None
    youtube_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper().strip()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("Invalid LOG_LEVEL")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
