"""Team-scoped read APIs backed by PostgreSQL."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.operations_access import Actor, resolve_actor
from app.operations_db import get_db
from app.operations_models import KPI, KPIResult, Person, Role, Task, Team


def create_operations_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

    def authorize(request: Request, db: Session):
        actor = resolve_actor(request, db)
        if actor is None:
            return None, JSONResponse(status_code=401, content={"detail": "Authentication required"})
        if not actor.is_admin and actor.person is None:
            return None, JSONResponse(status_code=403, content={"detail": "Active staff identity is required"})
        return actor, None

    def scoped_team(actor: Actor, requested: str | None):
        if actor.is_admin:
            return requested, None
        team_id = actor.person.team_id if actor.person else None
        if not team_id:
            return None, JSONResponse(status_code=403, content={"detail": "No team is assigned to this account"})
        if requested and requested != team_id:
            return None, JSONResponse(status_code=403, content={"detail": "Team access denied"})
        return team_id, None

    @router.get("/health")
    def health(request: Request, db: Session = Depends(get_db)):
        _, denied = authorize(request, db)
        if denied:
            return denied
        db.execute(select(1))
        return {"status": "connected", "database": "postgresql", "checked_at": datetime.now(UTC).isoformat()}

    @router.get("/teams")
    def teams(request: Request, db: Session = Depends(get_db)):
        actor, denied = authorize(request, db)
        if denied:
            return denied
        query = select(Team).order_by(Team.name)
        if not actor.is_admin:
            query = query.where(Team.team_id == actor.person.team_id)
        rows = db.scalars(query).all()
        return {"items": [{"id": x.team_id, "name": x.name, "lead_person_id": x.lead_person_id, "notes": x.notes} for x in rows], "total": len(rows)}

    @router.get("/roles")
    def roles(request: Request, db: Session = Depends(get_db)):
        _, denied = authorize(request, db)
        if denied:
            return denied
        rows = db.scalars(select(Role).order_by(Role.name)).all()
        return {"items": [{"id": x.role_id, "name": x.name, "core_domain": x.core_domain, "responsibilities": x.responsibilities, "output_metrics": x.output_metrics} for x in rows], "total": len(rows)}

    @router.get("/people")
    def people(request: Request, db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), team_id: str | None = None):
        actor, denied = authorize(request, db)
        if denied:
            return denied
        team_id, denied = scoped_team(actor, team_id)
        if denied:
            return denied
        query = select(Person)
        if team_id:
            query = query.where(Person.team_id == team_id)
        if not actor.is_admin:
            query = query.where(Person.status != "Inactive")
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(Person.full_name).limit(limit).offset(offset)).all()
        return {"items": [{
            "id": x.person_id, "full_name": x.full_name, "team_id": x.team_id,
            "role_id": x.role_id, "email": x.email if actor.is_admin else None,
            "telegram_handle": x.telegram_handle if actor.is_admin else None,
            "telegram_chat_id_linked": bool(x.telegram_chat_id), "reports_to": x.reports_to,
            "status": x.status, "start_date": x.start_date.isoformat() if x.start_date else None,
            "end_date": x.end_date.isoformat() if x.end_date else None, "notes": x.notes if actor.is_admin else None,
        } for x in rows], "total": total, "limit": limit, "offset": offset}

    @router.get("/tasks")
    def tasks(request: Request, db: Session = Depends(get_db), assignee: str | None = None, team: str | None = None, status: str | None = None, overdue_only: bool = False, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        actor, denied = authorize(request, db)
        if denied:
            return denied
        team, denied = scoped_team(actor, team)
        if denied:
            return denied
        query = select(Task)
        if assignee:
            query = query.where(Task.assigned_to == assignee)
        if team:
            query = query.where(Task.team_id == team)
        if status:
            query = query.where(func.lower(Task.status) == status.casefold())
        if overdue_only:
            query = query.where(Task.deadline < datetime.now(UTC).date(), Task.status.notin_({"Completed", "Cancelled", "Closed"}))
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(Task.deadline.asc().nulls_last(), Task.task_id).limit(limit).offset(offset)).all()
        return {"items": [{"id": x.task_id, "name": x.name, "task_type": x.task_type, "team_id": x.team_id,
                           "assigned_to": x.assigned_to, "assigned_by": x.assigned_by, "reviewer": x.reviewer,
                           "priority": x.priority, "date_assigned": x.date_assigned.isoformat(),
                           "deadline": x.deadline.isoformat() if x.deadline else None, "status": x.status,
                           "percent_complete": x.percent_complete, "completed_at": x.completed_at.isoformat() if x.completed_at else None,
                           "output_link": x.output_link, "blocker_note": x.blocker_note, "version": x.version} for x in rows],
                "total": total, "limit": limit, "offset": offset}

    @router.get("/kpis")
    def kpis(request: Request, db: Session = Depends(get_db), owner: str | None = None, team: str | None = None, active_only: bool = True, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        actor, denied = authorize(request, db)
        if denied:
            return denied
        team, denied = scoped_team(actor, team)
        if denied:
            return denied
        query = select(KPI)
        if owner:
            query = query.where(KPI.owner_person_id == owner)
        if team:
            query = query.where(KPI.team_id == team)
        if active_only:
            query = query.where(KPI.active.is_(True))
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(KPI.name).limit(limit).offset(offset)).all()
        return {"items": [{"id": x.kpi_id, "name": x.name, "team_id": x.team_id, "role_id": x.role_id,
                           "owner_person_id": x.owner_person_id, "target": str(x.target), "direction": x.direction,
                           "frequency": x.frequency, "unit": x.unit, "data_source": x.data_source, "active": x.active,
                           "definition": x.definition, "notes": x.notes} for x in rows],
                "total": total, "limit": limit, "offset": offset}

    @router.get("/kpi-results")
    def kpi_results(request: Request, db: Session = Depends(get_db), kpi_id: str | None = None, owner: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        actor, denied = authorize(request, db)
        if denied:
            return denied
        team_id, denied = scoped_team(actor, None)
        if denied:
            return denied
        query = select(KPIResult).join(KPI, KPI.kpi_id == KPIResult.kpi_id)
        if kpi_id:
            query = query.where(KPIResult.kpi_id == kpi_id)
        if owner:
            query = query.where(KPI.owner_person_id == owner)
        if team_id:
            query = query.where(KPI.team_id == team_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(KPIResult.period_start.desc(), KPIResult.id.desc()).limit(limit).offset(offset)).all()
        return {"items": [{"id": x.id, "kpi_id": x.kpi_id, "period_start": x.period_start.isoformat(),
                           "actual": str(x.actual) if x.actual is not None else None, "target_used": str(x.target_used),
                           "target_override": str(x.target_override) if x.target_override is not None else None,
                           "comment": x.comment, "next_action": x.next_action, "logged_by": x.logged_by,
                           "logged_at": x.logged_at.isoformat() if x.logged_at else None} for x in rows],
                "total": total, "limit": limit, "offset": offset}

    return router
