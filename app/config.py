"""Validated application configuration."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Polaris"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    timezone: str = "Africa/Lagos"

    telegram_bot_token: str | None = None
    telegram_public_chat_id: str | None = None
    telegram_core_chat_id: str | None = None

    google_sheet_id: str | None = None
    # Supports a service-account key file or a Google external-account/WIF config file.
    google_creds_path: str = "credentials/google-service-account.json"
    # Render production can use a dedicated Google user OAuth refresh token instead of a
    # downloadable service-account private key. Keep all three values in the host secret store.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_refresh_token: str | None = None

    enable_team_workflows: bool = False
    enable_scheduled_workflows: bool = False
    enable_analytics: bool = False
    enable_webhooks: bool = False
    webhook_secret: str | None = None
    webhook_db_path: str = "data/polaris-webhooks.sqlite3"
    webhook_max_skew_seconds: int = Field(default=300, ge=30, le=3600)
    webhook_max_body_bytes: int = Field(default=262144, ge=1024, le=2097152)
    job_descriptions_worksheet: str | None = None
    blocker_log_worksheet: str | None = None

    ga4_property_id: str | None = None
    youtube_channel_id: str | None = None
    youtube_api_key: str | None = None

    sheet_poll_interval_seconds: int = Field(default=60, ge=15, le=3600)
    reminder_cooldown_minutes: int = Field(default=240, ge=1, le=10080)
    max_reminders_per_task_per_day: int = Field(default=2, ge=1, le=20)
    reminder_receipts_db_path: str = "data/polaris-reminders.sqlite3"
    dry_run: bool = True

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
            raise ValueError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings object per process."""
    return Settings()
