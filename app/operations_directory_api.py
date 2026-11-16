"""Administrator management for teams, staff, roles, and permission grants."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.operations_access import has_team_permission, require_csrf, resolve_actor
from app.operations_db import get_db
from app.operations_models import AuditLog, Person, Role, RolePermission, Team


class TeamCreate(BaseModel):
    team_id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=2, max_length=120)
    lead_person_id: str | None = None
    notes: str | None = None


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    lead_person_id: str | None = None
    notes: str | None = None


class RoleCreate(BaseModel):
    role_id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=2, max_length=120)
    core_domain: str | None = None
    responsibilities: str | None = None
    output_metrics: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list, max_length=20)


class RolePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    core_domain: str | None = None
    responsibilities: str | None = None
    output_metrics: dict[str, Any] | None = None
    permissions: list[str] | None = Field(default=None, max_length=20)


class PersonCreate(BaseModel):
    person_id: str | None = Field(default=None, max_length=32)
    full_name: str = Field(min_length=2, max_length=160)
    team_id: str | None = None
    role_id: str | None = None
    email: str | None = Field(default=None, max_length=254)
    telegram_handle: str | None = Field(default=None, max_length=64)
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    reports_to: str | None = None
    status: str = "Active"
    notify: bool = True
    notes: str | None = None


class PersonPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    team_id: str | None = None
    role_id: str | None = None
    email: str | None = Field(default=None, max_length=254)
    telegram_handle: str | None = Field(default=None, max_length=64)
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    reports_to: str | None = None
    status: str | None = None
    notify: bool | None = None
    notes: str | None = None


def create_operations_directory_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operations-directory"])

    def authorize(request: Request, db: Session, *, write: bool = False):
        actor = resolve_actor(request, db)
        if actor is None:
            return None, JSONResponse(status_code=401, content={"detail": "Authentication required"})
        if not actor.is_admin:
            return None, JSONResponse(status_code=403, content={"detail": "Administrator permission required"})
        if write:
            # Resolve the signed session through the shared auth helper and compare its CSRF token.
            from app.admin_auth import require_authenticated
            verified = require_authenticated(request)
            if not request.app.state.settings.operations_db_writes_enabled:
                return None, JSONResponse(status_code=503, content={"detail": "PostgreSQL writes are disabled"})
            if not verified or not require_csrf(request, verified):
                return None, JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
        return actor, None

    def admin_only(request: Request, db: Session, write: bool = False):
        return authorize(request, db, write=write)

    def staff_manager_only(request: Request, db: Session, team_id: str | None):
        actor = resolve_actor(request, db)
        if actor is None:
            return None, JSONResponse(status_code=401, content={"detail": "Authentication required"})
        if not actor.is_admin and (not team_id or not has_team_permission(actor, db, team_id, "staff.manage")):
            return None, JSONResponse(status_code=403, content={"detail": "Staff management permission required for this team"})
        from app.admin_auth import require_authenticated
        verified = require_authenticated(request)
        if not request.app.state.settings.operations_db_writes_enabled:
            return None, JSONResponse(status_code=503, content={"detail": "PostgreSQL writes are disabled"})
        if not verified or not require_csrf(request, verified):
            return None, JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
        return actor, None

    def role_grants_allowed(actor, db: Session, role_id: str | None):
        if actor.is_admin or not role_id:
            return True
        actor_grants = set(db.scalars(select(RolePermission.permission).where(
            RolePermission.role_id == actor.person.role_id)).all()) if actor.person and actor.person.role_id else set()
        requested = set(db.scalars(select(RolePermission.permission).where(
            RolePermission.role_id == role_id)).all())
        return requested.issubset(actor_grants)

    @router.post("/teams", status_code=201)
    def create_team(p: TeamCreate, request: Request, db: Session = Depends(get_db)):
        actor, denied = admin_only(request, db, True)
        if denied:
            return denied
        if p.lead_person_id:
            lead = db.get(Person, p.lead_person_id)
            if not lead or lead.status != "Active" or lead.team_id is not None:
                return JSONResponse(status_code=422, content={"detail": "Team lead must be active and not assigned to another team"})
        team_id = p.team_id or "TM-" + uuid.uuid4().hex[:12].upper()
        if db.get(Team, team_id):
            return JSONResponse(status_code=409, content={"detail": "Team ID already exists"})
        team = Team(team_id=team_id, name=p.name.strip(), lead_person_id=p.lead_person_id, notes=p.notes)
        if p.lead_person_id:
            lead.team_id = team_id
        db.add(team)
        if p.lead_person_id:
            db.add(AuditLog(actor=actor.person_id, entity="person", entity_id=lead.person_id,
                            field="team_id", old=None, new=team_id, outcome="APPLIED"))
        db.add(AuditLog(actor=actor.person_id, entity="team", entity_id=team_id, field="created", old=None,
                        new={"name": team.name, "lead_person_id": team.lead_person_id}, outcome="APPLIED"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content={"detail": "A team with this name already exists"})
        return {"item": {"id": team_id, "name": team.name}}

    @router.patch("/teams/{team_id}")
    def update_team(team_id: str, p: TeamPatch, request: Request, db: Session = Depends(get_db)):
        actor, denied = admin_only(request, db, True)
        if denied:
            return denied
        team = db.get(Team, team_id)
        if not team:
            return JSONResponse(status_code=404, content={"detail": "Team not found"})
        changes = p.model_dump(exclude_unset=True)
        if not changes:
            return JSONResponse(status_code=422, content={"detail": "At least one team field is required"})
        if changes.get("lead_person_id"):
            lead = db.get(Person, changes["lead_person_id"])
            if not lead or lead.status != "Active" or lead.team_id not in {None, team_id}:
                return JSONResponse(status_code=422, content={"detail": "Team lead must be active and not assigned to another team"})
            old_team_id = lead.team_id
            lead.team_id = team_id
            if old_team_id != team_id:
                db.add(AuditLog(actor=actor.person_id, entity="person", entity_id=lead.person_id,
                                field="team_id", old=old_team_id, new=team_id, outcome="APPLIED"))
        for field, value in changes.items():
            old = getattr(team, field)
            setattr(team, field, value)
            db.add(AuditLog(actor=actor.person_id, entity="team", entity_id=team_id, field=field,
                            old=old, new=value, outcome="APPLIED"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content={"detail": "A team with this name already exists"})
        return {"item": {"id": team_id, "updated": list(changes)}}

    @router.post("/roles", status_code=201)
    def create_role(p: RoleCreate, request: Request, db: Session = Depends(get_db)):
        actor, denied = admin_only(request, db, True)
        if denied:
            return denied
        invalid = set(p.permissions) - {"team.manage", "staff.manage", "kpi.manage", "task.manage", "review.manage"}
        if invalid:
            return JSONResponse(status_code=422, content={"detail": "Unknown role permission"})
        role_id = p.role_id or "ROLE-" + uuid.uuid4().hex[:12].upper()
        if db.get(Role, role_id):
            return JSONResponse(status_code=409, content={"detail": "Role ID already exists"})
        role = Role(role_id=role_id, name=p.name.strip(), core_domain=p.core_domain,
                    responsibilities=p.responsibilities, output_metrics=p.output_metrics)
        db.add(role)
        for permission in set(p.permissions):
            db.add(RolePermission(role_id=role_id, permission=permission))
        db.add(AuditLog(actor=actor.person_id, entity="role", entity_id=role_id, field="created", old=None,
                        new={"name": role.name, "permissions": sorted(set(p.permissions))}, outcome="APPLIED"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content={"detail": "A role with this name already exists"})
        return {"item": {"id": role_id, "name": role.name}}

    @router.patch("/roles/{role_id}")
    def update_role(role_id: str, p: RolePatch, request: Request, db: Session = Depends(get_db)):
        actor, denied = admin_only(request, db, True)
        if denied:
            return denied
        role = db.get(Role, role_id)
        if not role:
            return JSONResponse(status_code=404, content={"detail": "Role not found"})
        changes = p.model_dump(exclude_unset=True)
        permissions = changes.pop("permissions", None)
        allowed = {"team.manage", "staff.manage", "kpi.manage", "task.manage", "review.manage"}
        if permissions is not None and set(permissions) - allowed:
            return JSONResponse(status_code=422, content={"detail": "Unknown role permission"})
        if not changes and permissions is None:
            return JSONResponse(status_code=422, content={"detail": "At least one role field is required"})
        for field, value in changes.items():
            old = getattr(role, field)
            setattr(role, field, value)
            db.add(AuditLog(actor=actor.person_id, entity="role", entity_id=role_id, field=field,
                            old=old, new=value, outcome="APPLIED"))
        if permissions is not None:
            old_permissions = sorted(db.scalars(select(RolePermission.permission).where(RolePermission.role_id == role_id)).all())
            db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
            for permission in set(permissions):
                db.add(RolePermission(role_id=role_id, permission=permission))
            db.add(AuditLog(actor=actor.person_id, entity="role", entity_id=role_id, field="permissions",
                            old=old_permissions, new=sorted(set(permissions)), outcome="APPLIED"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content={"detail": "A role with this name already exists"})
        return {"item": {"id": role_id, "updated": list(changes) + (["permissions"] if permissions is not None else [])}}

    @router.post("/people", status_code=201)
    def create_person(p: PersonCreate, request: Request, db: Session = Depends(get_db)):
        actor, denied = staff_manager_only(request, db, p.team_id)
        if denied:
            return denied
        if p.status not in {"Active", "Inactive", "On Leave"}:
            return JSONResponse(status_code=422, content={"detail": "Invalid staff status"})
        if p.team_id and not db.get(Team, p.team_id):
            return JSONResponse(status_code=422, content={"detail": "Unknown team"})
        if p.role_id and not db.get(Role, p.role_id):
            return JSONResponse(status_code=422, content={"detail": "Unknown role"})
        if not role_grants_allowed(actor, db, p.role_id):
            return JSONResponse(status_code=403, content={"detail": "You cannot assign a role with permissions you do not hold"})
        if p.reports_to:
            manager = db.get(Person, p.reports_to)
            if not manager or manager.status != "Active" or manager.person_id == p.person_id or (p.team_id and manager.team_id != p.team_id):
                return JSONResponse(status_code=422, content={"detail": "Reporting manager must be active and belong to the same team"})
        person_id = p.person_id or "P-" + uuid.uuid4().hex[:12].upper()
        if db.get(Person, person_id):
            return JSONResponse(status_code=409, content={"detail": "Person ID already exists"})
        person = Person(person_id=person_id, full_name=p.full_name.strip(), team_id=p.team_id,
                        role_id=p.role_id, email=p.email.strip().casefold() if p.email else None,
                        telegram_handle=p.telegram_handle, telegram_chat_id=p.telegram_chat_id,
                        reports_to=p.reports_to, status=p.status, notify=p.notify, notes=p.notes)
        db.add(person)
        db.add(AuditLog(actor=actor.person_id, entity="person", entity_id=person_id, field="created", old=None,
                        new={"full_name": person.full_name, "team_id": person.team_id, "role_id": person.role_id}, outcome="APPLIED"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content={"detail": "Email or Telegram chat ID is already linked"})
        return {"item": {"id": person_id, "full_name": person.full_name}}

    @router.patch("/people/{person_id}")
    def update_person(person_id: str, p: PersonPatch, request: Request, db: Session = Depends(get_db)):
        person = db.get(Person, person_id)
        if not person:
            return JSONResponse(status_code=404, content={"detail": "Staff member not found"})
        target_team = p.team_id if "team_id" in p.model_fields_set else person.team_id
        actor, denied = staff_manager_only(request, db, person.team_id)
        if denied:
            return denied
        if not actor.is_admin and target_team != person.team_id:
            return JSONResponse(status_code=403, content={"detail": "Only administrators can transfer staff between teams"})
        changes = p.model_dump(exclude_unset=True)
        if not changes:
            return JSONResponse(status_code=422, content={"detail": "At least one staff field is required"})
        if changes.get("status") and changes["status"] not in {"Active", "Inactive", "On Leave"}:
            return JSONResponse(status_code=422, content={"detail": "Invalid staff status"})
        if changes.get("team_id") and not db.get(Team, changes["team_id"]):
            return JSONResponse(status_code=422, content={"detail": "Unknown team"})
        if changes.get("role_id") and not db.get(Role, changes["role_id"]):
            return JSONResponse(status_code=422, content={"detail": "Unknown role"})
        if "role_id" in changes and not role_grants_allowed(actor, db, changes["role_id"]):
            return JSONResponse(status_code=403, content={"detail": "You cannot assign a role with permissions you do not hold"})
        if changes.get("reports_to"):
            manager = db.get(Person, changes["reports_to"])
            target_team = changes.get("team_id", person.team_id)
            if not manager or manager.status != "Active" or manager.person_id == person_id or (target_team and manager.team_id != target_team):
                return JSONResponse(status_code=422, content={"detail": "Reporting manager must be active and belong to the same team"})
        if "email" in changes and changes["email"]:
            changes["email"] = changes["email"].strip().casefold()
        for field, value in changes.items():
            old = getattr(person, field)
            setattr(person, field, value)
            db.add(AuditLog(actor=actor.person_id, entity="person", entity_id=person_id, field=field,
                            old=old, new=value, outcome="APPLIED"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content={"detail": "Email or Telegram chat ID is already linked"})
        return {"item": {"id": person_id, "updated": list(changes)}}

    return router
