from app.config import Settings
from app.preflight import Finding, configuration_findings, print_report


def configured_settings(**overrides):
    values = {
        "database_url": "postgresql+psycopg://user@localhost/sageops",
        "admin_google_client_id": "client-id",
        "admin_google_client_secret": "client-secret",
        "admin_google_redirect_uri": "https://sageops.example.com/auth/callback",
        "admin_emails": "admin@example.com",
        "admin_session_secret": "s" * 48,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_configuration_preflight_passes_required_database_and_auth_settings():
    findings = configuration_findings(configured_settings())
    assert findings == [Finding("PASS", "Configuration", "Required database and authentication settings are present.")]


def test_configuration_preflight_blocks_missing_database_and_weak_session_secret():
    findings = configuration_findings(configured_settings(database_url=None, admin_session_secret="weak"))
    assert any(x.component == "PostgreSQL" and x.severity == "BLOCKER" for x in findings)
    assert any(x.component == "Authentication" and x.severity == "BLOCKER" for x in findings)


def test_configuration_preflight_requires_secure_production_cookie():
    findings = configuration_findings(configured_settings(app_env="production", admin_cookie_secure=False))
    assert any("Secure session cookies" in x.message for x in findings)


def test_report_returns_failure_when_blockers_exist(capsys):
    assert print_report([Finding("BLOCKER", "PostgreSQL", "Database unavailable")]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_report_returns_zero_without_blockers(capsys):
    assert print_report([Finding("PASS", "PostgreSQL", "Database ready")]) == 0
    assert "PREFLIGHT PASSED" in capsys.readouterr().out
