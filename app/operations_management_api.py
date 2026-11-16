"""Remaining KPI and weekly-review management endpoints."""
from __future__ import annotations
from datetime import UTC, datetime
from decimal import Decimal
from fastapi import APIRouter,Depends,Query,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.admin_auth import require_authenticated
from app.operations_access import can_access_team,is_manager,require_csrf,resolve_actor
from app.operations_db import get_db
from app.operations_models import AuditLog,Blocker,KPI,Notification,NotificationAttempt,Person,ReviewAction,Role,Task,WeeklyReview

class KPICreate(BaseModel):
    kpi_id:str|None=None
    team_id:str
    role_id:str|None=None
    name:str=Field(min_length=2,max_length=200)
    definition:str|None=None
    direction:str
    frequency:str="Weekly"
    owner_person_id:str
    target:Decimal
    unit:str|None=None
    data_source:str="Manual"
class KPIPatch(BaseModel):
    name:str|None=Field(default=None,max_length=200)
    definition:str|None=None
    direction:str|None=None
    frequency:str|None=None
    owner_person_id:str|None=None
    target:Decimal|None=None
    unit:str|None=None
    active:bool|None=None
class ActionPatch(BaseModel):
    status:str
class BlockerCreate(BaseModel):
    task_id:str|None=None
    description:str=Field(min_length=3,max_length=5000)
class BlockerPatch(BaseModel):
    status:str

