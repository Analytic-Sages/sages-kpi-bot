from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE, _serializer
from app.admin_records_api import create_admin_records_router
from app.config import Settings


def client_for(authenticated=True):
    settings = Settings(
        _env_file=None,
        app_env="test",
        admin_google_client_id="client-id",
        admin_google_client_secret="client-secret",
        admin_google_redirect_uri="https://sageops.example/auth/callback",
        admin_emails="admin@example.com",
        admin_session_secret="s" * 48,
    )
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


def test_task_list_passes_filters_to_postgres_adapter(monkeypatch):
    from app import admin_records_api

    def fake_list(**kwargs):
        assert kwargs == {
            "limit": 10,
            "offset": 5,
            "status": "blocked",
            "team": "Research",
            "person": None,
            "query": "report",
            "overdue_only": True,
        }
        return {"source": {"system": "PostgreSQL"}, "items": [], "total": 0}

    monkeypatch.setattr(admin_records_api.operations_admin_adapter, "list_tasks", fake_list)
    response = client_for().get(
        "/api/v1/admin/tasks?limit=10&offset=5&status=blocked&team=Research&query=report&overdue_only=true"
    )
    assert response.status_code == 200
    assert response.json()["source"]["system"] == "PostgreSQL"
    assert response.headers["cache-control"] == "no-store"


def test_task_detail_uses_postgres_adapter(monkeypatch):
    from app import admin_records_api

    monkeypatch.setattr(admin_records_api.operations_admin_adapter, "get_task", lambda task_id: None)
    response = client_for().get("/api/v1/admin/tasks/missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


def test_kpi_list_includes_unassigned_team_kpis(monkeypatch):
    from app import admin_records_api

    def fake_list(**kwargs):
        assert kwargs == {"limit": 25, "offset": 0, "team": "Learning", "person": None, "active_only": True}
        return {"source": {"system": "PostgreSQL"}, "items": [{"owner_id": None}], "total": 1}

    monkeypatch.setattr(admin_records_api.operations_admin_adapter, "list_kpis", fake_list)
    response = client_for().get("/api/v1/admin/kpis?team=Learning")
    assert response.status_code == 200
    assert response.json()["items"] == [{"owner_id": None}]


def test_kpi_detail_uses_postgres_adapter(monkeypatch):
    from app import admin_records_api

    monkeypatch.setattr(admin_records_api.operations_admin_adapter, "get_kpi", lambda kpi_id: {"item": {"id": kpi_id}})
    response = client_for().get("/api/v1/admin/kpis/KPI001")
    assert response.status_code == 200
    assert response.json()["item"]["id"] == "KPI001"
