from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_api import create_admin_api_router
from app.admin_auth import SESSION_COOKIE, _serializer
from app.config import Settings


def settings():
    return Settings(
        _env_file=None,
        app_name="SageOps",
        app_env="test",
        admin_google_client_id="client-id",
        admin_google_client_secret="client-secret",
        admin_google_redirect_uri="https://sageops.example/auth/callback",
        admin_emails="admin@example.com",
        admin_session_secret="s" * 48,
    )


def client_for(authenticated=True):
    config = settings()
    app = FastAPI()
    app.state.settings = config
    app.include_router(create_admin_api_router())
    client = TestClient(app, base_url="https://testserver")
    if authenticated:
        cookie = _serializer(config, "polaris-admin-session-v1").dumps(
            {"email": "admin@example.com", "csrf": "csrf-token", "issued_at": 1}
        )
        client.cookies.set(SESSION_COOKIE, cookie)
    return client


def test_team_performance_requires_auth():
    response = client_for(authenticated=False).get("/api/v1/admin/team-performance")
    assert response.status_code == 401


def test_team_performance_reads_postgres_adapter(monkeypatch):
    from app import admin_api

    def fake_performance(**kwargs):
        assert kwargs["start_date"] == date(2026, 10, 1)
        assert kwargs["end_date"] == date(2026, 10, 31)
        return {"source": {"system": "PostgreSQL", "status": "read_success"}, "summary": {"tasks": 1}}

    monkeypatch.setattr(admin_api, "team_performance", fake_performance)
    response = client_for().get(
        "/api/v1/admin/team-performance?start_date=2026-10-01&end_date=2026-10-31"
    )
    assert response.status_code == 200
    assert response.json()["source"]["system"] == "PostgreSQL"
    assert response.json()["summary"]["tasks"] == 1
    assert response.headers["cache-control"] == "no-store"


def test_team_performance_rejects_inverted_date_range():
    response = client_for().get(
        "/api/v1/admin/team-performance?start_date=2026-10-31&end_date=2026-10-01"
    )
    assert response.status_code == 422
