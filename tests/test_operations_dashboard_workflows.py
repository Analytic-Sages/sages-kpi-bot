"""API integration coverage for the dashboard's PostgreSQL workflow contracts."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.admin_auth import SESSION_COOKIE, _serializer
from app.config import Settings
from app.operations_api import create_operations_router
from app.operations_db import get_db
from app.operations_directory_api import create_operations_directory_router
from app.operations_management_api import create_operations_management_router
from app.operations_models import Base, Person, Role, RolePermission, Team
from app.operations_write_api import create_operations_write_router


@pytest.fixture
def ops_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(_env_file=None, admin_session_secret="s" * 48,
                        admin_google_client_id="client", admin_google_client_secret="secret",
                        admin_google_redirect_uri="https://example.test/callback",
                        admin_emails="admin@example.com", admin_cookie_secure=False,
                        operations_db_writes_enabled=True)
    app = FastAPI()
    app.state.settings = settings
    app.include_router(create_operations_router())
    app.include_router(create_operations_write_router())
    app.include_router(create_operations_management_router())
    app.include_router(create_operations_directory_router())

    def get_test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_test_db
    with factory.begin() as db:
        db.add_all([
            Team(team_id="TEAM-A", name="Alpha"),
            Role(role_id="ROLE-M", name="Manager"),
            Role(role_id="ROLE-S", name="Staff"),
        ])
        db.flush()
        db.add(RolePermission(role_id="ROLE-M", permission="team.manage"))
        db.add_all([
            Person(person_id="P-M", full_name="Manager One", email="manager@example.com", team_id="TEAM-A", role_id="ROLE-M", status="Active"),
            Person(person_id="P-S", full_name="Staff One", email="staff@example.com", team_id="TEAM-A", role_id="ROLE-S", status="Active"),
        ])
    def client(email="manager@example.com", staff=True):
        test_client = TestClient(app)
        cookie = _serializer(settings, "polaris-admin-session-v1").dumps({
            "email": email, "staff_identity": staff, "csrf": "csrf-workflow", "issued_at": 1,
        })
        test_client.cookies.set(SESSION_COOKIE, cookie)
        test_client.headers.update({"X-CSRF-Token": "csrf-workflow"})
        return test_client
    yield app, factory, client
    engine.dispose()


def test_team_staff_and_role_workflows_are_admin_only(ops_env):
    _, factory, client = ops_env
    admin = client("admin@example.com", False)
    staff = client()
    denied = staff.post("/api/v1/operations/people", json={"full_name": "No Access"})
    assert denied.status_code == 403
    role = admin.post("/api/v1/operations/roles", json={
        "role_id": "ROLE-NEW", "name": "Reviewer", "permissions": ["review.manage"],
    })
    assert role.status_code == 201
    changed = admin.patch("/api/v1/operations/roles/ROLE-NEW", json={"permissions": ["kpi.manage"]})
    assert changed.status_code == 200
    assert admin.post("/api/v1/operations/roles",json={"role_id":"ROLE-HR","name":"Staff Coordinator","permissions":["staff.manage"]}).status_code==201
    hr_person=admin.post("/api/v1/operations/people",json={"person_id":"P-HR","full_name":"Staff Coordinator","email":"hr@example.com","team_id":"TEAM-A","role_id":"ROLE-HR"})
    assert hr_person.status_code==201
    hr=client("hr@example.com",True)
    assert hr.get("/api/v1/operations/capabilities").json()["can_manage_staff"] is True
    assert hr.post("/api/v1/operations/people",json={"full_name":"New Staff","team_id":"TEAM-A","role_id":"ROLE-S"}).status_code==201
    escalated=hr.post("/api/v1/operations/people",json={"full_name":"Privileged Staff","team_id":"TEAM-A","role_id":"ROLE-NEW"})
    assert escalated.status_code==403
    person = admin.post("/api/v1/operations/people", json={
        "person_id": "P-NEW", "full_name": "New Person", "email": "new@example.com", "team_id": "TEAM-A", "role_id": "ROLE-NEW",
    })
    assert person.status_code == 201
    role_user = client("new@example.com", True)
    assert role_user.get("/api/v1/operations/capabilities").json()["can_manage_kpis"] is True
    assert admin.patch("/api/v1/operations/people/P-NEW", json={"status": "Inactive"}).status_code == 200
    created_team=admin.post("/api/v1/operations/teams",json={"team_id":"TEAM-B","name":"Beta"})
    assert created_team.status_code==201
    lead=admin.post("/api/v1/operations/people",json={"person_id":"P-LEAD","full_name":"Beta Lead"})
    assert lead.status_code==201
    assigned=admin.patch("/api/v1/operations/teams/TEAM-B",json={"lead_person_id":"P-LEAD"})
    assert assigned.status_code==200
    team = admin.patch("/api/v1/operations/teams/TEAM-A", json={"name": "Alpha Team"})
    assert team.status_code == 200
    with factory() as db:
        assert db.get(Person, "P-NEW").status == "Inactive"
        assert db.get(Team, "TEAM-A").name == "Alpha Team"
        assert db.get(Person,"P-LEAD").team_id=="TEAM-B"


def test_ownerless_kpi_submission_and_result_edit_use_authorized_routes(ops_env):
    _, factory, client = ops_env
    manager = client()
    created = manager.post("/api/v1/operations/kpis", json={
        "kpi_id": "KPI-TEAM", "team_id": "TEAM-A", "name": "Team delivery", "direction": "Increase",
        "frequency": "Weekly", "target": 10, "unit": "items",
    })
    assert created.status_code == 201
    submitted = manager.post("/api/v1/operations/kpis/KPI-TEAM/results", json={
        "period_start": "2026-11-09", "actual": 8,
    })
    assert submitted.status_code == 201
    result_id = int(submitted.json()["item"]["id"])
    edited = manager.patch(f"/api/v1/operations/kpi-results/{result_id}", json={"actual": 12, "comment": "Updated"})
    assert edited.status_code == 200
    assert manager.patch("/api/v1/operations/kpis/KPI-TEAM", json={"target": 11}).status_code == 200
    history = manager.get("/api/v1/operations/kpi-results?kpi_id=KPI-TEAM").json()["items"]
    assert history[0]["achievement"] == 120.0
    assert history[0]["below_target"] is False
    with factory() as db:
        assert db.get(Team, "TEAM-A")


def test_task_review_blocker_and_approval_workflows(ops_env):
    _, factory, client = ops_env
    manager = client()
    staff = client("staff@example.com", True)
    task = manager.post("/api/v1/operations/tasks", json={
        "task_id": "TSK-WORK", "name": "Prepare report", "team_id": "TEAM-A", "assigned_to": "P-S",
        "priority": "High", "deadline": "2026-11-30",
    })
    assert task.status_code == 201
    updated = staff.patch("/api/v1/operations/tasks/TSK-WORK", json={
        "expected_version": 1, "status": "In Progress", "percent_complete": 40,
    })
    assert updated.status_code == 200
    reassigned = manager.patch("/api/v1/operations/tasks/TSK-WORK", json={
        "expected_version": 2, "assigned_to": "P-M", "priority": "Urgent",
    })
    assert reassigned.status_code == 200
    review = manager.post("/api/v1/operations/weekly-reviews", json={
        "team_id": "TEAM-A", "week_start": "2026-11-16", "key_achievement": "Shipped",
        "actions": [{"owner_person_id": "P-S", "description": "Follow up", "due_date": "2026-11-20"}],
    })
    assert review.status_code == 201
    listed = manager.get("/api/v1/operations/weekly-reviews?team_id=TEAM-A").json()["items"][0]
    action_id = listed["actions"][0]["id"]
    assert manager.patch(f"/api/v1/operations/review-actions/{action_id}", json={"status": "Done"}).status_code == 200
    blocker = manager.post("/api/v1/operations/blockers", json={"task_id": "TSK-WORK", "owner_person_id": "P-M", "description": "Waiting on source data"})
    assert blocker.status_code == 201
    assert manager.patch(f"/api/v1/operations/blockers/{blocker.json()['item']['id']}", json={"status": "Resolved"}).status_code == 200
    request = staff.post("/api/v1/operations/changes", json={
        "entity": "task", "entity_id": "TSK-WORK", "changes": {"priority": "Urgent"}, "reason": "Deadline risk",
    })
    assert request.status_code == 201
    request_id = request.json()["item"]["request_id"]
    assert staff.post(f"/api/v1/operations/changes/{request_id}/confirm", json={}).status_code == 200
    assert manager.post(f"/api/v1/operations/changes/{request_id}/approve", json={}).status_code == 200
    approvals = manager.get("/api/v1/operations/changes?state=APPROVED").json()["items"]
    assert approvals[0]["state"] == "APPROVED"
    completed=manager.patch("/api/v1/operations/tasks/TSK-WORK",json={"expected_version":6,"status":"Completed","percent_complete":100})
    assert completed.status_code==200
    second=staff.post("/api/v1/operations/changes",json={"entity":"task","entity_id":"TSK-WORK","changes":{"priority":"Low"},"reason":"Lower priority requested"})
    assert second.status_code==201
    second_id=second.json()["item"]["request_id"]
    assert staff.post(f"/api/v1/operations/changes/{second_id}/confirm",json={}).status_code==200
    assert manager.post(f"/api/v1/operations/changes/{second_id}/reject",json={}).status_code==200
    assert manager.get("/api/v1/operations/changes?state=REJECTED").json()["items"][0]["id"]==second_id
    assert manager.get("/api/v1/operations/notifications").status_code == 200
    audit=manager.get("/api/v1/operations/audit?limit=200").json()["items"]
    assert {"task","weekly_review","change_request"}.issubset({event["entity"] for event in audit})
    with factory() as db:
        assert db.get(Team, "TEAM-A")


def test_mutations_require_csrf_and_team_scope(ops_env):
    app, factory, client = ops_env
    manager = client()
    manager.headers.pop("X-CSRF-Token")
    response = manager.post("/api/v1/operations/tasks", json={
        "name": "No CSRF", "team_id": "TEAM-A", "priority": "Low",
    })
    assert response.status_code == 403
    staff = client("staff@example.com", True)
    forbidden = staff.post("/api/v1/operations/tasks", json={
        "name": "No permission", "team_id": "TEAM-A", "priority": "Low",
    })
    assert forbidden.status_code == 403
    caps=staff.get("/api/v1/operations/capabilities").json()
    assert caps["is_team_manager"] is False
