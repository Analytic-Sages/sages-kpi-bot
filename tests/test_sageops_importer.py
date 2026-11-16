from datetime import UTC, date, datetime

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.operations_db import Base
from app.operations_models import (
    Blocker, JobRun, KPI, KPIResult, Notification, NotificationAttempt, Person,
    ReviewAction, Setting, Task, TaskDetail, TaskReminderState, Team, WeeklyReview,
)
from scripts import import_sageops_workbook as importer


def make_workbook(path):
    workbook = Workbook()
    teams = workbook.active
    teams.title = "Teams"
    teams.append(["Team ID", "Team Name", "Team Lead ID", "Notes"])
    teams.append(["TM001", "Team Alpha", "P001", "Team note"])

    people = workbook.create_sheet("People Directory")
    people.append(["Person ID", "Full Name", "Team", "Role", "Email", "Telegram Handle", "Telegram Chat ID", "Reports To ID", "Status", "Notify?", "Notes"])
    people.append(["P001", "Ada Example", "Team Alpha", "Team Lead", "ada@example.com", "ada", "10001", None, "Active", "Yes", "Person note"])

    job = workbook.create_sheet("Job_Descriptions")
    job.append(["Role", "Core Domain", "Job Description", "Key Output Metrics"])
    job.append(["Team Lead", "Operations", "Lead a team", "Delivery; quality"])

    kpis = workbook.create_sheet("KPI Register")
    kpis.append(["KPI ID", "Team", "Role", "KPI", "Definition / Measurement", "Direction", "Frequency", "Owner Person ID", "Target", "Unit", "Data Source", "Active?", "Notes"])
    kpis.append(["KPI001", "Team Alpha", "Team Lead", "Delivery", "Definition", "Increase", "Weekly", "P001", 90, "%", "Manual", "Yes", "Owned KPI"])
    kpis.append(["KPI002", "Team Alpha", "Team Lead", "Quality", "Quality measure", "Increase", "Weekly", None, 95, "%", "Manual", "Yes", "Team KPI"])

    weekly = workbook.create_sheet("Weekly KPI Log")
    weekly.append(["Week Start", "KPI", "KPI ID", "Team", "Owner Person ID", "Direction", "Target Override", "Target", "Actual", "Achievement %", "Status", "Comment", "Next Action"])
    weekly.append([date(2026, 10, 5), "Delivery", "KPI001", "Team Alpha", "P001", "Increase", None, 90, 81, None, None, "Good week", "Keep pace"])

    tasks = workbook.create_sheet("Master Task Tracker")
    tasks.append(["Task ID", "Task Name", "Task Type", "Team", "Assigned To ID", "Assigned By ID", "Reviewer ID", "Priority", "Date Assigned", "Deadline", "Status", "% Complete", "Organization / Partner", "Output / Link"])
    tasks.append(["T0001", "Prepare report", "General", "Team Alpha", "P001", "P001", None, "High", date(2026, 10, 1), date(2026, 10, 10), "In Progress", 0.5, "Partner X", "https://example.test/report"])

    review = workbook.create_sheet("Weekly Review")
    review.append(["Week Start", "Team", "Key Achievement", "Challenge / Blocker", "Decision / Next Step", "Action Owner", "Action Due Date", "Action Status", "Action Owner ID"])
    review.append([date(2026, 10, 5), "Team Alpha", "Shipped", "Capacity", "Plan next week", None, date(2026, 10, 12), "Open", None])

    blockers = workbook.create_sheet("Blocker Log Worksheet")
    blockers.append(["Blocker ID ", "Task ID", "Person ID ", "Full Name ", "Description ", "Reported At ", "Status"])
    blockers.append(["B0001", "T0001", "P001", "Ada Example", "Waiting for review", "2026-10-06T10:00:00+00:00", "Open"])

    reminders = workbook.create_sheet("Reminder Log")
    reminders.append(["Log ID", "Sent At", "Person ID", "Telegram Chat ID", "Reminder Type", "Reference ID", "Message Summary", "Delivery Status", "Error"])
    reminders.append(["R0001", "2026-10-07T08:00:00+00:00", "P001", "10001", "task_reminder", "T0001", "Task due soon", "Sent", None])

    config = workbook.create_sheet("Config")
    config.append(["Setting Key", "Value", "What it does"])
    config.append(["Cfg_BotEnabled", "No", "Global switch"])
    config.append(["Cfg_Timezone", "Africa/Lagos", "Business timezone"])

    lists = workbook.create_sheet("Lists")
    lists.append(["Task Status", "Priority"])
    lists.append(["Not Started", "Low"])
    lists.append(["In Progress", "High"])

    for name in ("START HERE", "Dashboard", "Team Roster", "Person Digest", "Bot Guide", "Data Dictionary"):
        workbook.create_sheet(name)
    workbook.save(path)


