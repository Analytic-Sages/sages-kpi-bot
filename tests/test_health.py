from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

client = TestClient(app)


def test_root_reports_running_service():
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "SageOps"
    assert payload["status"] == "running"


def test_liveness_probe():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_does_not_claim_integrations_are_connected(monkeypatch):
    monkeypatch.setattr(main_module.settings, "telegram_bot_token", None)
    monkeypatch.setattr(main_module, "_database_check", lambda: (True, "0003_ops_persistence"))

    response = client.get("/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["integrations"]["postgresql"] == "connected"
    assert "google_sheets" not in payload["integrations"]


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


def test_deployment_preflight_checks_postgresql_only(monkeypatch):
    token = "a" * 40
    monkeypatch.setattr(main_module.settings, "preflight_token", token)
    monkeypatch.setattr(main_module, "_database_check", lambda: (True, "0003_ops_persistence"))

    response = client.get(
        "/admin/preflight",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "passed",
        "checks": [{"severity": "INFO", "component": "PostgreSQL", "message": "Connection and migration revision 0003_ops_persistence verified."}],
    }


def test_telegram_health_is_degraded_when_not_configured(monkeypatch):
    monkeypatch.setattr(main_module.settings, "telegram_bot_token", None)
    monkeypatch.setattr(main_module.settings, "telegram_webhook_secret", None)
    response = client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "not_configured"


def test_telegram_health_requires_webhook_configuration(monkeypatch):
    monkeypatch.setattr(main_module.settings, "telegram_bot_token", "configured")
    monkeypatch.setattr(main_module.settings, "telegram_webhook_secret", None)

    response = client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "not_configured"


def test_telegram_health_is_healthy_when_webhook_is_configured(monkeypatch):
    monkeypatch.setattr(main_module.settings, "telegram_bot_token", "configured")
    monkeypatch.setattr(main_module.settings, "telegram_webhook_secret", "secret")

    response = client.get("/health/telegram")
    assert response.status_code == 200
    assert response.json()["status"] == "webhook_configured"


def test_privacy_policy_is_public_and_contains_required_sections():
    response = client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "Google User Data" in response.text
    assert "Google API Services User Data Policy" in response.text
