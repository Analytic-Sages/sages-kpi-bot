"""Real-browser smoke coverage for authentication, rendered records and team scoping."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from itsdangerous import URLSafeTimedSerializer
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SESSION_SECRET = "browser-test-session-secret-that-is-long-enough"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _environment(db_file: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{db_file}",
            "OPERATIONS_DB_WRITES_ENABLED": "true",
            "ADMIN_GOOGLE_CLIENT_ID": "browser-test-client",
            "ADMIN_GOOGLE_CLIENT_SECRET": "browser-test-client-secret",
            "ADMIN_GOOGLE_REDIRECT_URI": f"http://127.0.0.1:{port}/auth/callback",
            "ADMIN_SESSION_SECRET": SESSION_SECRET,
            "ADMIN_EMAILS": "admin@example.test",
            "ADMIN_COOKIE_SECURE": "false",
            "OPERATIONS_NOTIFICATIONS_ENABLED": "false",
            "TELEGRAM_TEST_MODE": "true",
            "DRY_RUN": "true",
            "PORT": str(port),
        }
    )
    return env


def _seed_database(db_file: Path, env: dict[str, str]) -> None:
    seed = """
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.operations_db import database_engine
from app.operations_models import Base, Person, Role, RolePermission, Team, Task, KPI
engine=database_engine()
Base.metadata.create_all(engine)
with engine.begin() as connection:
    connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
    connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0004_blocker_ownership')"))
with Session(engine) as db:
    db.add_all([Team(team_id='TEAM-A',name='Alpha Team'),Team(team_id='TEAM-B',name='Beta Team')])
    db.add_all([Role(role_id='MANAGER',name='Manager'),Role(role_id='STAFF',name='Staff')])
    db.flush()
    db.add(RolePermission(role_id='MANAGER',permission='team.manage'))
    db.add_all([
      Person(person_id='ADMIN',full_name='Admin User',email='admin@example.test',team_id='TEAM-A',role_id='MANAGER'),
      Person(person_id='STAFF-A',full_name='Staff Alpha',email='staff-a@example.test',team_id='TEAM-A',role_id='STAFF'),
      Person(person_id='STAFF-B',full_name='Staff Beta',email='staff-b@example.test',team_id='TEAM-B',role_id='STAFF'),
    ])
    db.flush()
    db.add_all([
      Task(task_id='TASK-A',name='Alpha delivery',task_type='General',team_id='TEAM-A',assigned_to='STAFF-A',assigned_by='ADMIN',priority='High',date_assigned=__import__('datetime').date.today(),status='In Progress'),
      Task(task_id='TASK-B',name='Beta confidential work',task_type='General',team_id='TEAM-B',assigned_to='STAFF-B',assigned_by='ADMIN',priority='Medium',date_assigned=__import__('datetime').date.today()),
      KPI(kpi_id='KPI-A',team_id='TEAM-A',name='Alpha output',direction='Increase',frequency='Weekly',target=10,owner_person_id='STAFF-A'),
    ])
    db.commit()
