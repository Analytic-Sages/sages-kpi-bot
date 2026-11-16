"""Daily workbook/PostgreSQL parity check for the seven-day SageOps shadow window."""
from __future__ import annotations
import argparse
import json
from datetime import UTC,datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.operations_db import SessionLocal
from app.operations_models import JobRun,KPI,Person,Task,Team
from scripts.import_sageops_workbook import load

def run_check(workbook:Path,record:bool=True):
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    data,_=load(workbook)
    source={
      "teams":{str(x["Team ID"]) for x in data["Teams"] if x.get("Team ID")},
      "people":{str(x["Person ID"]) for x in data["People Directory"] if x.get("Person ID")},
      "kpis":{str(x["KPI ID"]) for x in data["KPI Register"] if x.get("KPI ID")},
      "tasks":{str(x["Task ID"]) for x in data["Master Task Tracker"] if x.get("Task ID")},
    }
    with SessionLocal() as db:
        database={
          "teams":{x.team_id for x in db.scalars(select(Team)).all()},
          "people":{x.person_id for x in db.scalars(select(Person)).all()},
          "kpis":{x.kpi_id for x in db.scalars(select(KPI)).all()},
          "tasks":{x.task_id for x in db.scalars(select(Task)).all()},
        }
        diff={}
        for key in source:
            diff[key]={"missing_in_database":sorted(source[key]-database[key]),"unexpected_in_database":sorted(database[key]-source[key])}
        source_task={str(x["Task ID"]):str(x.get("Status") or "Not Started") for x in data["Master Task Tracker"] if x.get("Task ID")}
        db_tasks={x.task_id:x.status for x in db.scalars(select(Task)).all()}
        diff["task_status_mismatches"]=[{"task_id":k,"workbook":v,"database":db_tasks.get(k)} for k,v in source_task.items() if db_tasks.get(k)!=v]
        passed=not any(diff[k]["missing_in_database"] or diff[k]["unexpected_in_database"] for k in ("teams","people","kpis","tasks")) and not diff["task_status_mismatches"]
        now=datetime.now(UTC)
        if record:
            db.add(JobRun(job_name="sageops_shadow",started_at=now,finished_at=now,status="passed" if passed else "failed",detail=json.dumps(diff)[:4000]))
            db.commit()
        tz=ZoneInfo("Africa/Lagos")
        local_today=now.astimezone(tz).date()
        runs=db.scalars(select(JobRun).where(JobRun.job_name=="sageops_shadow",JobRun.status=="passed",JobRun.started_at>=now-timedelta(days=8)).order_by(JobRun.started_at.desc())).all()
        passed_days={x.started_at.astimezone(tz).date() for x in runs}
        passed_days.add(local_today) if passed and not record else None
        consecutive=0
        for n in range(7):
            if local_today-timedelta(days=n) in passed_days:consecutive+=1
            else:break
        return {"status":"passed" if passed else "mismatch","checked_at":now.isoformat(),"consecutive_pass_days":consecutive,"seven_day_shadow_complete":consecutive>=7,"differences":diff}

def main():
    p=argparse.ArgumentParser();p.add_argument("workbook",type=Path);p.add_argument("--no-record",action="store_true");a=p.parse_args()
    result=run_check(a.workbook,record=not a.no_record);print(json.dumps(result,indent=2));return 0 if result["status"]=="passed" else 2
if __name__=="__main__":
    raise SystemExit(main())
