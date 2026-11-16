"""Compatibility adapter: preserve existing Control Centre JSON contracts on PostgreSQL."""
from __future__ import annotations

from datetime import UTC, date, datetime
from sqlalchemy import func, select

from app.operations_db import SessionLocal
from app.operations_models import KPI, KPIResult, Person, Task, Team


def _status(value: str) -> str:
    normalized = "".join(char for char in value.casefold() if char.isalnum())
    if normalized in {"done", "complete", "completed", "closed", "resolved", "finished"}:
        return "completed"
    if any(token in normalized for token in ("block", "imped", "stuck")):
        return "blocked"
    if any(token in normalized for token in ("progress", "working", "started", "review")):
        return "in_progress"
    if normalized in {"", "open", "todo", "notstarted", "pending", "backlog", "assigned"}:
        return "open"
    if any(token in normalized for token in ("cancel", "wontdo", "notapplicable")):
        return "cancelled"
    return "other"


def _context(db):
    people = db.scalars(select(Person)).all()
    teams = {x.team_id: x.name for x in db.scalars(select(Team)).all()}
    return {
        x.person_id: {
            "id": x.person_id,
            "name": x.full_name,
            "team": teams.get(x.team_id, "Unassigned team"),
            "status": x.status,
        }
        for x in people
    }, teams


def list_tasks(*, limit: int, offset: int, status: str | None, team: str | None,
               person: str | None, query: str | None, overdue_only: bool) -> dict:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured")
    today = datetime.now(UTC).date()
    with SessionLocal() as db:
        people, teams = _context(db)
        rows = db.scalars(select(Task).order_by(Task.deadline.asc().nulls_last(), Task.task_id)).all()
        items = []
        for task in rows:
            owner = people.get(task.assigned_to, {})
            state = _status(task.status)
            overdue = bool(task.deadline and task.deadline < today and state not in {"completed", "cancelled"})
            if status and state != status:
                continue
            if team and owner.get("team", "").casefold() != team.casefold():
                continue
            if person and person.casefold() not in {str(task.assigned_to or "").casefold(), owner.get("name", "").casefold()}:
                continue
            needle = (query or "").casefold().strip()
            if needle and needle not in " ".join((task.task_id, task.name, owner.get("name", ""))).casefold():
                continue
            if overdue_only and not overdue:
                continue
            items.append({
                "id": task.task_id, "name": task.name, "owner_id": task.assigned_to,
                "owner_name": owner.get("name", "Unassigned"), "team": owner.get("team", teams.get(task.team_id, "Unassigned team")),
                "status": state, "status_label": task.status, "due_date": task.deadline.isoformat() if task.deadline else None,
                "updated_date": task.updated_at.date().isoformat() if task.updated_at else None,
                "overdue": overdue, "blocker": task.blocker_note,
            })
        items.sort(key=lambda x: (not x["overdue"], x["due_date"] or "9999-12-31", x["id"]))
        return {
            "source": {"system": "PostgreSQL", "status": "read_success", "table": "tasks"},
            "items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset,
            "data_quality": {"duplicate_person_ids": 0, "duplicate_task_ids": 0,
                             "tasks_missing_id": 0, "tasks_missing_owner": sum(x.assigned_to is None for x in rows),
                             "tasks_unknown_or_ambiguous_owner": sum(x.assigned_to is not None and x.assigned_to not in people for x in rows)},
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }


def get_task(task_id: str) -> dict | None:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured")
    with SessionLocal() as db:
        matches = db.scalars(select(Task).where(Task.task_id == task_id)).all()
        if len(matches) != 1:
            return None
        task = matches[0]
        people, teams = _context(db)
        owner = people.get(task.assigned_to, {})
        return {
            "source": {"system": "PostgreSQL", "status": "read_success", "table": "tasks"},
            "item": {
                "id": task.task_id, "name": task.name, "owner_id": task.assigned_to,
                "owner_name": owner.get("name", "Unassigned"), "team": owner.get("team", teams.get(task.team_id, "Unassigned team")),
                "status_label": task.status, "due_date": task.deadline.isoformat() if task.deadline else None,
                "updated_date": task.updated_at.date().isoformat() if task.updated_at else None,
                "blocker": task.blocker_note,
            },
        }


def list_kpis(*, limit: int, offset: int, team: str | None, person: str | None, active_only: bool) -> dict:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured")
    with SessionLocal() as db:
        people, teams = _context(db)
        query = select(KPI).order_by(KPI.name, KPI.kpi_id)
        if active_only:
            query = query.where(KPI.active.is_(True))
        rows = db.scalars(query).all()
        latest = {}
        for result in db.scalars(select(KPIResult).order_by(KPIResult.period_start.desc(), KPIResult.id.desc())).all():
            latest.setdefault(result.kpi_id, result)
        items = []
        for kpi in rows:
            owner = people.get(kpi.owner_person_id, {})
            team_name = teams.get(kpi.team_id, "Unassigned team")
            if team and team_name.casefold() != team.casefold():
                continue
            if person and person.casefold() not in {kpi.owner_person_id.casefold(), owner.get("name", "").casefold()}:
                continue
            observation = latest.get(kpi.kpi_id)
            items.append({
                "id": kpi.kpi_id, "name": kpi.name, "owner_id": kpi.owner_person_id,
                "owner_name": owner.get("name", "Unassigned"), "team": team_name,
                "target": str(kpi.target), "actual": str(observation.actual) if observation and observation.actual is not None else None,
                "week_start": observation.period_start.isoformat() if observation else None,
                "active": kpi.active, "direction": kpi.direction,
            })
        return {
            "source": {"system": "PostgreSQL", "status": "read_success", "table": "kpis"},
            "items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset,
            "data_quality": {"duplicate_person_ids": 0, "duplicate_kpi_ids": 0, "kpis_missing_id": 0},
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
