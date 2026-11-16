from datetime import date, datetime, timezone

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.operations_db import Base
from app.operations_models import (
    Blocker, KPI, KPIResult, Notification, NotificationAttempt, Person, ReviewAction,
    JobRun, Setting, Task, TaskDetail, Team, WeeklyReview,
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


def test_reconciliation_preserves_unassigned_team_kpis_and_checks_references(tmp_path):
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
        db.add(Team(team_id="EXISTING", name="Existing")); db.commit()
    with pytest.raises(RuntimeError, match="empty operational target"):
        importer.apply(data)
    with sessions() as db:
        assert len(db.scalars(select(Team)).all()) == 1
    engine.dispose()


def test_reconciliation_blocks_invalid_reference(tmp_path):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    workbook = Workbook()
    workbook.close()
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

    worker_started = datetime(2026, 11, 16, 18, 2, 5, tzinfo=timezone.utc)
    with sessions() as db:
        db.add(JobRun(
            job_name="reminder_dispatch",
            started_at=worker_started,
            finished_at=datetime(2026, 11, 16, 18, 2, 6, tzinfo=timezone.utc),
            status="success",
            detail="recovered=0;queued=0;sent=0",
        ))
        db.commit()

    imported = importer.apply(data)

    with sessions() as db:
        worker_runs = db.scalars(select(JobRun)).all()
        assert len(worker_runs) == 2
        preserved = next(run for run in worker_runs if run.job_name == "reminder_dispatch")
        assert preserved.started_at.replace(tzinfo=timezone.utc) == worker_started
        assert preserved.status == "success"
        assert preserved.detail == "recovered=0;queued=0;sent=0"
        assert db.get(Team, "TM001") is not None

    assert imported["teams"] == 1
    engine.dispose()