def test_reconciliation_preserves_unassigned_team_kpis_and_checks_references(tmp_path, monkeypatch):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    data, sheets = importer.load(path)
    result = importer.report(data)

    assert "Dashboard" in sheets
    assert result["ready"] is True
    assert result["counts"] == {
        "teams": 1, "people": 1, "roles": 1, "kpis": 2,
        "weekly_kpi_results": 1, "tasks": 1, "weekly_review_rows": 1,
        "blockers": 1, "reminder_history": 1, "config_entries": 2,
        "list_configuration_columns": 2,
        "ignored_config_rows": 0,
    }
    assert sum(x["issue"] == "unassigned_team_kpi" for x in result["warnings"]) == 1
    assert result["issues"] == []


def test_import_persists_operational_rows_and_all_source_fields(tmp_path, monkeypatch):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    data, _ = importer.load(path)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(importer, "SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))

    imported = importer.apply(data)
    with importer.SessionLocal() as db:
        team = db.get(Team, "TM001")
        person = db.get(Person, "P001")
        task = db.get(Task, "T0001")
        detail = db.get(TaskDetail, "T0001")
        team_kpi = db.get(KPI, "KPI002")
        result = db.scalar(select(KPIResult).where(KPIResult.kpi_id == "KPI001"))
        review = db.scalar(select(WeeklyReview))
        action = db.scalar(select(ReviewAction))
        blocker = db.get(Blocker, "B0001")
        notice = db.scalar(select(Notification))
        attempt = db.scalar(select(NotificationAttempt))
        settings = {x.key: x.value for x in db.scalars(select(Setting)).all()}
        assert team.notes == "Team note"
        assert person.notes == "Person note"
        assert task.date_assigned == date(2026, 10, 1)
        assert detail.details["workbook_fields"]["Organization / Partner"] == "Partner X"
        assert team_kpi.owner_person_id is None
        assert result.actual == 81
        assert review.submitted_by is None
        assert action.owner_person_id is None
        assert blocker.reported_by == "P001"
        assert notice.status == "Sent"
        assert attempt.outcome == "sent"
        assert settings["bot_enabled"] is False
        assert settings["workbook_lists"]["Priority"] == ["Low", "High"]
    assert imported == {
        "teams": 1, "roles": 1, "people": 1, "kpis": 2,
        "weekly_kpi_results": 1, "tasks": 1, "weekly_reviews": 1,
        "blockers": 1, "reminder_history": 1,
    }
    engine.dispose()


def test_import_refuses_nonempty_target_without_changes(tmp_path, monkeypatch):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    data, _ = importer.load(path)
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(importer, "SessionLocal", sessions)
    with sessions() as db:
        db.add(Team(team_id="EXISTING", name="Existing"))
        db.commit()
    with pytest.raises(RuntimeError, match="empty operational target"):
        importer.apply(data)
    with sessions() as db:
        assert len(db.scalars(select(Team)).all()) == 1
    engine.dispose()


def test_reconciliation_blocks_invalid_reference(tmp_path):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    data, _ = importer.load(path)
    data["Master Task Tracker"][0]["Assigned To ID"] = "UNKNOWN"
    result = importer.report(data)
    assert result["ready"] is False
    assert any(item["issue"] == "unknown_person_reference" for item in result["issues"])


def test_import_preserves_existing_worker_runs_and_allows_empty_business_import(tmp_path, monkeypatch):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    data, _ = importer.load(path)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(importer, "SessionLocal", sessions)

    worker_started = datetime(2026, 11, 16, 18, 2, 5, tzinfo=UTC)
    with sessions() as db:
        db.add(JobRun(
            job_name="reminder_dispatch",
            started_at=worker_started,
            finished_at=datetime(2026, 11, 16, 18, 2, 6, tzinfo=UTC),
            status="success",
            detail="recovered=0;queued=0;sent=0",
        ))
        db.commit()

    imported = importer.apply(data)

    with sessions() as db:
        worker_runs = db.scalars(select(JobRun)).all()
        assert len(worker_runs) == 2
        preserved = next(run for run in worker_runs if run.job_name == "reminder_dispatch")
        assert preserved.started_at.replace(tzinfo=UTC) == worker_started
        assert preserved.status == "success"
        assert preserved.detail == "recovered=0;queued=0;sent=0"
        assert db.get(Team, "TM001") is not None

    assert imported["teams"] == 1
    engine.dispose()



def test_import_preserves_task_reminder_state_and_never_enables_bot(tmp_path, monkeypatch):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    workbook = __import__("openpyxl").load_workbook(path)
    tasks = workbook["Master Task Tracker"]
    headers = [cell.value for cell in tasks[1]]
    extra = {
        "Last Reminded At": "2026-10-09T08:30:00+01:00",
        "Reminder Count": 3,
        "Snooze Until": "2026-10-10T09:00:00+01:00",
        "Last Escalated At": "2026-10-09T09:00:00+01:00",
        "Assigner Notified At": "2026-10-09T09:15:00+01:00",
    }
    for key, value in extra.items():
        if key not in headers:
            headers.append(key)
            tasks.cell(1, len(headers)).value = key
        tasks.cell(2, headers.index(key) + 1).value = value
    config = workbook["Config"]
    config.cell(2, 2).value = "Yes"
    workbook.save(path)

    data, _ = importer.load(path)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(importer, "SessionLocal", sessions)

    importer.apply(data)
    with sessions() as db:
        state = db.get(TaskReminderState, "T0001")
        task = db.get(Task, "T0001")
        settings = {item.key: item.value for item in db.scalars(select(Setting)).all()}
        assert state.reminder_count == 3
        assert state.last_reminded_at.isoformat().startswith("2026-10-09T08:30:00")
        assert state.last_escalated_at.isoformat().startswith("2026-10-09T09:00:00")
        assert state.assigner_notified_at.isoformat().startswith("2026-10-09T09:15:00")
        assert task.snooze_until.isoformat().startswith("2026-10-10T09:00:00")
        assert settings["bot_enabled"] is False
        assert settings["workbook.Cfg_BotEnabled_requested"] is True
    engine.dispose()



def test_expected_settings_uses_importer_mapping_and_skips_blank_config_rows(tmp_path):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    workbook = __import__("openpyxl").load_workbook(path)
    config = workbook["Config"]
    config.append([None, None, None])
    config.append(["Cfg_DueSoonDays", 2, "Due soon window"])
    workbook["Lists"].append([None, None])
    workbook.save(path)

    data, _ = importer.load(path)
    expected = importer.expected_settings(data)

    assert len(data["Config"]) == 3
    assert expected["bot_enabled"] is False
    assert expected["workbook.Cfg_BotEnabled_requested"] is False
    assert expected["timezone"] == "Africa/Lagos"
    assert expected["due_soon_days"] == 2
    assert expected["workbook_lists"]["Task Status"] == ["Not Started", "In Progress"]


def test_config_import_ignores_instruction_rows_and_reports_worker_unused_settings(tmp_path):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    workbook = __import__("openpyxl").load_workbook(path)
    config = workbook["Config"]
    config.append(["Blue on yellow = you edit it. Grey = automatic.", None, None])
    config.append(["Cfg_ManagerChatID", None, "Manager chat"])
    workbook.save(path)

    data, _ = importer.load(path)
    result = importer.report(data)
    expected = importer.expected_settings(data)

    assert len(data["Config"]) == 3
    assert data["_ignored_config_rows"] == [
        {"row": 4, "reason": "setting_key_not_prefixed_cfg"}
    ]
    assert result["counts"]["config_entries"] == 3
    assert result["counts"]["ignored_config_rows"] == 1
    assert any(
        item["sheet"] == "Config" and item["row"] == 4
        and item["issue"] == "setting_key_not_prefixed_cfg"
        for item in result["warnings"]
    )
    assert "workbook.Blue on yellow = you edit it. Grey = automatic." not in expected
    assert "manager_chat_id" in result["runtime_setting_review"]["not_read_by_operations_worker"]
    assert "timezone" in result["runtime_setting_review"]["worker_consumed"]



def test_unimplemented_runtime_settings_are_preserved_without_becoming_live_controls(tmp_path, monkeypatch):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    workbook = __import__("openpyxl").load_workbook(path)
    config = workbook["Config"]
    config.append(["Cfg_DueCooldownHrs", 12, "Due reminder cooldown"])
    config.append(["Cfg_ManagerChatID", "12345", "Manager chat"])
    config.append(["Cfg_QuietHours", "20:00-07:00", "Quiet hours"])
    config.append(["Cfg_KPILogWeekday", 1, "KPI logging weekday"])
    workbook.save(path)

    data, _ = importer.load(path)
    expected = importer.expected_settings(data)
    result = importer.report(data)

    assert expected["workbook.Cfg_DueCooldownHrs"] == 12
    assert expected["workbook.Cfg_ManagerChatID"] == 12345
    assert expected["workbook.Cfg_QuietHours"] == "20:00-07:00"
    assert expected["workbook.Cfg_KPILogWeekday"] == 1
    assert "due_reminder_cooldown_hours" not in expected
    assert "manager_chat_id" not in expected
    assert "quiet_hours" not in expected
    assert "kpi_log_weekday" not in expected
    assert "workbook.Cfg_QuietHours" in result["runtime_setting_review"]["not_read_by_operations_worker"]

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(importer, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    importer.apply(data)

    with importer.SessionLocal() as db:
        settings = {item.key: item.value for item in db.scalars(select(Setting)).all()}
        assert settings["workbook.Cfg_QuietHours"] == "20:00-07:00"
        assert "quiet_hours" not in settings
        assert settings["bot_enabled"] is False
    engine.dispose()
