from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE, _serializer
from app.admin_records_api import create_admin_records_router
from app.config import Settings


class FakeSheets:
    headers = {
        "People Directory": ["Person ID", "Full Name", "Team", "Status"],
        "Master Task Tracker": [
            "Task ID", "Task Name", "Assigned To ID", "Status", "Due Date", "Last Updated"
        ],
        "KPI Register": ["KPI ID", "KPI", "Owner Person ID", "Target", "Active?", "Direction"],
        "Weekly KPI Log": ["Week Start", "KPI ID", "Owner Person ID", "Target", "Actual"],
    }
    records = {
        "People Directory": [
            {"Person ID": "p1", "Full Name": "Ada", "Team": "Research", "Status": "Active"},
            {"Person ID": "p2", "Full Name": "Ben", "Team": "Engineering", "Status": "Active"},
        ],
        "Master Task Tracker": [
            {
                "Task ID": "t1", "Task Name": "Write report", "Assigned To ID": "p1",
                "Status": "In Progress", "Due Date": "2026-10-01",
                "Last Updated": "2026-10-02",
            },
            {
                "Task ID": "t2", "Task Name": "Ship API", "Assigned To ID": "p2",
                "Status": "Completed", "Due Date": "2026-10-20",
                "Last Updated": "2026-10-16",
            },
            {
                "Task ID": "t3", "Task Name": "No owner", "Assigned To ID": "",
                "Status": "Blocked", "Due Date": "",
            },
        ],
        "KPI Register": [
            {
                "KPI ID": "k1", "KPI": "Reports", "Owner Person ID": "p1",
                "Target": "10", "Active?": "Yes", "Direction": "higher",
            },
            {
                "KPI ID": "k2", "KPI": "Pipeline", "Owner Person ID": "p2",
                "Target": "5", "Active?": "No", "Direction": "",
            },
        ],
        "Weekly KPI Log": [
            {
                "Week Start": "2026-10-12", "KPI ID": "k1", "Owner Person ID": "p1",
                "Target": "10", "Actual": "8",
            },
            {
                "Week Start": "2026-10-05", "KPI ID": "k1", "Owner Person ID": "p1",
                "Target": "10", "Actual": "7",
            },
        ],
    }

    async def read_headers(self, worksheet):
        return self.headers[worksheet]

    async def read_records(self, worksheet):
        return self.records[worksheet]


def configured_settings(**overrides):
    values = {
        "app_env": "test",
        "dry_run": True,
        "google_sheet_id": "test-sheet",
        "admin_google_client_id": "client-id",
        "admin_google_client_secret": "client-secret",
        "admin_google_redirect_uri": "https://polaris.example/auth/callback",
        "admin_emails": "admin@example.com",
        "admin_session_secret": "s" * 48,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def client_for(settings=None, authenticated=True):
    settings = settings or configured_settings()
    app = FastAPI()
    app.state.settings = settings
    app.include_router(create_admin_records_router())
    client = TestClient(app, base_url="https://testserver")
    if authenticated:
        cookie = _serializer(settings, "polaris-admin-session-v1").dumps(
            {"email": "admin@example.com", "csrf": "csrf-token", "issued_at": 1}
        )
        client.cookies.set(SESSION_COOKIE, cookie)
    return client


def test_task_list_requires_authentication():
    response = client_for(authenticated=False).get("/api/v1/admin/tasks")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_task_list_filters_and_sorts_overdue_first(monkeypatch):
    from app import admin_records_api

    monkeypatch.setattr(
        admin_records_api.GoogleSheetsClient,
        "from_settings",
        staticmethod(lambda settings: FakeSheets()),
    )
    response = client_for().get("/api/v1/admin/tasks?team=Research&status=in_progress")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "t1"
    assert body["items"][0]["overdue"] is True
    assert response.headers["cache-control"] == "no-store"


def test_task_detail_rejects_missing_or_ambiguous_record(monkeypatch):
    from app import admin_records_api

    monkeypatch.setattr(
        admin_records_api.GoogleSheetsClient,
        "from_settings",
        staticmethod(lambda settings: FakeSheets()),
    )
    response = client_for().get("/api/v1/admin/tasks/missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found or task ID is ambiguous"


def test_kpi_list_returns_latest_observation_and_hides_inactive_by_default(monkeypatch):
    from app import admin_records_api

    monkeypatch.setattr(
        admin_records_api.GoogleSheetsClient,
        "from_settings",
        staticmethod(lambda settings: FakeSheets()),
    )
    response = client_for().get("/api/v1/admin/kpis")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "k1"
    assert body["items"][0]["actual"] == "8"
    assert body["items"][0]["week_start"] == "2026-10-12"


def test_missing_sheet_configuration_returns_sanitized_503():
    response = client_for(configured_settings(google_sheet_id=None)).get("/api/v1/admin/tasks")
    assert response.status_code == 503
    assert "credentials" not in response.text



def test_duplicate_task_ids_are_hidden_and_reported(monkeypatch):
    from app import admin_records_api

    class DuplicateSheets(FakeSheets):
        async def read_records(self, worksheet):
            rows = list(self.records[worksheet])
            if worksheet == "Master Task Tracker":
                rows.append(dict(rows[0]))
            return rows

    monkeypatch.setattr(
        admin_records_api.GoogleSheetsClient,
        "from_settings",
        staticmethod(lambda settings: DuplicateSheets()),
    )
    response = client_for().get("/api/v1/admin/tasks")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["data_quality"]["duplicate_task_ids"] == 1
    assert all(item["id"] != "t1" for item in body["items"])
