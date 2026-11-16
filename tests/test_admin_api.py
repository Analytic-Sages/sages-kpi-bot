from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin_api
from app.admin_api import create_admin_api_router
from app.admin_auth import SESSION_COOKIE, _serializer, create_admin_auth_router
from app.config import Settings


def configured_settings(**overrides):
    values = {
        "app_name": "SageOps",
        "app_env": "test",
        "dry_run": True,
        "telegram_bot_token": None,
        "enable_team_workflows": False,
        "enable_team_writes": False,
        "enable_scheduled_workflows": False,
        "enable_analytics": False,
        "enable_webhooks": False,
        "enable_safe_telegram_replies": False,
        "enable_dry_run_team_replies": False,
        "admin_google_client_id": "client-id",
        "admin_google_client_secret": "client-secret",
        "admin_google_redirect_uri": "https://polaris.example/auth/callback",
        "admin_emails": "admin@example.com",
        "admin_session_secret": "s" * 48,
        "admin_cookie_secure": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def client_for(settings):
    app = FastAPI()
    app.state.settings = settings
    app.include_router(create_admin_auth_router(settings))
    app.include_router(create_admin_api_router())
    client = TestClient(app, base_url="https://testserver")
    cookie = _serializer(settings, "polaris-admin-session-v1").dumps(
        {"email": "admin@example.com", "csrf": "csrf-token", "issued_at": 1}
    )
    client.cookies.set(SESSION_COOKIE, cookie)
    return client


def test_admin_overview_requires_authentication():
    settings = configured_settings()
    app = FastAPI()
    app.state.settings = settings
    app.include_router(create_admin_api_router())
    response = TestClient(app, base_url="https://testserver").get(
        "/api/v1/admin/overview"
    )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_admin_overview_reports_safe_status_without_secrets(monkeypatch):
    monkeypatch.setattr(admin_api, "database_engine", lambda: None)
    response = client_for(configured_settings()).get("/api/v1/admin/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["admin"] == {"email": "admin@example.com", "access": "administrator"}
    assert body["service"]["dry_run"] is True
    assert "google_sheets" not in body["integrations"]
    assert body["integrations"]["operations_database"]["status"] == "unavailable"
    assert body["safety"]["team_writes_enabled"] is False
    assert body["safety"]["live_sheet_mutations_available"] is False
    assert body["capabilities"]["task_and_kpi_browsing"] is True
    assert body["capabilities"]["task_and_kpi_editing"] is False
    assert "client-secret" not in response.text
    assert "sheet-id" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_audit_history_uses_postgresql_adapter(monkeypatch):
    monkeypatch.setattr(admin_api, "list_audit_events", lambda **kwargs: {"items": [], "total": 0})
    response = client_for(configured_settings()).get("/api/v1/admin/audit-events")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_audit_history_is_paginated_and_filterable(monkeypatch):
    def fake_events(**kwargs):
        assert kwargs == {"limit": 1, "offset": 0, "outcome": "verified", "worksheet": "Approved Pilot"}
        return {"items": [{"outcome": "verified", "changed_fields": ["Status"],
                           "before": {"Status": "Open"}, "after": {"Status": "Closed"}}], "total": 1}

    monkeypatch.setattr(admin_api, "list_audit_events", fake_events)
    client = client_for(configured_settings())
    response = client.get(
        "/api/v1/admin/audit-events?limit=1&offset=0&outcome=verified"
        "&worksheet=Approved%20Pilot"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["outcome"] == "verified"
    assert body["items"][0]["changed_fields"] == ["Status"]
    assert body["items"][0]["before"] == {"Status": "Open"}
    assert body["items"][0]["after"] == {"Status": "Closed"}


def test_audit_history_rejects_out_of_range_page_size():
    response = client_for(configured_settings()).get(
        "/api/v1/admin/audit-events?limit=1000"
    )
    assert response.status_code == 422