"""
    subprocess.run([sys.executable, "-c", seed], cwd=ROOT, env=env, check=True, capture_output=True, text=True)


def _session_cookie(email: str, *, staff: bool) -> str:
    return URLSafeTimedSerializer(SESSION_SECRET, salt="polaris-admin-session-v1").dumps(
        {"email": email, "staff_identity": staff, "csrf": "browser-test-csrf", "issued_at": int(time.time())}
    )


def test_real_browser_admin_staff_dashboard_and_mobile_scope(tmp_path):
    database = tmp_path / "sageops-browser.sqlite"
    port = _free_port()
    env = _environment(database, port)
    _seed_database(database, env)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            anonymous = browser.new_page(viewport={"width": 1440, "height": 1000})
            for _ in range(100):
                try:
                    response = anonymous.goto(base_url + "/control/", wait_until="domcontentloaded", timeout=1000)
                    if response and response.status == 200:
                        break
                except Exception:
                    time.sleep(0.1)
            assert anonymous.get_by_role("link", name="Continue with Google").is_visible()
            login_response = anonymous.request.get(base_url + "/auth/login", max_redirects=0)
            assert login_response.status == 302
            assert login_response.headers["location"].startswith("https://accounts.google.com/")

            admin = browser.new_page(viewport={"width": 1440, "height": 1000})
            admin.context.add_cookies(
                [{"name": "polaris_admin_session", "value": _session_cookie("admin@example.test", staff=False), "url": base_url}]
            )
            admin.goto(base_url + "/control/")
            admin.locator("#dashboard").wait_for(state="visible", timeout=10000)
            admin.get_by_role("heading", name="Task register").scroll_into_view_if_needed()
            admin.locator("#tasks-body").get_by_text("Alpha delivery").wait_for(timeout=10000)
            admin.locator("#kpi-records-body").get_by_text("Alpha output").wait_for(timeout=10000)
            assert admin.locator("#tasks-body").get_by_text("Alpha delivery").is_visible()
            assert admin.locator("#kpi-records-body").get_by_text("Alpha output").is_visible()
            admin.locator("#team-form [name='name']").fill("Gamma Team")
            admin.locator("#team-form").get_by_role("button", name="Save team").click()
            admin.locator("#workspace-message").get_by_text("Saved successfully.").wait_for(timeout=10000)
            admin.locator("#teams-management-body").get_by_text("Gamma Team").wait_for(timeout=10000)
            admin.locator("#task-create-form [name='name']").fill("Browser-created task")
            admin.locator("#task-create-form [name='team_id']").select_option(label="Alpha Team")
            admin.locator("#task-create-form [name='assigned_to']").select_option(label="Staff Alpha")
            admin.locator("#task-create-form").get_by_role("button", name="Create task").click()
            admin.locator("#workspace-message").get_by_text("Saved successfully.").wait_for(timeout=10000)
            admin.locator("#ops-tasks-body").get_by_text("Browser-created task").wait_for(timeout=10000)
            admin.locator("#kpi-manage-form [name='name']").fill("Browser-created KPI")
            admin.locator("#kpi-manage-form [name='team_id']").select_option(label="Alpha Team")
            admin.locator("#kpi-manage-form [name='target']").fill("12")
            admin.locator("#kpi-manage-form").get_by_role("button", name="Save KPI").click()
            admin.locator("#workspace-message").get_by_text("Saved successfully.").wait_for(timeout=10000)
            admin.locator("#kpi-records-body").get_by_text("Browser-created KPI").wait_for(timeout=10000)
            admin.get_by_role("button", name="Sign out").click()
            admin.locator("#signed-out").wait_for(state="visible", timeout=5000)

            staff = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            staff.context.add_cookies(
                [{"name": "polaris_admin_session", "value": _session_cookie("staff-a@example.test", staff=True), "url": base_url}]
            )
            staff.goto(base_url + "/control/")
            staff.locator("#dashboard").wait_for(state="visible", timeout=10000)
            staff.locator("#ops-tasks-body").get_by_text("Alpha delivery").wait_for(timeout=10000)
            assert "Beta confidential work" not in staff.locator("#ops-tasks-body").inner_text()
            assert staff.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            result = staff.evaluate(
                """async () => {
                  const r = await fetch('/api/v1/operations/tasks?team_id=TEAM-B');
                  return {status:r.status, body:await r.json()};
                }"""
            )
            assert result["status"] == 200
            assert all(item.get("team_id") != "TEAM-B" for item in result["body"].get("items", []))
            result = staff.evaluate(
                """async () => {
                  const r = await fetch('/api/v1/operations/teams', {
                    method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':'browser-test-csrf'},
                    body:JSON.stringify({team_id:'TEAM-X',name:'Unauthorized team'})
                  });
                  return r.status;
                }"""
            )
            assert result == 403
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
