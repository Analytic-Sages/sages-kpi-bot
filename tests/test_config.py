import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_safe_for_local_development():
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.dry_run is True
    assert settings.enable_team_writes is False
    assert settings.enable_bot_writes is False
    assert settings.enable_dry_run_team_replies is False
    assert settings.enable_safe_telegram_replies is False
    assert settings.timezone == "Africa/Lagos"
    assert settings.telegram_bot_token is None


def test_invalid_port_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, port=70000)


def test_invalid_timezone_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, timezone="Not/A_Timezone")


def test_log_level_is_normalized():
    settings = Settings(_env_file=None, log_level="debug")
    assert settings.log_level == "DEBUG"
