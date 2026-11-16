"""Validated one-time import from the SageOps XLSX workbook; --apply is explicit."""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from pathlib import Path
from openpyxl import load_workbook
from sqlalchemy import select
from app.operations_db import SessionLocal
from app.operations_models import KPI,Person,Role,Task,Team

def clean(v):
    if v is None:return None
    if isinstance(v,str):return v.strip() or None
    return v
def as_date(v):
    v=clean(v)
    if isinstance(v,datetime):return v.date()
    if isinstance(v,date):return v
    if isinstance(v,str):
        for f in ("%Y-%m-%d","%d/%m/%Y","%m/%d/%Y"):
            try:return datetime.strptime(v[:10],f).date()
            except ValueError:pass
    return None
def truth(v,default=True):
    return default if v is None else str(v).strip().casefold() in {"yes","true","1","active","enabled"}
def read_sheet(wb,name,required):
    ws=wb[name];headers=[str(clean(c.value) or "").strip() for c in ws[1]]
    if required not in headers:raise ValueError(f"{name}: missing required header {required}")
    out=[]
    for n,vals in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
        row={headers[i]:clean(vals[i]) for i in range(min(len(headers),len(vals))) if headers[i]}
        if row.get(required):row["_row"]=n;out.append(row)
    return out
def load(path):
    wb=load_workbook(path,data_only=True,read_only=True)
    spec={"Teams":"Team Name","People Directory":"Full Name","KPI Register":"KPI","Master Task Tracker":"Task Name","Weekly KPI Log":"KPI","Weekly Review":"Team"}
    missing=set(spec)-set(wb.sheetnames)
    if missing:raise ValueError(f"Missing sheets: {sorted(missing)}")
    d={s:read_sheet(wb,s,h) for s,h in spec.items()}
    d["Config"]=read_sheet(wb,"Config","Setting Key")
    return d,wb.sheetnames
def report(d):
    ids={k:[str(x.get(col)) for x in d[s] if x.get(col)] for k,s,col in [
        ("teams","Teams","Team ID"),("people","People Directory","Person ID"),("kpis","KPI Register","KPI ID"),("tasks","Master Task Tracker","Task ID")]}
    duplicates={k:sorted({x for x in v if v.count(x)>1}) for k,v in ids.items()}
    team_names={x.get("Team Name") for x in d["Teams"]}
    person_ids=set(ids["people"]);issues=[]
    for row in d["People Directory"]:
        if row.get("Team") and row["Team"] not in team_names:issues.append({"sheet":"People Directory","row":row["_row"],"issue":"unknown_team","value":row["Team"]})
        if row.get("Reports To ID") and row["Reports To ID"] not in person_ids:issues.append({"sheet":"People Directory","row":row["_row"],"issue":"unknown_manager","value":row["Reports To ID"]})
    warnings=[]
    for row in d["KPI Register"]:
        if row.get("Owner Person ID") and row["Owner Person ID"] not in person_ids:issues.append({"sheet":"KPI Register","row":row["_row"],"issue":"unknown_owner_reference","value":row.get("Owner Person ID")})
        if not row.get("Owner Person ID"):warnings.append({"sheet":"KPI Register","row":row["_row"],"issue":"unassigned_team_kpi","kpi_id":row.get("KPI ID"),"kpi":row.get("KPI")})
        if row.get("Team") and row["Team"] not in team_names:issues.append({"sheet":"KPI Register","row":row["_row"],"issue":"unknown_team","value":row["Team"]})
    for row in d["Master Task Tracker"]:
        if row.get("Team") and row["Team"] not in team_names:issues.append({"sheet":"Master Task Tracker","row":row["_row"],"issue":"unknown_team","value":row["Team"]})
        for col in ("Assigned To ID","Assigned By ID","Reviewer ID"):
            if row.get(col) and row[col] not in person_ids:issues.append({"sheet":"Master Task Tracker","row":row["_row"],"issue":"unknown_person_reference","column":col,"value":row[col]})
    return {"counts":{"teams":len(d["Teams"]),"people":len(d["People Directory"]),"kpis":len(d["KPI Register"]),"tasks":len(d["Master Task Tracker"]),"weekly_kpi_results":len(d["Weekly KPI Log"]),"weekly_reviews":len(d["Weekly Review"])},"duplicates":duplicates,"issues":issues,"warnings":warnings,"ready":not issues and not any(duplicates.values())}
