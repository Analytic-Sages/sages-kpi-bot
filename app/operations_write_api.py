"""Role-aware task, KPI, review and approval APIs."""
from __future__ import annotations
import uuid
from datetime import UTC,date,datetime,timedelta
from decimal import Decimal
from typing import Any
from fastapi import APIRouter,Depends,Request,Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.admin_auth import require_authenticated
from app.operations_access import can_access_team,has_team_permission,is_manager,require_csrf,resolve_actor
from app.operations_db import get_db
from app.operations_models import AuditLog,ChangeRequest,KPI,KPIResult,Notification,Person,ReviewAction,Task,Team,WeeklyReview

class TaskCreate(BaseModel):
    task_id:str|None=None
    name:str=Field(min_length=2,max_length=240)
    task_type:str="General"
    team_id:str
    assigned_to:str|None=None
    priority:str="Medium"
    date_assigned:date|None=None
    deadline:date|None=None
    reviewer:str|None=None
class TaskPatch(BaseModel):
    model_config=ConfigDict(extra="forbid")
    expected_version:int=Field(ge=1)
    status:str|None=None
    name:str|None=Field(default=None,min_length=2,max_length=240)
    task_type:str|None=Field(default=None,max_length=64)
    percent_complete:int|None=Field(default=None,ge=0,le=100)
    deadline:date|None=None
    assigned_to:str|None=None
    priority:str|None=None
    output_link:str|None=Field(default=None,max_length=2000)
    blocker_note:str|None=Field(default=None,max_length=5000)
class KPIResultInput(BaseModel):
    period_start:date
    actual:Decimal
    target_override:Decimal|None=None
    comment:str|None=None
    next_action:str|None=None
class ActionInput(BaseModel):
    owner_person_id:str|None=None
    description:str=Field(min_length=2,max_length=2000)
    due_date:date|None=None
class ReviewInput(BaseModel):
    team_id:str
    week_start:date
    key_achievement:str|None=None
    challenge:str|None=None
    decision:str|None=None
    actions:list[ActionInput]=Field(default_factory=list,max_length=30)
class ChangeInput(BaseModel):
    entity:str="task"
    entity_id:str
    changes:dict[str,Any]
    reason:str=Field(min_length=3,max_length=2000)

def validate_requested_task_changes(task:Task, changes:dict[str,Any], db:Session):
    allowed={"name","task_type","assigned_to","priority","deadline","status","percent_complete","output_link","blocker_note"}
    if not changes or set(changes)-allowed:return "Unsupported or empty task change"
    if "priority" in changes and changes["priority"] not in {"Low","Medium","High","Urgent"}:return "Invalid priority"
    statuses={"Not Started","In Progress","In Review","Blocked","On Hold","Completed","Cancelled","Closed"}
    if "status" in changes and changes["status"] not in statuses:return "Invalid task status"
    if "percent_complete" in changes and (not isinstance(changes["percent_complete"],int) or not 0<=changes["percent_complete"]<=100):return "Progress must be between 0 and 100"
    if "deadline" in changes and changes["deadline"]:
        try:date.fromisoformat(changes["deadline"])
        except (TypeError,ValueError):return "Deadline must be an ISO date"
    if "assigned_to" in changes and changes["assigned_to"]:
        person=db.get(Person,changes["assigned_to"])
        if not person or person.status!="Active" or person.team_id!=task.team_id:return "Assignee must be active and belong to the task team"
    transitions={"Not Started":{"In Progress","Blocked","On Hold","Cancelled"},"In Progress":{"In Review","Blocked","On Hold","Completed","Cancelled"},"In Review":{"In Progress","Blocked","Completed"},"Blocked":{"In Progress","Cancelled"},"On Hold":{"In Progress","Cancelled"},"Completed":{"Closed"},"Cancelled":{"Closed"},"Closed":{"In Progress"}}
    if "status" in changes and changes["status"]!=task.status and changes["status"] not in transitions.get(task.status,set()):return "Task status transition is not allowed"
    if changes.get("status")=="Completed" and changes.get("percent_complete",100)!=100:return "Completed tasks must be 100 percent complete"
    return None

