import asyncio
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_api import create_admin_api_router
from app.admin_auth import SESSION_COOKIE, _serializer
from app.config import Settings


class FakeSheets:
    async def read_records(self, worksheet_name):
        data = {
            "People Directory": [
                {"Person ID": "p1", "Full Name": "Ada", "Team": "Research", "Status": "Active"}
            ],
            "Master Task Tracker": [
                {"Task ID": "t1", "Task Name": "Write report", "Assigned To ID": "p1", "Status": "In Progress", "Due Date": "2026-10-01"}
            ],
            "KPI Register": [
                {"KPI ID": "k1", "KPI": "Reports", "Owner Person ID": "p1", "Target": "10"}
            ],
            "Weekly KPI Log": [
                {"Week Start": "2026-10-12", "KPI ID": "k1", "Owner Person ID": "p1", "Target": "10", "Actual": "8"}
            ],
        }
        return data[worksheet_name]


def settings():
    return Settings(
        _env_file=None, app_name="Polaris", app_env="test", dry_run=True,
        google_sheet_id="sheet-id", admin_google_client_id="client-id",
        admin_google_client_secret="client-secret",
        admin_google_redirect_uri="https://polaris.example/auth/callback",
        admin_emails="admin@example.com", admin_session_secret="s" * 48,
        audit_db_path="data/audit.sqlite3",
    )


def test_team_performance_requires_auth():
    app = FastAPI()
    app.state.settings = settings()
    app.include_router(create_admin_api_router())
    response = TestClient(app, base_url="https://testserver").get("/api/v1/admin/team-performance")
    assert response.status_code == 401


def test_team_performance_returns_read_only_aggregates(monkeypatch):
    from app import admin_api

    monkeypatch.setattr(
        admin_api.GoogleSheetsClient, "from_settings", staticmethod(lambda settings: FakeSheets())
    )
    app = FastAPI()
    app.state.settings = settings()
    app.include_router(create_admin_api_router())
    client = TestClient(app, base_url="https://testserver")
    cookie = _serializer(settings(), "polaris-admin-session-v1").dumps(
        {"email": "admin@example.com", "csrf": "csrf-token", "issued_at": 1}
    )
    client.cookies.set(SESSION_COOKIE, cookie)
    response = client.get("/api/v1/admin/team-performance?start_date=2026-10-01&end_date=2026-10-31")
    assert response.status_code == 200
    body = response.json()
    assert body["source"]["status"] == "read_success"
    assert body["summary"]["tasks"] == 1
    assert body["summary"]["overdue"] == 1
    assert body["kpis"][0]["actual"] == 8.0
    assert response.headers["cache-control"] == "no-store"


def test_team_performance_rejects_inverted_date_range():
    app = FastAPI()
    app.state.settings = settings()
    app.include_router(create_admin_api_router())
    client = TestClient(app, base_url="https://testserver")
    cookie = _serializer(settings(), "polaris-admin-session-v1").dumps(
        {"email": "admin@example.com", "csrf": "csrf-token", "issued_at": 1}
    )
    client.cookies.set(SESSION_COOKIE, cookie)
    response = client.get("/api/v1/admin/team-performance?start_date=2026-10-31&end_date=2026-10-01")
    assert response.status_code == 422