def apply(d):
    if SessionLocal is None:raise RuntimeError("DATABASE_URL is not configured")
    db=SessionLocal()
    try:
        if db.scalar(select(Team.team_id).limit(1)) or db.scalar(select(Person.person_id).limit(1)):raise RuntimeError("Import requires an empty target database")
        tmap={}
        for x in d["Teams"]:
            tid,name=x.get("Team ID"),x.get("Team Name")
            if tid and name:db.add(Team(team_id=str(tid),name=str(name)[:120]));tmap[str(name)]=str(tid)
        db.flush()
        rmap={}
        for x in d["People Directory"]:
            role=clean(x.get("Role"))
            if role and role not in rmap:
                rid="ROLE-"+re.sub(r"[^A-Z0-9]+","-",str(role).upper()).strip("-")[:24]
                rmap[role]=rid;db.add(Role(role_id=rid,name=str(role)[:120]))
        db.flush()
        for x in d["People Directory"]:
            pid,name=x.get("Person ID"),x.get("Full Name")
            if not(pid and name):continue
            db.add(Person(person_id=str(pid),full_name=str(name)[:160],team_id=tmap.get(str(x.get("Team"))),role_id=rmap.get(x.get("Role")),email=str(x["Email"]).casefold() if x.get("Email") else None,telegram_handle=str(x["Telegram Handle"]) if x.get("Telegram Handle") else None,telegram_chat_id=str(x["Telegram Chat ID"]) if x.get("Telegram Chat ID") else None,reports_to=str(x["Reports To ID"]) if x.get("Reports To ID") else None,status=str(x.get("Status") or "Active") if str(x.get("Status") or "Active") in {"Active","Inactive","On Leave"} else "Inactive",start_date=as_date(x.get("Start Date")),end_date=as_date(x.get("End Date")),replacement_for=str(x["Replacement For ID"]) if x.get("Replacement For ID") else None,notify=truth(x.get("Notify?"))))
        db.flush()
        for x in d["Teams"]:
            team=db.get(Team,str(x.get("Team ID")))
            lead=x.get("Team Lead ID")
            if team and lead and db.get(Person,str(lead)):team.lead_person_id=str(lead)
        for x in d["KPI Register"]:
            kid,name,owner=x.get("KPI ID"),x.get("KPI"),x.get("Owner Person ID");team=tmap.get(str(x.get("Team")))
            if not(kid and name and team):continue
            if owner and not db.get(Person,str(owner)):continue
            owner_id=str(owner) if owner else None
            direction=str(x.get("Direction") or "Increase");direction=direction if direction in {"Increase","Decrease","Maintain"} else "Increase"
            freq=str(x.get("Frequency") or "Weekly");freq=freq if freq in {"Daily","Weekly","Monthly","Quarterly"} else "Weekly"
            try:target=Decimal(str(x.get("Target") or 0))
            except InvalidOperation:target=Decimal(0)
            source=str(x.get("Data Source") or "Manual");source=source if source in {"Manual","Auto"} else "Manual"
            db.add(KPI(kpi_id=str(kid),team_id=team,role_id=rmap.get(x.get("Role")),name=str(name)[:200],definition=str(x.get("Definition / Measurement") or ""),direction=direction,frequency=freq,owner_person_id=owner_id,target=target,unit=str(x.get("Unit") or "")[:40] or None,data_source=source,active=truth(x.get("Active?"))))
        db.flush()
        for x in d["Master Task Tracker"]:
            tid,name=x.get("Task ID"),x.get("Task Name");team=tmap.get(str(x.get("Team")))
            if not(tid and name and team):continue
            status=str(x.get("Status") or "Not Started");status=status if status in {"Not Started","In Progress","In Review","Blocked","On Hold","Completed","Cancelled","Closed"} else "Not Started"
            priority=str(x.get("Priority") or "Medium");priority={"Critical":"Urgent"}.get(priority,priority);priority=priority if priority in {"Low","Medium","High","Urgent"} else "Medium"
            pct=float(x.get("% Complete") or 0);pct=int(max(0,min(100,pct*100 if pct<=1 else pct)))
            refs={k:(str(x[k]) if x.get(k) and db.get(Person,str(x[k])) else None) for k in ("Assigned To ID","Assigned By ID","Reviewer ID")}
            db.add(Task(task_id=str(tid),name=str(name)[:240],task_type=str(x.get("Task Type") or "General")[:64],team_id=team,assigned_to=refs["Assigned To ID"],assigned_by=refs["Assigned By ID"],reviewer=refs["Reviewer ID"],priority=priority,date_assigned=as_date(x.get("Date Assigned")) or date.today(),deadline=as_date(x.get("Deadline")),status=status,percent_complete=pct,output_link=str(x["Output / Link"]) if x.get("Output / Link") else None,blocker_note=str(x["Remarks / Blocker"]) if x.get("Remarks / Blocker") else None))
        db.commit()
        return {"teams":len(d["Teams"]),"people":len(d["People Directory"]),"kpis":len(d["KPI Register"]),"tasks":len(d["Master Task Tracker"])}
    except Exception:db.rollback();raise
    finally:db.close()
def main():
    ap=argparse.ArgumentParser();ap.add_argument("workbook",type=Path);ap.add_argument("--report",type=Path,default=Path("sageops-reconciliation.json"));ap.add_argument("--apply",action="store_true");args=ap.parse_args()
    d,sheets=load(args.workbook);r=report(d);r["source"]={"file":args.workbook.name,"sheets":sheets};r["applied"]=False
    if args.apply:
        if not r["ready"]:raise SystemExit("Import blocked: resolve reconciliation issues first")
        r["imported"]=apply(d);r["applied"]=True
    args.report.write_text(json.dumps(r,indent=2,default=str),encoding="utf-8");print(json.dumps(r,indent=2,default=str));return 0 if r["ready"] else 2
if __name__=="__main__":sys.exit(main())
