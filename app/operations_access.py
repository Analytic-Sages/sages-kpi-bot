"""Staff identity and team-scoped authorization."""
from __future__ import annotations
from dataclasses import dataclass
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.admin_auth import require_authenticated
from app.operations_models import Person,RolePermission,Team

@dataclass(frozen=True)
class Actor:
    email:str
    person:Person|None
    is_admin:bool
    @property
    def person_id(self):return self.person.person_id if self.person else None

def resolve_actor(request:Request,db:Session):
    session=require_authenticated(request)
    if not session:return None
    email=str(session.get("email","")).casefold()
    admins={x.strip().casefold() for x in request.app.state.settings.admin_emails.split(",") if x.strip()}
    person=db.scalar(select(Person).where(Person.email==email))
    if email in admins:return Actor(email,person,True)
    if person is None or person.status!="Active":return None
    return Actor(email,person,False)

def is_manager(actor:Actor,db:Session,team_id:str|None=None):
    if actor.is_admin:return True
    if not actor.person:return False
    team_id=team_id or actor.person.team_id
    if team_id!=actor.person.team_id:return False
    if actor.person.role_id and db.scalar(select(RolePermission.permission).where(
        RolePermission.role_id==actor.person.role_id,
        RolePermission.permission=="team.manage",
    )):
        return True
    return db.scalar(select(Team.team_id).where(Team.team_id==team_id,Team.lead_person_id==actor.person_id)) is not None

def has_team_permission(actor:Actor,db:Session,team_id:str,permission:str):
    if actor.is_admin:return True
    if not actor.person or actor.person.team_id!=team_id:return False
    if is_manager(actor,db,team_id):return True
    return bool(actor.person.role_id and db.scalar(select(RolePermission.permission).where(
        RolePermission.role_id==actor.person.role_id,RolePermission.permission==permission)))

def can_access_team(actor:Actor,db:Session,team_id:str):
    return actor.is_admin or bool(actor.person and (actor.person.team_id==team_id or is_manager(actor,db,team_id)))

def require_csrf(request:Request,session:dict):
    import secrets
    token=request.headers.get("X-CSRF-Token","")
    return bool(token and secrets.compare_digest(token,str(session.get("csrf",""))))
