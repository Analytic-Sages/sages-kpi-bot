import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_api import create_admin_api_router
from app.admin_auth import SESSION_COOKIE, _serializer, create_admin_auth_router
from app.audit.store import SQLiteAuditLog
from app.config import Settings


def configured_settings(**overrides):
    values = {
        "app_name": "Polaris",
        "app_env": "test",
        "dry_run": True,
        "google_sheet_id": "sheet-id",
        "telegram_bot_token": None,
        "enable_team_workflows": False,
        "enable_team_writes": False,
        "enable_scheduled_workflows": False,
        "enable_analytics": False,
        "enable_webhooks": False,
        "enable_safe_telegram_replies": False,
        "enable_dry_run_team_replies": False,
        "audit_db_path": "data/polaris-audit.sqlite3",
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


def test_admin_overview_reports_safe_status_without_secrets():
    response = client_for(configured_settings()).get("/api/v1/admin/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["admin"] == {"email": "admin@example.com", "access": "read_only"}
    assert body["service"]["dry_run"] is True
    assert body["integrations"]["google_sheets"]["status"] == "configured_not_checked"
    assert body["safety"]["team_writes_enabled"] is False
    assert body["safety"]["live_sheet_mutations_available"] is False
    assert body["capabilities"]["task_and_kpi_browsing"] is False
    assert body["capabilities"]["task_and_kpi_editing"] is False
    assert "client-secret" not in response.text
    assert "sheet-id" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_audit_history_is_empty_without_creating_database(tmp_path):
    database = tmp_path / "missing" / "audit.sqlite3"
    response = client_for(configured_settings(audit_db_path=str(database))).get(
        "/api/v1/admin/audit-events"
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}
    assert not database.exists()


def test_audit_history_is_paginated_and_filterable(tmp_path):
    database = tmp_path / "audit.sqlite3"
    store = SQLiteAuditLog(str(database))
    for index, outcome in enumerate(("preview", "verified", "conflict")):
        asyncio.run(
            store.record(
                {
                    "request_id": f"request-{index}",
                    "actor_id": "admin@example.com",
                    "action": "sheet_mutation",
                    "worksheet": "Approved Pilot",
                    "record_id": f"record-{index}",
                    "changed_fields": ["Status"],
                    "before": {"Status": "Open"},
                    "after": {"Status": "Closed"},
                    "outcome": outcome,
                }
            )
        )

    client = client_for(configured_settings(audit_db_path=str(database)))
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
