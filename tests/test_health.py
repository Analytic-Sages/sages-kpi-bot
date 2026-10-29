from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

client = TestClient(app)


def test_root_reports_running_service():
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "Polaris"
    assert payload["status"] == "running"


def test_liveness_probe():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_does_not_claim_integrations_are_connected(monkeypatch):
    monkeypatch.setattr(main_module.settings, "telegram_bot_token", None)

    response = client.get("/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["integrations"]["analytics"] == "not_enabled"


def test_deployment_preflight_is_hidden_without_token(monkeypatch):
    monkeypatch.setattr(main_module.settings, "preflight_token", None)
    response = client.get("/admin/preflight")
    assert response.status_code == 404


def test_deployment_preflight_rejects_invalid_bearer_token(monkeypatch):
    monkeypatch.setattr(main_module.settings, "preflight_token", "a" * 40)
    response = client.get(
        "/admin/preflight",
        headers={"Authorization": "Bearer " + "b" * 40},
    )
    assert response.status_code == 401


def test_deployment_preflight_returns_only_header_check_findings(monkeypatch):
    token = "a" * 40
    monkeypatch.setattr(main_module.settings, "preflight_token", token)
    monkeypatch.setattr(main_module.settings, "google_sheet_id", "spreadsheet-id")
    monkeypatch.setattr(main_module.GoogleSheetsClient, "from_settings", lambda settings: object())
    monkeypatch.setattr(main_module, "sheet_findings", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module, "row_integrity_findings", AsyncMock(return_value=[]))

    response = client.get(
        "/admin/preflight",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "passed", "checks": []}


def test_telegram_health_is_degraded_when_not_configured(monkeypatch):
    monkeypatch.setattr(main_module.settings, "telegram_bot_token", None)
    response = client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "not_configured"


def test_telegram_health_requires_recent_successful_poll(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    monkeypatch.setattr(main_module.settings, "telegram_bot_token", "configured")
    bot = SimpleNamespace(last_poll_success_at=datetime.now(UTC) - timedelta(minutes=5))
    task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(app.state, "telegram_bot", bot, raising=False)
    monkeypatch.setattr(app.state, "telegram_task", task, raising=False)

    response = client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["polling_task_alive"] is True


def test_telegram_health_is_healthy_when_polling_is_recent(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    monkeypatch.setattr(main_module.settings, "telegram_bot_token", "configured")
    bot = SimpleNamespace(last_poll_success_at=datetime.now(UTC))
    task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(app.state, "telegram_bot", bot, raising=False)
    monkeypatch.setattr(app.state, "telegram_task", task, raising=False)

    response = client.get("/health/telegram")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_privacy_policy_is_public_and_contains_required_sections():
    response = client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "Google User Data" in response.text
    assert "Google API Services User Data Policy" in response.text
