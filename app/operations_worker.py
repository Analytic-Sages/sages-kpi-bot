"""Standalone durable notification dispatcher. Run: python -m app.operations_worker."""
from __future__ import annotations
import asyncio
import logging
from datetime import UTC,datetime,timedelta
import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.config import get_settings
from app.logging_config import configure_logging
from app.operations_db import SessionLocal
from app.operations_models import JobRun,KPI,KPIResult,Notification,NotificationAttempt,Person,ReviewAction,Setting,Task,TaskReminderState,Team,WeeklyReview
from app.operations_rules import reminder_decision,escalation_due,next_allowed_send
from zoneinfo import ZoneInfo
settings=get_settings();configure_logging(settings.log_level);log=logging.getLogger("sageops.worker")
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env, traces_sample_rate=0.05,
                    send_default_pii=False)
def val(db,key,default):
    row=db.get(Setting,key);return row.value if row else default
def queue(db,p,kind,ref,message,at):
    if not p or not p.telegram_chat_id or not p.notify:return False
    item=Notification(person_id=p.person_id,chat_id=p.telegram_chat_id,type=kind,reference_id=ref,message=message,dedupe_key=f"{kind}:{ref}:{p.person_id}:{at.date()}",available_at=at)
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
        return True
    except IntegrityError:
        return False
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
            notification_type={"OVERDUE":"task_overdue","DUE_TODAY":"task_due_today","DUE_SOON":"task_due_soon"}[d.reminder_type]
            count+=int(queue(db,p,notification_type,t.task_id,f"Task {d.reminder_type.replace('_',' ').lower()}: {t.name}\nStatus: {t.status}\nDeadline: {t.deadline or 'Not set'}",d.send_after))
            if s is None:s=TaskReminderState(task_id=t.task_id);db.add(s)
            s.last_reminded_at=now;s.reminder_count=(s.reminder_count or 0)+1
            if t.deadline and escalation_due(deadline=t.deadline,status=t.status,today=today,last_escalated_at=s.last_escalated_at,now=now,escalate_after_days=int(val(db,"escalate_after_days",3)),cooldown_hours=int(val(db,"escalation_cooldown_hours",44))):
                team=db.get(Team,t.team_id);lead=db.get(Person,team.lead_person_id) if team and team.lead_person_id else None
                if lead:count+=int(queue(db,lead,"task_escalation",t.task_id,f"Escalation: {t.name} ({t.task_id}) remains overdue.",d.send_after));s.last_escalated_at=now
        db.commit()
    return count