def create_operations_management_router():
    router=APIRouter(prefix="/api/v1/operations",tags=["operations-management"])
    def authorize(request,db,write=False):
        session=require_authenticated(request)
        if not session:return None,None,JSONResponse(status_code=401,content={"detail":"Authentication required"})
        if write and not request.app.state.settings.operations_db_writes_enabled:
            return None,None,JSONResponse(status_code=503,content={"detail":"PostgreSQL writes are disabled until cutover approval"})
        if write and not require_csrf(request,session):
            return None,None,JSONResponse(status_code=403,content={"detail":"CSRF validation failed"})
        actor=resolve_actor(request,db)
        if not actor:return None,None,JSONResponse(status_code=403,content={"detail":"No active staff identity is linked"})
        return actor,None,None
    @router.post("/kpis",status_code=201)
    def create_kpi(p:KPICreate,request:Request,db:Session=Depends(get_db)):
        a,_,err=authorize(request,db,True)
        if err:return err
        if not can_access_team(a,db,p.team_id) or not is_manager(a,db,p.team_id):
            return JSONResponse(status_code=403,content={"detail":"Team manager permission required"})
        if p.owner_person_id:
            owner=db.get(Person,p.owner_person_id)
            if not owner or owner.status!="Active" or owner.team_id!=p.team_id:return JSONResponse(status_code=422,content={"detail":"KPI owner must be active and belong to the KPI team"})
        if p.direction not in {"Increase","Decrease","Maintain"}:return JSONResponse(status_code=422,content={"detail":"Invalid KPI direction"})
        if p.frequency not in {"Daily","Weekly","Monthly","Quarterly"}:return JSONResponse(status_code=422,content={"detail":"Invalid frequency"})
        if p.data_source not in {"Manual","Auto"}:return JSONResponse(status_code=422,content={"detail":"Invalid data source"})
        if not p.target.is_finite() or p.target<=0:return JSONResponse(status_code=422,content={"detail":"Target must be positive and finite"})
        if p.role_id and not db.get(Role,p.role_id):return JSONResponse(status_code=422,content={"detail":"Unknown KPI role"})
        kid=p.kpi_id or "KPI-"+__import__("uuid").uuid4().hex[:10].upper()
        if db.get(KPI,kid):return JSONResponse(status_code=409,content={"detail":"KPI ID already exists"})
        k=KPI(kpi_id=kid,team_id=p.team_id,role_id=p.role_id,name=p.name.strip(),definition=p.definition,direction=p.direction,frequency=p.frequency,owner_person_id=p.owner_person_id,target=p.target,unit=p.unit,data_source=p.data_source,active=True)
        db.add(k);db.add(AuditLog(actor=a.person_id,entity="kpi",entity_id=kid,field="created",old=None,new={"name":k.name,"team_id":k.team_id},outcome="APPLIED"));db.commit()
        return {"item":{"id":kid,"name":k.name}}
    @router.patch("/kpis/{kpi_id}")
    def patch_kpi(kpi_id:str,p:KPIPatch,request:Request,db:Session=Depends(get_db)):
        a,_,err=authorize(request,db,True)
        if err:return err
        k=db.get(KPI,kpi_id)
        if not k:return JSONResponse(status_code=404,content={"detail":"KPI not found"})
        if not can_access_team(a,db,k.team_id) or not is_manager(a,db,k.team_id):return JSONResponse(status_code=403,content={"detail":"Team manager permission required"})
        changes=p.model_dump(exclude_unset=True)
        if "owner_person_id" in changes and changes["owner_person_id"]:
            owner=db.get(Person,changes["owner_person_id"])
            if not owner or owner.status!="Active" or owner.team_id!=k.team_id:return JSONResponse(status_code=422,content={"detail":"Owner must be active and belong to the KPI team"})
        if "direction" in changes and changes["direction"] not in {"Increase","Decrease","Maintain"}:return JSONResponse(status_code=422,content={"detail":"Invalid direction"})
        if "frequency" in changes and changes["frequency"] not in {"Daily","Weekly","Monthly","Quarterly"}:return JSONResponse(status_code=422,content={"detail":"Invalid frequency"})
        if "target" in changes and (not changes["target"].is_finite() or changes["target"]<=0):return JSONResponse(status_code=422,content={"detail":"Target must be positive and finite"})
        if "role_id" in changes and changes["role_id"] and not db.get(Role,changes["role_id"]):return JSONResponse(status_code=422,content={"detail":"Unknown KPI role"})
        for field,value in changes.items():
            old=getattr(k,field);setattr(k,field,value)
            db.add(AuditLog(actor=a.person_id,entity="kpi",entity_id=kpi_id,field=field,old=str(old) if isinstance(old,Decimal) else old,new=str(value) if isinstance(value,Decimal) else value,outcome="APPLIED"))
        db.commit()
        return {"item":{"id":kpi_id,"updated":list(changes)}}
    @router.get("/weekly-reviews")
    def list_reviews(request:Request,db:Session=Depends(get_db),team_id:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
        a,_,err=authorize(request,db)
        if err:return err
        q=select(WeeklyReview).order_by(WeeklyReview.week_start.desc(),WeeklyReview.id.desc())
        if team_id:
            if not can_access_team(a,db,team_id):return JSONResponse(status_code=403,content={"detail":"Team access denied"})
            q=q.where(WeeklyReview.team_id==team_id)
        elif not a.is_admin and a.person:
            q=q.where(WeeklyReview.team_id==a.person.team_id)
        total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows=db.scalars(q.limit(limit).offset(offset)).all()
        items=[]
        for r in rows:
            actions=db.scalars(select(ReviewAction).where(ReviewAction.review_id==r.id)).all()
            items.append({"id":r.id,"team_id":r.team_id,"week_start":r.week_start.isoformat(),"key_achievement":r.key_achievement,"challenge":r.challenge,"decision":r.decision,"submitted_by":r.submitted_by,"actions":[{"id":x.id,"owner_person_id":x.owner_person_id,"description":x.description,"due_date":x.due_date.isoformat() if x.due_date else None,"status":x.status} for x in actions]})
        return {"items":items,"total":total,"limit":limit,"offset":offset}
    @router.patch("/review-actions/{action_id}")
    def patch_action(action_id:int,p:ActionPatch,request:Request,db:Session=Depends(get_db)):
        a,_,err=authorize(request,db,True)
        if err:return err
        action=db.get(ReviewAction,action_id)
        if not action:return JSONResponse(status_code=404,content={"detail":"Review action not found"})
        review=db.get(WeeklyReview,action.review_id)
        if not review:return JSONResponse(status_code=404,content={"detail":"Review not found"})
        if p.status not in {"Open","Done","Dropped"}:return JSONResponse(status_code=422,content={"detail":"Invalid action status"})
        if not(a.is_admin or a.person_id==action.owner_person_id or is_manager(a,db,review.team_id)):return JSONResponse(status_code=403,content={"detail":"Action owner or team manager required"})
        old=action.status;action.status=p.status
        db.add(AuditLog(actor=a.person_id,entity="review_action",entity_id=str(action_id),field="status",old=old,new=p.status,outcome="APPLIED"));db.commit()
        return {"item":{"id":action_id,"status":action.status}}

    @router.get("/blockers")
    def list_blockers(request:Request,db:Session=Depends(get_db),status:str|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
        a,_,err=authorize(request,db)
        if err:return err
        q=select(Blocker).order_by(Blocker.created_at.desc(),Blocker.blocker_id)
        if status:q=q.where(Blocker.status==status)
        if not a.is_admin:
            if not a.person:return JSONResponse(status_code=403,content={"detail":"Active staff identity is required"})
            q=q.outerjoin(Task,Task.task_id==Blocker.task_id).where((Blocker.reported_by==a.person_id)|(Task.team_id==a.person.team_id))
        total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows=db.scalars(q.limit(limit).offset(offset)).all()
        return {"items":[{"id":x.blocker_id,"task_id":x.task_id,"reported_by":x.reported_by,"description":x.description,"status":x.status,"created_at":x.created_at.isoformat() if x.created_at else None,"resolved_at":x.resolved_at.isoformat() if x.resolved_at else None} for x in rows],"total":total,"limit":limit,"offset":offset}

    @router.post("/blockers",status_code=201)
    def create_blocker(p:BlockerCreate,request:Request,db:Session=Depends(get_db)):
        a,_,err=authorize(request,db,True)
        if err:return err
        if not a.person_id:return JSONResponse(status_code=422,content={"detail":"Staff identity is required"})
        task=db.get(Task,p.task_id) if p.task_id else None
        if p.task_id and not task:return JSONResponse(status_code=404,content={"detail":"Task not found"})
        if task and (not can_access_team(a,db,task.team_id) or task.assigned_to!=a.person_id and not is_manager(a,db,task.team_id)):
            return JSONResponse(status_code=403,content={"detail":"Only the assignee or team manager can report a task blocker"})
        item=Blocker(task_id=task.task_id if task else None,reported_by=a.person_id,description=p.description.strip(),status="Open")
        db.add(item);db.flush()
        db.add(AuditLog(actor=a.person_id,entity="blocker",entity_id=item.blocker_id,field="created",old=None,new={"task_id":item.task_id,"status":"Open"},outcome="APPLIED"))
        if task:task.blocker_note=p.description.strip();task.version+=1
        db.commit()
        return {"item":{"id":item.blocker_id,"task_id":item.task_id,"status":item.status}}

    @router.patch("/blockers/{blocker_id}")
    def update_blocker(blocker_id:str,p:BlockerPatch,request:Request,db:Session=Depends(get_db)):
        a,_,err=authorize(request,db,True)
        if err:return err
        if p.status not in {"Open","In Progress","Resolved"}:return JSONResponse(status_code=422,content={"detail":"Invalid blocker status"})
        item=db.get(Blocker,blocker_id)
        if not item:return JSONResponse(status_code=404,content={"detail":"Blocker not found"})
        task=db.get(Task,item.task_id) if item.task_id else None
        can_manage=a.is_admin or bool(task and is_manager(a,db,task.team_id))
        if not can_manage and item.reported_by!=a.person_id:return JSONResponse(status_code=403,content={"detail":"Blocker reporter or team manager permission required"})
        if p.status=="Resolved" and not can_manage:return JSONResponse(status_code=403,content={"detail":"Team manager permission required to resolve a blocker"})
        old=item.status;item.status=p.status
        if p.status=="Resolved":item.resolved_at=datetime.now(UTC)
        if task and p.status=="Resolved":task.blocker_note=None;task.version+=1
        db.add(AuditLog(actor=a.person_id,entity="blocker",entity_id=item.blocker_id,field="status",old=old,new=p.status,outcome="APPLIED"));db.commit()
        return {"item":{"id":item.blocker_id,"status":item.status}}

    @router.get("/notifications")
    def list_notifications(request:Request,db:Session=Depends(get_db),limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
        a,_,err=authorize(request,db)
        if err:return err
        q=select(Notification).order_by(Notification.created_at.desc(),Notification.id.desc())
        if not a.is_admin:q=q.where(Notification.person_id==a.person_id)
        total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows=db.scalars(q.limit(limit).offset(offset)).all()
        ids=[x.id for x in rows]
        attempts={}
        if ids:
            for attempt in db.scalars(select(NotificationAttempt).where(NotificationAttempt.notification_id.in_(ids)).order_by(NotificationAttempt.attempted_at.desc())).all():
                attempts.setdefault(attempt.notification_id,[]).append({"at":attempt.attempted_at.isoformat(),"outcome":attempt.outcome,"error":attempt.error})
        return {"items":[{"id":x.id,"person_id":x.person_id,"type":x.type,"reference_id":x.reference_id,"message":x.message,"status":x.status,"attempts":x.attempts,"error":x.error,"created_at":x.created_at.isoformat() if x.created_at else None,"sent_at":x.sent_at.isoformat() if x.sent_at else None,"delivery_attempts":attempts.get(x.id,[])} for x in rows],"total":total,"limit":limit,"offset":offset}
    return router