def create_operations_write_router():
    router=APIRouter(prefix="/api/v1/operations",tags=["operations-write"])
    def auth(request,db):
        session=require_authenticated(request)
        if not session:return None,None,JSONResponse(status_code=401,content={"detail":"Authentication required"})
        if not request.app.state.settings.operations_db_writes_enabled:
            return None,None,JSONResponse(status_code=503,content={"detail":"PostgreSQL writes are disabled until cutover approval"})
        actor=resolve_actor(request,db)
        if not actor:return None,None,JSONResponse(status_code=403,content={"detail":"No active staff identity is linked to this account"})
        if not require_csrf(request,session):return None,None,JSONResponse(status_code=403,content={"detail":"CSRF validation failed"})
        return actor,session,None
    def log(db,actor,entity,eid,field,old,new,outcome="APPLIED",rid=None,approver=None):
        db.add(AuditLog(actor=actor,approver=approver,entity=entity,entity_id=str(eid),field=field,old=old,new=new,outcome=outcome,request_id=rid))
    def assignment_notice(db,task,person_id):
        person=db.get(Person,person_id) if person_id else None
        if not person or not person.notify or not person.telegram_chat_id:return
        db.add(Notification(person_id=person.person_id,chat_id=person.telegram_chat_id,type="task_assignment",
            reference_id=task.task_id,message=f"Task assigned: {task.name}\nPriority: {task.priority}\nDeadline: {task.deadline or 'Not set'}",
            dedupe_key=f"task_assignment:{task.task_id}:{person.person_id}:{task.version}"))
    @router.post("/tasks",status_code=201)
    def create_task(p:TaskCreate,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        if not db.get(Team,p.team_id):return JSONResponse(status_code=422,content={"detail":"Unknown team"})
        if not can_access_team(a,db,p.team_id) or not has_team_permission(a,db,p.team_id,"task.manage"):return JSONResponse(status_code=403,content={"detail":"Task management permission required"})
        if p.priority not in {"Low","Medium","High","Urgent"}:return JSONResponse(status_code=422,content={"detail":"Invalid priority"})
        if p.assigned_to:
            assignee=db.get(Person,p.assigned_to)
            if not assignee or assignee.status!="Active" or assignee.team_id!=p.team_id:return JSONResponse(status_code=422,content={"detail":"Assignee must be active and belong to the task team"})
        if p.reviewer:
            reviewer=db.get(Person,p.reviewer)
            if not reviewer or reviewer.status!="Active" or reviewer.team_id!=p.team_id:return JSONResponse(status_code=422,content={"detail":"Reviewer must be active and belong to the task team"})
        tid=p.task_id or "TSK-"+uuid.uuid4().hex[:10].upper()
        if db.get(Task,tid):return JSONResponse(status_code=409,content={"detail":"Task ID already exists"})
        t=Task(task_id=tid,name=p.name.strip(),task_type=p.task_type,team_id=p.team_id,assigned_to=p.assigned_to,assigned_by=a.person_id,reviewer=p.reviewer,priority=p.priority,date_assigned=p.date_assigned or date.today(),deadline=p.deadline,status="Not Started",percent_complete=0)
        db.add(t);db.flush();assignment_notice(db,t,t.assigned_to);log(db,a.person_id,"task",tid,"created",None,{"name":t.name,"team_id":t.team_id,"assigned_to":t.assigned_to,"priority":t.priority,"deadline":t.deadline.isoformat() if t.deadline else None});db.commit()
        return {"item":{"id":tid,"version":t.version}}
    @router.patch("/tasks/{task_id}")
    def patch_task(task_id:str,p:TaskPatch,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        t=db.get(Task,task_id)
        if not t:return JSONResponse(status_code=404,content={"detail":"Task not found"})
        if not can_access_team(a,db,t.team_id):return JSONResponse(status_code=403,content={"detail":"Team access denied"})
        if t.version!=p.expected_version:return JSONResponse(status_code=409,content={"detail":"Task version conflict","current_version":t.version})
        changes=p.model_dump(exclude={"expected_version"},exclude_unset=True)
        if not changes:return JSONResponse(status_code=422,content={"detail":"At least one task change is required"})
        self_update=a.person_id==t.assigned_to and set(changes).issubset({"status","percent_complete","output_link","blocker_note"})
        if not self_update and not has_team_permission(a,db,t.team_id,"task.manage"):return JSONResponse(status_code=403,content={"detail":"Assignee progress or task management permission required"})
        statuses={"Not Started","In Progress","In Review","Blocked","On Hold","Completed","Cancelled","Closed"}
        if "status" in changes and changes["status"] not in statuses:return JSONResponse(status_code=422,content={"detail":"Invalid task status"})
        transitions={"Not Started":{"In Progress","Blocked","On Hold","Cancelled"},"In Progress":{"In Review","Blocked","On Hold","Completed","Cancelled"},"In Review":{"In Progress","Blocked","Completed"},"Blocked":{"In Progress","Cancelled"},"On Hold":{"In Progress","Cancelled"},"Completed":{"Closed"},"Cancelled":{"Closed"},"Closed":{"In Progress"}}
        if "status" in changes and changes["status"]!=t.status and changes["status"] not in transitions.get(t.status,set()):return JSONResponse(status_code=422,content={"detail":"Task status transition is not allowed"})
        if "status" in changes and (changes["status"] in {"Closed","Cancelled"} or t.status=="Closed") and not is_manager(a,db,t.team_id):return JSONResponse(status_code=403,content={"detail":"Team manager permission required for closing or reopening tasks"})
        if "priority" in changes and changes["priority"] not in {"Low","Medium","High","Urgent"}:return JSONResponse(status_code=422,content={"detail":"Invalid priority"})
        if "assigned_to" in changes and changes["assigned_to"]:
            assignee=db.get(Person,changes["assigned_to"])
            if not assignee or assignee.status!="Active" or assignee.team_id!=t.team_id:return JSONResponse(status_code=422,content={"detail":"Assignee must be active and belong to the task team"})
        if changes.get("status")=="Completed" and changes.get("percent_complete",t.percent_complete)!=100:return JSONResponse(status_code=422,content={"detail":"Completed tasks must be 100 percent complete"})
        old_assignee=t.assigned_to
        for k,v in changes.items():
            old=getattr(t,k);setattr(t,k,v);log(db,a.person_id,"task",task_id,k,old.isoformat() if isinstance(old,date) else old,v.isoformat() if isinstance(v,date) else v)
        if changes.get("status")=="Completed":t.percent_complete=100;t.completed_at=datetime.now(UTC)
        t.version+=1
        if changes.get("assigned_to") and changes["assigned_to"]!=old_assignee:
            assignment_notice(db,t,changes["assigned_to"])
        db.commit()
        return {"item":{"id":task_id,"version":t.version,"updated":list(changes)}}
    @router.post("/kpis/{kpi_id}/results",status_code=201)
    def add_result(kpi_id:str,p:KPIResultInput,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        k=db.get(KPI,kpi_id)
        if not k:return JSONResponse(status_code=404,content={"detail":"KPI not found"})
        if not k.active:return JSONResponse(status_code=409,content={"detail":"Inactive KPIs do not accept results"})
        if not(a.is_admin or (k.owner_person_id is not None and a.person_id==k.owner_person_id) or has_team_permission(a,db,k.team_id,"kpi.manage")):return JSONResponse(status_code=403,content={"detail":"KPI owner or KPI management permission required"})
        if not p.actual.is_finite():return JSONResponse(status_code=422,content={"detail":"Actual must be finite"})
        if p.target_override is not None and (not p.target_override.is_finite() or p.target_override<=0):return JSONResponse(status_code=422,content={"detail":"Target override must be positive and finite"})
        if db.scalar(select(KPIResult).where(KPIResult.kpi_id==kpi_id,KPIResult.period_start==p.period_start)):return JSONResponse(status_code=409,content={"detail":"Result already exists for period"})
        target=p.target_override if p.target_override is not None else k.target
        r=KPIResult(kpi_id=kpi_id,period_start=p.period_start,actual=p.actual,target_used=target,target_override=p.target_override,comment=p.comment,next_action=p.next_action,logged_by=a.person_id,logged_at=datetime.now(UTC))
        db.add(r);db.flush();log(db,a.person_id,"kpi_result",f"{kpi_id}:{p.period_start}","created",None,{"actual":str(p.actual),"target":str(target)});db.commit()
        return {"item":{"id":r.id,"kpi_id":kpi_id,"period_start":p.period_start.isoformat(),"actual":str(p.actual)}}
    @router.post("/weekly-reviews",status_code=201)
    def review(p:ReviewInput,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        if not db.get(Team,p.team_id):return JSONResponse(status_code=422,content={"detail":"Unknown team"})
        if not can_access_team(a,db,p.team_id) or not has_team_permission(a,db,p.team_id,"review.manage"):return JSONResponse(status_code=403,content={"detail":"Review management permission required"})
        if not a.person_id:return JSONResponse(status_code=422,content={"detail":"Staff directory identity required"})
        r=db.scalar(select(WeeklyReview).where(WeeklyReview.team_id==p.team_id,WeeklyReview.week_start==p.week_start))
        if not r:r=WeeklyReview(team_id=p.team_id,week_start=p.week_start,submitted_by=a.person_id);db.add(r);db.flush()
        r.key_achievement,r.challenge,r.decision=p.key_achievement,p.challenge,p.decision
        for x in p.actions:
            owner=db.get(Person,x.owner_person_id) if x.owner_person_id else None
            if x.owner_person_id and (not owner or owner.status!="Active" or owner.team_id!=p.team_id):return JSONResponse(status_code=422,content={"detail":"Action owner must be active and belong to the review team"})
        for old_action in db.scalars(select(ReviewAction).where(ReviewAction.review_id==r.id)).all():db.delete(old_action)
        db.flush()
        for x in p.actions:
            db.add(ReviewAction(review_id=r.id,owner_person_id=x.owner_person_id,description=x.description,due_date=x.due_date))
        log(db,a.person_id,"weekly_review",r.id,"submitted",None,{"team":p.team_id,"week":p.week_start.isoformat()});db.commit()
        return {"item":{"id":r.id,"team_id":r.team_id,"week_start":r.week_start.isoformat()}}
    @router.post("/changes",status_code=201)
    def change(p:ChangeInput,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        t=db.get(Task,p.entity_id) if p.entity=="task" else None
        if not t:return JSONResponse(status_code=404,content={"detail":"Task not found"})
        validation=validate_requested_task_changes(t,p.changes,db)
        if validation:return JSONResponse(status_code=422,content={"detail":validation})
        if not a.person_id or not can_access_team(a,db,t.team_id):return JSONResponse(status_code=403,content={"detail":"Staff identity and team access required"})
        changes=[{"field":k,"value":v.isoformat() if isinstance(v,date) else v} for k,v in p.changes.items()]
        r=ChangeRequest(requester=a.person_id,entity="task",entity_id=t.task_id,changes=changes,tier="manager",state="AWAITING_CONFIRMATION",expires_at=datetime.now(UTC)+timedelta(hours=24))
        db.add(r);db.flush();log(db,a.person_id,"change_request",r.request_id,"created",None,{"reason":p.reason}, "AWAITING_CONFIRMATION",r.request_id);db.commit()
        return {"item":{"request_id":r.request_id,"state":r.state,"expires_at":r.expires_at.isoformat()}}
    @router.post("/changes/{rid}/confirm")
    def confirm(rid:str,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        r=db.get(ChangeRequest,rid)
        if not r:return JSONResponse(status_code=404,content={"detail":"Request not found"})
        if r.requester!=a.person_id:return JSONResponse(status_code=403,content={"detail":"Only requester can confirm"})
        expires=r.expires_at.replace(tzinfo=UTC) if r.expires_at.tzinfo is None else r.expires_at
        if r.state!="AWAITING_CONFIRMATION" or expires<=datetime.now(UTC):return JSONResponse(status_code=409,content={"detail":"Request not confirmable"})
        r.state="PENDING_APPROVAL";log(db,a.person_id,"change_request",rid,"state","AWAITING_CONFIRMATION",r.state,"PENDING_APPROVAL",rid);db.commit()
        return {"item":{"request_id":rid,"state":r.state}}
    @router.post("/changes/{rid}/approve")
    def approve(rid:str,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        r=db.get(ChangeRequest,rid);t=db.get(Task,r.entity_id) if r and r.entity=="task" else None
        if not r or not t:return JSONResponse(status_code=404,content={"detail":"Request or task not found"})
        expires=r.expires_at.replace(tzinfo=UTC) if r.expires_at.tzinfo is None else r.expires_at
        if r.state!="PENDING_APPROVAL" or expires<=datetime.now(UTC):return JSONResponse(status_code=409,content={"detail":"Request not awaiting approval"})
        if r.requester==a.person_id:return JSONResponse(status_code=403,content={"detail":"Requester cannot approve own request"})
        if not(a.is_admin or has_team_permission(a,db,t.team_id,"task.manage")):return JSONResponse(status_code=403,content={"detail":"Task approval permission required"})
        validation=validate_requested_task_changes(t,{c["field"]:c["value"] for c in r.changes},db)
        if validation:return JSONResponse(status_code=409,content={"detail":"Requested change is no longer valid: "+validation})
        for c in r.changes:
            k,v=c["field"],c["value"]
            if k=="deadline" and v:v=date.fromisoformat(v)
            if k=="assigned_to" and v:
                assignee=db.get(Person,v)
                if not assignee or assignee.status!="Active" or assignee.team_id!=t.team_id:return JSONResponse(status_code=422,content={"detail":"Assignee must be active and belong to the task team"})
            old=getattr(t,k);setattr(t,k,v);log(db,r.requester,"task",t.task_id,k,old.isoformat() if isinstance(old,date) else old,v.isoformat() if isinstance(v,date) else v,"APPROVED",rid,a.person_id)
            if k=="status" and v=="Completed":
                if t.percent_complete!=100:log(db,r.requester,"task",t.task_id,"percent_complete",t.percent_complete,100,"APPROVED",rid,a.person_id)
                t.percent_complete=100;t.completed_at=datetime.now(UTC)
        t.version+=1;r.state="APPROVED";r.approver=a.person_id;r.decided_at=datetime.now(UTC)
        log(db,a.person_id,"change_request",rid,"state","PENDING_APPROVAL","APPROVED","APPROVED",rid);db.commit()
        return {"item":{"request_id":rid,"state":r.state,"task_version":t.version}}
    @router.post("/changes/{rid}/reject")
    def reject(rid:str,request:Request,db:Session=Depends(get_db)):
        a,_,err=auth(request,db)
        if err:return err
        r=db.get(ChangeRequest,rid);t=db.get(Task,r.entity_id) if r and r.entity=="task" else None
        if not r:return JSONResponse(status_code=404,content={"detail":"Request not found"})
        if not(a.is_admin or (t and has_team_permission(a,db,t.team_id,"task.manage"))):return JSONResponse(status_code=403,content={"detail":"Task approval permission required"})
        if r.state!="PENDING_APPROVAL":return JSONResponse(status_code=409,content={"detail":"Request not awaiting approval"})
        r.state="REJECTED";r.approver=a.person_id;r.decided_at=datetime.now(UTC);log(db,a.person_id,"change_request",rid,"state","PENDING_APPROVAL","REJECTED","REJECTED",rid);db.commit()
        return {"item":{"request_id":rid,"state":r.state}}
    @router.get("/audit")
    def audit(request:Request,db:Session=Depends(get_db),limit:int=Query(100,ge=1,le=500)):
        a=resolve_actor(request,db)
        if not a:return JSONResponse(status_code=401,content={"detail":"Authentication required"})
        q=select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
        if not a.is_admin:q=q.where(AuditLog.actor==a.person_id)
        rows=db.scalars(q).all()
        return {"items":[{"id":x.audit_id,"at":x.at.isoformat(),"actor":x.actor,"entity":x.entity,"entity_id":x.entity_id,"field":x.field,"old":x.old,"new":x.new,"outcome":x.outcome} for x in rows],"total":len(rows)}
    return router