def scan_digests():
    """Queue one per-person daily attention digest after the configured local time."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    now=datetime.now(UTC)
    queued=0
    with SessionLocal() as db:
        if not val(db,"bot_enabled",False):
            return 0
        tz=val(db,"timezone","Africa/Lagos")
        local=now.astimezone(ZoneInfo(tz))
        hour,minute=(str(val(db,"digest_time","08:00")).split(":")+["0"])[:2]
        if (local.hour,local.minute)<(int(hour),int(minute)) or local.hour>=20:
            return 0
        today=local.date()
        people=db.scalars(select(Person).where(Person.status=="Active",Person.notify.is_(True),Person.telegram_chat_id.is_not(None))).all()
        for person in people:
            if person.last_digest_sent_at and person.last_digest_sent_at.astimezone(ZoneInfo(tz)).date()==today:
                continue
            tasks=db.scalars(select(Task).where(Task.assigned_to==person.person_id,Task.status.notin_({"Completed","Cancelled","Closed"}))).all()
            overdue=sum(1 for t in tasks if t.deadline and t.deadline<today and t.status!="On Hold")
            due_today=sum(1 for t in tasks if t.deadline==today and t.status!="On Hold")
            due_soon=sum(1 for t in tasks if t.deadline and today<t.deadline<=today+timedelta(days=int(val(db,"due_soon_days",2))) and t.status!="On Hold")
            actions=db.scalars(select(ReviewAction).where(ReviewAction.owner_person_id==person.person_id,ReviewAction.status=="Open")).all()
            week_start=today-timedelta(days=today.weekday())
            weekly_kpis=db.scalars(select(KPI).where(KPI.owner_person_id==person.person_id,KPI.active.is_(True),KPI.frequency=="Weekly")).all()
            logged_ids=set(db.scalars(select(KPIResult.kpi_id).where(KPIResult.kpi_id.in_([k.kpi_id for k in weekly_kpis]),KPIResult.period_start==week_start)).all()) if weekly_kpis else set()
            missing_kpis=sum(1 for k in weekly_kpis if k.kpi_id not in logged_ids)
            review_due=0
            if person.team_id and local.weekday()>=5:
                team=db.get(Team,person.team_id)
                if team and team.lead_person_id==person.person_id:
                    submitted=db.scalar(select(WeeklyReview.id).where(WeeklyReview.team_id==team.team_id,WeeklyReview.week_start==week_start))
                    review_due=int(submitted is None)
            attention=overdue+due_today+due_soon+len(actions)+missing_kpis+review_due
            if attention<=0:
                continue
            message=(f"Your SageOps daily digest\nOpen tasks: {len(tasks)}\nOverdue: {overdue}\n"
                     f"Due today: {due_today}\nDue soon: {due_soon}\nOpen review actions: {len(actions)}\n"
                     f"Weekly KPI check-ins missing: {missing_kpis}\nTeam weekly review due: {review_due}")
            if queue(db,person,"daily_digest",person.person_id,message,now):
                person.last_digest_sent_at=now
                queued+=1
        # Remind team leads about the current weekly review on Saturday/Sunday.
        if local.weekday()>=5:
            week_start=today-timedelta(days=today.weekday())
            teams=db.scalars(select(Team).where(Team.lead_person_id.is_not(None))).all()
            for team in teams:
                exists=db.scalar(select(WeeklyReview.id).where(WeeklyReview.team_id==team.team_id,WeeklyReview.week_start==week_start))
                lead=db.get(Person,team.lead_person_id)
                if not exists and lead:
                    queued+=int(queue(db,lead,"weekly_review_due",team.team_id,
                                      f"Weekly review due: please submit the {team.name} review for week starting {week_start}.",next_allowed_send(now,timezone=tz)))
        db.commit()
    return queued

async def dispatch(limit=50):
    if not settings.operations_notifications_enabled:return 0
    if not settings.telegram_test_mode and (not settings.telegram_bot_token or settings.dry_run):return 0
    sent=0
    async def deliver(chat_id,message):
        if settings.telegram_test_mode:
            log.info("telegram_test_delivery simulated=true")
            return "simulated"
        async with httpx.AsyncClient(timeout=15) as client:
            response=await client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",json={"chat_id":chat_id,"text":message})
            response.raise_for_status()
            if not response.json().get("ok"):raise RuntimeError("Telegram delivery rejected")
            return "sent"
    while sent<limit:
        with SessionLocal() as db:
            item=db.scalar(select(Notification).where(Notification.status=="Queued",Notification.available_at<=datetime.now(UTC)).order_by(Notification.available_at,Notification.id).with_for_update(skip_locked=True).limit(1))
            if item is None:break
            item.status="Sending";item.attempts+=1;item.delivery_started_at=datetime.now(UTC);db.commit();iid,chat,msg,attempts=item.id,item.chat_id,item.message,item.attempts
        try:
            outcome=await deliver(chat,msg)
            with SessionLocal() as db:
                x=db.get(Notification,iid);x.status="Simulated" if outcome=="simulated" else "Sent";x.sent_at=datetime.now(UTC);x.delivery_started_at=None;x.error=None
                db.add(NotificationAttempt(notification_id=iid,outcome=outcome));db.commit()
            sent+=1
        except Exception as exc:
            with SessionLocal() as db:
                x=db.get(Notification,iid);x.status="Queued" if attempts<5 else "Failed";x.delivery_started_at=None;x.error=type(exc).__name__;x.available_at=datetime.now(UTC)+timedelta(minutes=min(60,2**attempts))
                db.add(NotificationAttempt(notification_id=iid,outcome="failed",error=type(exc).__name__));db.commit()
            log.warning("delivery_failed id=%s type=%s",iid,type(exc).__name__)
    return sent

async def run_cycle():
    """Run one scheduled cycle only when this process owns the PostgreSQL advisory lock."""
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    started=datetime.now(UTC)
    with SessionLocal() as db:
        acquired=db.scalar(text("SELECT pg_try_advisory_lock(:lock_id)"),{"lock_id":7184202601})
        if not acquired:
            log.info("worker_cycle_skipped reason=another_worker_holds_lock")
            return None
        try:
            recovered=recover_interrupted_deliveries();queued=scan()+scan_digests();sent=await dispatch()
            db.add(JobRun(job_name="reminder_dispatch",started_at=started,finished_at=datetime.now(UTC),status="success",detail=f"recovered={recovered};queued={queued};sent={sent}"));db.commit()
            return {"recovered":recovered,"queued":queued,"sent":sent}
        except Exception as exc:
            log.exception("worker_cycle_failed")
            db.rollback()
            db.add(JobRun(job_name="reminder_dispatch",started_at=datetime.now(UTC),finished_at=datetime.now(UTC),status="failed",detail=type(exc).__name__));db.commit()
            raise
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:lock_id)"),{"lock_id":7184202601})

async def main():
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    while True:
        try:
            await run_cycle()
        except Exception:
            pass
        await asyncio.sleep(settings.operations_worker_interval_seconds)

def recover_interrupted_deliveries():
    """Requeue sends left in Sending by a worker crash; the DB lock prevents lost work."""
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    now=datetime.now(UTC);cutoff=now-timedelta(minutes=15)
    with SessionLocal() as db:
        stale=db.scalars(select(Notification).where(
            Notification.status=="Sending",
            Notification.delivery_started_at.is_not(None),
            Notification.delivery_started_at<cutoff,
        ).with_for_update(skip_locked=True)).all()
        for item in stale:
            item.status="Queued";item.available_at=now;item.delivery_started_at=None
            item.error="Worker restarted during delivery; outcome may require Telegram reconciliation"
            db.add(NotificationAttempt(notification_id=item.id,outcome="interrupted",error="Worker restarted during delivery"))
        db.commit()
        return len(stale)

if __name__=="__main__":asyncio.run(main())
