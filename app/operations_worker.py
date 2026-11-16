"""Standalone durable notification dispatcher. Run: python -m app.operations_worker."""
from __future__ import annotations
import asyncio,logging,os
from datetime import UTC,datetime,timedelta
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.config import get_settings
from app.operations_db import SessionLocal
from app.operations_models import JobRun,Notification,Person,Setting,Task,TaskReminderState,Team
from app.operations_rules import reminder_decision,escalation_due
from zoneinfo import ZoneInfo
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"));log=logging.getLogger("sageops.worker");settings=get_settings()
def val(db,key,default):
    row=db.get(Setting,key);return row.value if row else default
def queue(db,p,kind,ref,message,at):
    if not p or not p.telegram_chat_id or not p.notify:return False
    item=Notification(person_id=p.person_id,chat_id=p.telegram_chat_id,type=kind,reference_id=ref,message=message,dedupe_key=f"{kind}:{ref}:{p.person_id}:{at.date()}",available_at=at)
    db.add(item)
    try:db.flush();return True
    except IntegrityError:db.rollback();return False
def scan():
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    now=datetime.now(UTC);count=0
    with SessionLocal() as db:
        if not val(db,"bot_enabled",False):return 0
        tz=val(db,"timezone","Africa/Lagos");today=now.astimezone(ZoneInfo(tz)).date()
        tasks=db.scalars(select(Task).where(Task.status.notin_({"Completed","Cancelled","Closed","On Hold"}))).all()
        for t in tasks:
            p=db.get(Person,t.assigned_to) if t.assigned_to else None
            if not p:continue
            s=db.get(TaskReminderState,t.task_id)
            d=reminder_decision(status=t.status,deadline=t.deadline,assigned_to=t.assigned_to,person_active=p.status=="Active",notify=p.notify,chat_id=p.telegram_chat_id,snooze_until=t.snooze_until,last_reminded_at=s.last_reminded_at if s else None,now=now,timezone=tz,due_soon_days=int(val(db,"due_soon_days",2)),cooldown_hours=int(val(db,"reminder_cooldown_hours",20)))
            if not d.eligible or not d.send_after:continue
            count+=int(queue(db,p,"task_reminder",t.task_id,f"Task reminder: {t.name}\nStatus: {t.status}\nDeadline: {t.deadline or 'Not set'}",d.send_after))
            if s is None:s=TaskReminderState(task_id=t.task_id);db.add(s)
            s.last_reminded_at=now;s.reminder_count=(s.reminder_count or 0)+1
            if t.deadline and escalation_due(deadline=t.deadline,status=t.status,today=today,last_escalated_at=s.last_escalated_at,now=now,escalate_after_days=int(val(db,"escalate_after_days",3)),cooldown_hours=int(val(db,"escalation_cooldown_hours",44))):
                team=db.get(Team,t.team_id);lead=db.get(Person,team.lead_person_id) if team and team.lead_person_id else None
                if lead:count+=int(queue(db,lead,"task_escalation",t.task_id,f"Escalation: {t.name} ({t.task_id}) remains overdue.",d.send_after));s.last_escalated_at=now
        db.commit()
    return count
async def dispatch(limit=50):
    if not settings.telegram_bot_token or os.getenv("OPERATIONS_NOTIFICATIONS_ENABLED","false").casefold()!="true" or settings.dry_run:return 0
    sent=0
    async with httpx.AsyncClient(timeout=15) as client:
        while sent<limit:
            with SessionLocal() as db:
                item=db.scalar(select(Notification).where(Notification.status=="Queued",Notification.available_at<=datetime.now(UTC)).order_by(Notification.available_at,Notification.id).with_for_update(skip_locked=True).limit(1))
                if item is None:break
                item.status="Sending";item.attempts+=1;db.commit();iid,chat,msg,attempts=item.id,item.chat_id,item.message,item.attempts
            try:
                r=await client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",json={"chat_id":chat,"text":msg});r.raise_for_status()
                if not r.json().get("ok"):raise RuntimeError("Telegram delivery rejected")
                with SessionLocal() as db:
                    x=db.get(Notification,iid);x.status="Sent";x.sent_at=datetime.now(UTC);x.error=None;db.commit()
                sent+=1
            except Exception as exc:
                with SessionLocal() as db:
                    x=db.get(Notification,iid);x.status="Queued" if attempts<5 else "Failed";x.error=type(exc).__name__;x.available_at=datetime.now(UTC)+timedelta(minutes=min(60,2**attempts));db.commit()
                log.warning("delivery_failed id=%s type=%s",iid,type(exc).__name__)
    return sent
async def main():
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    while True:
        started=datetime.now(UTC)
        try:
            queued=scan();sent=await dispatch()
            with SessionLocal() as db:db.add(JobRun(job_name="reminder_dispatch",started_at=started,finished_at=datetime.now(UTC),status="success",detail=f"queued={queued};sent={sent}"));db.commit()
        except Exception as exc:
            log.exception("worker_cycle_failed")
            with SessionLocal() as db:db.add(JobRun(job_name="reminder_dispatch",started_at=started,finished_at=datetime.now(UTC),status="failed",detail=type(exc).__name__));db.commit()
        await asyncio.sleep(int(os.getenv("OPERATIONS_WORKER_INTERVAL_SECONDS","60")))
if __name__=="__main__":asyncio.run(main())
