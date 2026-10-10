from fastapi.testclient import TestClient

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


def test_readiness_does_not_claim_integrations_are_connected():
    response = client.get("/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["integrations"]["analytics"] == "not_enabled"
