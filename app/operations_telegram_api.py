"""Authenticated Telegram webhook for staff self-service commands."""
from __future__ import annotations
import secrets
from datetime import UTC,datetime
import httpx
from fastapi import APIRouter,Depends,Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.operations_db import get_db
from app.operations_models import AuditLog,Person,Task,TelegramUpdate,Setting

def create_operations_telegram_router():
    router=APIRouter(prefix="/api/v1/operations/telegram",tags=["operations-telegram"])
    @router.post("/webhook")
    async def webhook(request:Request,db:Session=Depends(get_db)):
        settings=request.app.state.settings
        secret=settings.telegram_webhook_secret
        supplied=request.headers.get("X-Telegram-Bot-Api-Secret-Token","")
        if not secret or not secrets.compare_digest(secret,supplied):
            return JSONResponse(status_code=401,content={"detail":"Invalid webhook secret"})
        try:payload=await request.json()
        except Exception:return JSONResponse(status_code=400,content={"detail":"Invalid JSON"})
        update_id=payload.get("update_id")
        message=payload.get("message") or {}
        chat=message.get("chat") or {}
        chat_id=chat.get("id")
        text=str(message.get("text") or "").strip()
        if not isinstance(update_id,int):return JSONResponse(status_code=400,content={"detail":"Missing update ID"})
        inserted=db.execute(insert(TelegramUpdate).values(update_id=update_id).on_conflict_do_nothing(index_elements=["update_id"]).returning(TelegramUpdate.update_id)).scalar_one_or_none()
        if inserted is None:
            db.rollback()
            return {"ok":True,"duplicate":True}
        db.commit()
        if not chat_id or chat.get("type")!="private" or not text.startswith("/"):
            return {"ok":True,"ignored":True}
        person=db.scalar(select(Person).where(Person.telegram_chat_id==str(chat_id),Person.status=="Active"))
        if person is None:return {"ok":True,"ignored":True}
        command,*args=text.split(maxsplit=1)
        command=command.split("@",1)[0].casefold()
        reply="Use /help to see available staff commands."
        if command in {"/start","/help"}:
            reply=("SageOps staff assistant\n/mytasks — your open tasks\n/task TASK-ID — task details\n"
                   "/done TASK-ID — mark your assigned task completed (when enabled)")
        elif command=="/mytasks":
            rows=db.scalars(select(Task).where(Task.assigned_to==person.person_id,Task.status.notin_({"Completed","Cancelled","Closed"})).order_by(Task.deadline.asc().nulls_last()).limit(15)).all()
            reply="Your open tasks:\n"+"\n".join(f"• {x.task_id}: {x.name} — {x.status} — due {x.deadline or 'not set'}" for x in rows) if rows else "You have no open tasks."
        elif command=="/task" and args:
            task=db.get(Task,args[0].strip())
            if task and (task.assigned_to==person.person_id or task.assigned_by==person.person_id or (person.team_id==task.team_id)):
                reply=f"{task.task_id}: {task.name}\nStatus: {task.status}\nDeadline: {task.deadline or 'not set'}\nProgress: {task.percent_complete}%"
            else:reply="Task not found or you do not have access."
        elif command=="/done" and args:
            task=db.get(Task,args[0].strip())
            writes_enabled=settings.operations_db_writes_enabled and bool(db.get(Setting,"chat_writes_enabled") and db.get(Setting,"chat_writes_enabled").value)
            if not writes_enabled:reply="Task updates from Telegram are currently disabled. Please use the Control Centre."
            elif not task or task.assigned_to!=person.person_id:reply="You can only complete a task assigned to you."
            elif task.status in {"Cancelled","Closed"}:reply="This task is closed and cannot be completed."
            else:
                old=task.status;task.status="Completed";task.percent_complete=100;task.completed_at=datetime.now(UTC);task.version+=1
                db.add(AuditLog(actor=person.person_id,entity="task",entity_id=task.task_id,field="status",old=old,new="Completed",outcome="APPLIED"))
                db.commit();reply=f"Marked {task.task_id} as completed."
        if settings.telegram_bot_token:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",json={"chat_id":chat_id,"text":reply})
        return {"ok":True}
    return router
