"""Loss-aware, one-time import from the SageOps Excel migration source.

The default command only writes a reconciliation report. `--apply` is refused unless all
references validate and every target operational table is empty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select

from app.operations_db import SessionLocal
from app.operations_models import (
    AuditLog, Blocker, ChangeRequest, JobRun, KPI, KPIResult, Notification,
    NotificationAttempt, Person, ReviewAction, Role, RolePermission, Setting, Task,
    TaskDetail, TaskReminderState, Team, TelegramUpdate, WebhookEvent, WeeklyReview,
)

SHEETS = {
    "Teams": "Team Name",
    "People Directory": "Full Name",
    "Job_Descriptions": "Role",
    "KPI Register": "KPI",
    "Weekly KPI Log": "KPI ID",
    "Master Task Tracker": "Task Name",
    "Weekly Review": "Team",
    "Blocker Log Worksheet": "Description ",
    "Reminder Log": "Log ID",
    "Config": "Setting Key",
    "Lists": None,
}
DERIVED_SHEETS = {
    "START HERE": "Operator instructions",
    "Dashboard": "Formula-derived presentation",
    "Team Roster": "Formula-derived view of Teams and People Directory",
    "Person Digest": "Formula-derived current snapshot; delivery time is retained on People Directory",
    "Bot Guide": "Operator instructions",
    "Data Dictionary": "Workbook documentation",
}
VALID_TASK_STATUSES = {"Not Started", "In Progress", "In Review", "Blocked", "On Hold", "Completed", "Cancelled", "Closed"}
VALID_PRIORITIES = {"Low", "Medium", "High", "Urgent"}
VALID_DIRECTIONS = {"Increase", "Decrease", "Maintain"}
VALID_FREQUENCIES = {"Daily", "Weekly", "Monthly", "Quarterly"}
FORMULA_REFERENCE_FIELDS = {
    "Team Lead ID", "Reports To ID", "Replacement For ID", "Owner Person ID",
    "Assigned To ID", "Assigned By ID", "Reviewer ID", "Action Owner ID",
}


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _date(value: Any) -> date | None:
    value = _clean(value)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y"):
            try:
                return datetime.strptime(value[:10], fmt).date()
            except ValueError:
                continue
    return None


def _truth(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"yes", "true", "1", "active", "enabled", "y"}


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
        return number if number.is_finite() else None
    except (InvalidOperation, AttributeError):
        return None


def _role_id(name: str) -> str:
    return "ROLE-" + re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:27].strip("-")


def _read_sheet(values_wb, formulas_wb, name: str, key: str | None) -> list[dict[str, Any]]:
    ws = values_wb[name]
    formula_ws = formulas_wb[name]
    headers = [str(_clean(cell.value) or "").strip() for cell in ws[1]]
    if key and key.strip() not in headers:
        raise ValueError(f"{name}: missing required header {key.strip()}")
    records = []
    for row_no in range(2, ws.max_row + 1):
        data: dict[str, Any] = {}
        for col_no, header in enumerate(headers, 1):
            if not header:
                continue
            formula = formula_ws.cell(row_no, col_no).value
            is_formula = isinstance(formula, str) and formula.startswith("=")
            if is_formula and header not in FORMULA_REFERENCE_FIELDS:
                continue
            value = _clean(ws.cell(row_no, col_no).value)
            if value is not None:
                data[header] = value
                if is_formula:
                    data.setdefault("_formula_reference_fields", []).append(header)
        if data and (key is None or data.get(key.strip()) is not None):
            data["_row"] = row_no
            records.append(data)
    return records


def load(path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    formulas_wb = load_workbook(path, data_only=False, read_only=False)
    values_wb = load_workbook(path, data_only=True, read_only=False)
    missing = (set(SHEETS) | set(DERIVED_SHEETS)) - set(values_wb.sheetnames)
    if missing:
        formulas_wb.close(); values_wb.close()
        raise ValueError(f"Missing workbook sheets: {sorted(missing)}")
    data = {name: _read_sheet(values_wb, formulas_wb, name, key) for name, key in SHEETS.items()}
    data["_formula_counts"] = {
        name: sum(
            1 for row in ws.iter_rows() for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        )
        for name, ws in ((sheet, formulas_wb[sheet]) for sheet in values_wb.sheetnames)
    }
    data["_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    names = list(values_wb.sheetnames)
    formulas_wb.close(); values_wb.close()
    return data, names


def _record_ids(rows: list[dict[str, Any]], key: str) -> list[str]:
    return [str(row[key]).strip() for row in rows if row.get(key)]


def report(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    teams = data["Teams"]
    people = data["People Directory"]
    roles = data["Job_Descriptions"]
    kpis = data["KPI Register"]
    results = data["Weekly KPI Log"]
    tasks = data["Master Task Tracker"]
    reviews = data["Weekly Review"]
    blockers = data["Blocker Log Worksheet"]
    reminders = data["Reminder Log"]
    identities = {
        "team": _record_ids(teams, "Team ID"),
        "person": _record_ids(people, "Person ID"),
        "kpi": _record_ids(kpis, "KPI ID"),
        "task": _record_ids(tasks, "Task ID"),
        "blocker": _record_ids(blockers, "Blocker ID"),
        "reminder": _record_ids(reminders, "Log ID"),
    }
    duplicates = {kind: sorted({value for value in ids if ids.count(value) > 1}) for kind, ids in identities.items()}
    def duplicate_values(rows, field, normalize=lambda x: str(x).casefold().strip()):
        values = [normalize(row[field]) for row in rows if row.get(field) not in (None, "")]
        return sorted({value for value in values if values.count(value) > 1})
    duplicates.update({
        "team_name": duplicate_values(teams, "Team Name"),
        "person_email": duplicate_values(people, "Email"),
        "telegram_chat_id": duplicate_values(people, "Telegram Chat ID"),
        "role_name": duplicate_values(roles, "Role"),
    })
    result_keys = [(str(row.get("KPI ID")), str(_date(row.get("Week Start")))) for row in data["Weekly KPI Log"]]
    duplicates["kpi_period"] = sorted({key[0] + "|" + key[1] for key in result_keys if result_keys.count(key) > 1})
    team_names = {str(x.get("Team Name")) for x in teams if x.get("Team Name")}
    person_ids = set(identities["person"])
    kpi_ids = set(identities["kpi"])
    role_names = {str(x.get("Role")).casefold() for x in roles if x.get("Role")}
    role_names.update(str(x.get("Role")).casefold() for x in people if x.get("Role"))
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def issue(sheet: str, row: dict[str, Any], code: str, field: str | None = None):
        item = {"sheet": sheet, "row": row.get("_row"), "issue": code}
        if field:
            item["field"] = field
        issues.append(item)

    for row in teams:
        if not row.get("Team ID"):
            issue("Teams", row, "missing_team_id")
        if row.get("Team Lead ID") and row["Team Lead ID"] not in person_ids:
            issue("Teams", row, "unknown_team_lead", "Team Lead ID")
    for row in people:
        if not row.get("Person ID"):
            issue("People Directory", row, "missing_person_id")
        if row.get("Team") and row["Team"] not in team_names:
            issue("People Directory", row, "unknown_team", "Team")
        for field in ("Reports To ID", "Replacement For ID"):
            if row.get(field) and row[field] not in person_ids:
                issue("People Directory", row, "unknown_person_reference", field)
        if row.get("Role") and str(row["Role"]).casefold() not in role_names:
            warnings.append({"sheet": "People Directory", "row": row["_row"], "issue": "role_without_job_description"})
        if row.get("Status") and str(row["Status"]) not in {"Active", "Inactive", "On Leave"}:
            issue("People Directory", row, "invalid_status", "Status")
        for field in ("Start Date", "End Date"):
            if row.get(field) and _date(row[field]) is None:
                issue("People Directory", row, "invalid_date", field)
    for row in kpis:
        if not row.get("KPI ID"):
            issue("KPI Register", row, "missing_kpi_id")
        if row.get("Team") and row["Team"] not in team_names:
            issue("KPI Register", row, "unknown_team", "Team")
        if row.get("Owner Person ID") and row["Owner Person ID"] not in person_ids:
            issue("KPI Register", row, "unknown_owner", "Owner Person ID")
        if not row.get("Owner Person ID"):
            warnings.append({"sheet": "KPI Register", "row": row["_row"], "issue": "unassigned_team_kpi", "kpi_id": row.get("KPI ID")})
        if _number(row.get("Target")) is None:
            issue("KPI Register", row, "invalid_or_missing_target", "Target")
        if row.get("Direction") and row["Direction"] not in VALID_DIRECTIONS:
            issue("KPI Register", row, "invalid_direction", "Direction")
        if row.get("Frequency") and row["Frequency"] not in VALID_FREQUENCIES:
            issue("KPI Register", row, "invalid_frequency", "Frequency")
    for row in results:
        if row.get("KPI ID") not in kpi_ids:
            issue("Weekly KPI Log", row, "unknown_kpi", "KPI ID")
        if _date(row.get("Week Start")) is None:
            issue("Weekly KPI Log", row, "invalid_or_missing_period", "Week Start")
        if row.get("Actual") not in (None, "") and _number(row.get("Actual")) is None:
            issue("Weekly KPI Log", row, "invalid_actual", "Actual")
    for row in tasks:
        if not row.get("Task ID"):
            issue("Master Task Tracker", row, "missing_task_id")
        if row.get("Team") and row["Team"] not in team_names:
            issue("Master Task Tracker", row, "unknown_team", "Team")
        for field in ("Assigned To ID", "Assigned By ID", "Reviewer ID"):
            if row.get(field) and row[field] not in person_ids:
                issue("Master Task Tracker", row, "unknown_person_reference", field)
        if _date(row.get("Date Assigned")) is None:
            issue("Master Task Tracker", row, "invalid_or_missing_assignment_date", "Date Assigned")
        if row.get("Status") and row["Status"] not in VALID_TASK_STATUSES:
            issue("Master Task Tracker", row, "invalid_status", "Status")
        if row.get("Priority") and row["Priority"] not in VALID_PRIORITIES | {"Critical"}:
            issue("Master Task Tracker", row, "invalid_priority", "Priority")
    for row in reviews:
        if row.get("Team") not in team_names:
            issue("Weekly Review", row, "unknown_team", "Team")
        if row.get("Week Start") and _date(row["Week Start"]) is None:
            issue("Weekly Review", row, "invalid_week_start", "Week Start")
        if row.get("Action Owner ID") and row["Action Owner ID"] not in person_ids:
            issue("Weekly Review", row, "unknown_action_owner", "Action Owner ID")
    for row in blockers:
        if row.get("Task ID") and row["Task ID"] not in set(identities["task"]):
            issue("Blocker Log Worksheet", row, "unknown_task", "Task ID")
        if row.get("Person ID") and row["Person ID"] not in person_ids:
            issue("Blocker Log Worksheet", row, "unknown_reporter", "Person ID")
    for row in reminders:
        if row.get("Person ID") and row["Person ID"] not in person_ids:
            issue("Reminder Log", row, "unknown_person", "Person ID")

    list_values = data["Lists"]
    list_headers = sorted({field for row in list_values for field in row if not field.startswith("_")})
    return {
        "counts": {
            "teams": len(teams), "people": len(people), "roles": len(roles),
            "kpis": len(kpis), "weekly_kpi_results": len(results), "tasks": len(tasks),
            "weekly_review_rows": len(reviews), "blockers": len(blockers),
            "reminder_history": len(reminders), "config_entries": len(data["Config"]),
            "list_configuration_columns": len(list_headers),
        },
        "duplicates": duplicates,
        "issues": issues,
        "warnings": warnings,
        "formula_cells_not_imported": data.get("_formula_counts", {}),
        "preservation": {
            "source_data_json": "All non-empty source values are retained; formula outputs are omitted except cached stable identity references needed to preserve relationships.",
            "derived_sheets": DERIVED_SHEETS,
            "lists_preserved_as_setting": True,
            "no_individual_owner_invented": True,
        },
        "ready": not issues and not any(duplicates.values()),
    }


def _source(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _json_value(v) for k, v in row.items() if not k.startswith("_")}


def _empty_target(db) -> bool:
    # Worker execution history is operational metadata, not imported business data.
    # Preserve existing job_runs rows and allow them to coexist with the one-time
    # workbook import; all business tables must still be empty.
    tables = (Team, Role, RolePermission, Person, KPI, KPIResult, Task, TaskDetail,
              TaskReminderState, WeeklyReview, ReviewAction, Blocker, Notification,
              NotificationAttempt, ChangeRequest, AuditLog, TelegramUpdate, WebhookEvent)
    return all(db.scalar(select(model).limit(1)) is None for model in tables)


def _setting_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.casefold() in {"yes", "true", "enabled"}:
            return True
        if text.casefold() in {"no", "false", "disabled"}:
            return False
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text
    return _json_value(value)


def apply(data: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    db = SessionLocal()
    try:
        if not _empty_target(db):
            raise RuntimeError("Import requires an empty operational target; no existing records were changed")
        team_by_name: dict[str, str] = {}
        for row in data["Teams"]:
            team_id, name = str(row["Team ID"]), str(row["Team Name"])
            team_by_name[name] = team_id
            db.add(Team(team_id=team_id, name=name[:120], notes=row.get("Notes"), source_data=_source(row)))
        role_rows: dict[str, dict[str, Any]] = {}
        for row in data["Job_Descriptions"]:
            if row.get("Role"):
                role_rows[str(row["Role"]).casefold()] = row
        for row in data["People Directory"]:
            if row.get("Role"):
                role_rows.setdefault(str(row["Role"]).casefold(), {"Role": row["Role"]})
        role_ids = {}
        for row in role_rows.values():
            name = str(row["Role"])
            role_id = _role_id(name)
            role_ids[name.casefold()] = role_id
            metrics = row.get("Key Output Metrics")
            output_metrics = {"description": str(metrics)} if metrics else {}
            db.add(Role(role_id=role_id, name=name[:120], core_domain=row.get("Core Domain"),
                        responsibilities=row.get("Job Description"), output_metrics=output_metrics,
                        source_data=_source(row)))
        db.flush()
        for row in data["People Directory"]:
            pid = str(row["Person ID"])
            status = str(row.get("Status") or "Active")
            if status not in {"Active", "Inactive", "On Leave"}:
                status = "Inactive"
            email = str(row["Email"]).casefold() if row.get("Email") else None
            db.add(Person(
                person_id=pid, full_name=str(row["Full Name"])[:160],
                team_id=team_by_name.get(str(row.get("Team"))),
                role_id=role_ids.get(str(row.get("Role", "")).casefold()),
                email=email, telegram_handle=str(row["Telegram Handle"]) if row.get("Telegram Handle") else None,
                telegram_chat_id=str(row["Telegram Chat ID"]) if row.get("Telegram Chat ID") else None,
                reports_to=str(row["Reports To ID"]) if row.get("Reports To ID") else None,
                status=status, start_date=_date(row.get("Start Date")), end_date=_date(row.get("End Date")),
                replacement_for=str(row["Replacement For ID"]) if row.get("Replacement For ID") else None,
                notify=_truth(row.get("Notify?")), notes=row.get("Notes"),
                last_digest_sent_at=(datetime.fromisoformat(str(row["Last Digest Sent At"])) if row.get("Last Digest Sent At") else None),
                source_data=_source(row),
            ))
        db.flush()
        for row in data["Teams"]:
            lead_id = str(row["Team Lead ID"]) if row.get("Team Lead ID") else None
            team = db.get(Team, str(row["Team ID"]))
            team.lead_person_id = lead_id
        for role_name, role_id in role_ids.items():
            if any(token in role_name for token in ("team lead", "manager", "head of")):
                db.add(RolePermission(role_id=role_id, permission="team.manage"))

        kpi_by_id: dict[str, KPI] = {}
        for row in data["KPI Register"]:
            kpi_id = str(row["KPI ID"])
            target = _number(row.get("Target"))
            direction = str(row.get("Direction") or "Increase")
            frequency = str(row.get("Frequency") or "Weekly")
            kpi = KPI(
                kpi_id=kpi_id, team_id=team_by_name[str(row["Team"])],
                role_id=role_ids.get(str(row.get("Role", "")).casefold()),
                name=str(row["KPI"])[:200], definition=str(row.get("Definition / Measurement") or ""),
                direction=direction if direction in VALID_DIRECTIONS else "Increase",
                frequency=frequency if frequency in VALID_FREQUENCIES else "Weekly",
                owner_person_id=str(row["Owner Person ID"]) if row.get("Owner Person ID") else None,
                target=target, unit=str(row["Unit"])[:40] if row.get("Unit") else None,
                data_source=str(row.get("Data Source") or "Manual"), active=_truth(row.get("Active?")),
                notes=row.get("Notes"), period_start=_date(row.get("Period Start")),
                checkin_due_date=_date(row.get("Check-in Due Date")),
                checkin_status=str(row["Check-in Status"]) if row.get("Check-in Status") else None,
                source_data=_source(row),
            )
            db.add(kpi); kpi_by_id[kpi_id] = kpi
        db.flush()
        for row in data["Master Task Tracker"]:
            task_id = str(row["Task ID"])
            raw_status = str(row.get("Status") or "Not Started")
            status = raw_status if raw_status in VALID_TASK_STATUSES else "Not Started"
            priority = str(row.get("Priority") or "Medium")
            priority = {"Critical": "Urgent"}.get(priority, priority)
            progress = _number(row.get("% Complete")) or Decimal(0)
            pct = int(max(0, min(100, progress * 100 if progress <= 1 else progress)))
            team_id = team_by_name[str(row["Team"])]
            db.add(Task(
                task_id=task_id, name=str(row["Task Name"])[:240],
                task_type=str(row.get("Task Type") or "General")[:64], team_id=team_id,
                assigned_to=str(row["Assigned To ID"]) if row.get("Assigned To ID") else None,
                assigned_by=str(row["Assigned By ID"]) if row.get("Assigned By ID") else None,
                reviewer=str(row["Reviewer ID"]) if row.get("Reviewer ID") else None,
                priority=priority if priority in VALID_PRIORITIES else "Medium",
                date_assigned=_date(row.get("Date Assigned")), deadline=_date(row.get("Deadline")),
                status=status, percent_complete=pct,
                completed_at=datetime.combine(_date(row.get("Completed Date")), datetime.min.time(), UTC) if _date(row.get("Completed Date")) else None,
                output_link=str(row["Output / Link"]) if row.get("Output / Link") else None,
                blocker_note=str(row["Remarks / Blocker"]) if row.get("Remarks / Blocker") else None,
            ))
            detail = {"workbook_fields": _source(row), "source_sheet": "Master Task Tracker", "source_row": row["_row"]}
            db.add(TaskDetail(task_id=task_id, task_type=str(row.get("Task Type") or "General")[:64], details=detail))

        for row in data["Weekly KPI Log"]:
            kpi_id = str(row["KPI ID"])
            kpi = kpi_by_id[kpi_id]
            actual = _number(row.get("Actual"))
            override = _number(row.get("Target Override"))
            target = override or _number(row.get("Target")) or kpi.target
            db.add(KPIResult(
                kpi_id=kpi_id, period_start=_date(row.get("Week Start")), actual=actual,
                target_used=target, target_override=override,
                comment=str(row["Comment"]) if row.get("Comment") else None,
                next_action=str(row["Next Action"]) if row.get("Next Action") else None,
                logged_by=str(row["Logged By ID"]) if row.get("Logged By ID") else None,
                logged_at=datetime.now(UTC), source_data=_source(row),
            ))

        review_groups: dict[tuple[str, date], list[dict[str, Any]]] = {}
        for row in data["Weekly Review"]:
            week = _date(row.get("Week Start"))
            if not week:
                continue
            review_groups.setdefault((team_by_name[str(row["Team"])], week), []).append(row)
        for (team_id, week), rows in review_groups.items():
            def first(field, grouped_rows=rows):
                return next((str(r[field]) for r in grouped_rows if r.get(field)), None)
            review = WeeklyReview(team_id=team_id, week_start=week,
                key_achievement=first("Key Achievement"), challenge=first("Challenge / Blocker"),
                decision=first("Decision / Next Step"), submitted_by=None,
                source_data={"workbook_rows": [_source(r) for r in rows]})
            db.add(review); db.flush()
            for row in rows:
                if not any(row.get(field) for field in ("Action Owner", "Action Owner ID", "Action Due Date", "Action Status")):
                    continue
                owner_id = str(row["Action Owner ID"]) if row.get("Action Owner ID") else None
                description = str(row.get("Action Owner") or row.get("Decision / Next Step") or "Imported review action")
                status = str(row.get("Action Status") or "Open")
                db.add(ReviewAction(review_id=review.id, owner_person_id=owner_id,
                    description=description, due_date=_date(row.get("Action Due Date")),
                    status=status if status in {"Open", "Done", "Dropped"} else "Open",
                    source_data=_source(row)))

        task_ids = {str(row["Task ID"]) for row in data["Master Task Tracker"]}
        for row in data["Blocker Log Worksheet"]:
            blocker_id = str(row.get("Blocker ID") or f"import-{data['_file_sha256'][:8]}-{row['_row']}")
            db.add(Blocker(
                blocker_id=blocker_id[:36], task_id=str(row["Task ID"]) if row.get("Task ID") in task_ids else None,
                reported_by=str(row["Person ID"]) if row.get("Person ID") else None,
                telegram_message_id=None, description=str(row.get("Description") or ""),
                status=str(row.get("Status") or "Open"), resolved_at=None,
                source_data=_source(row),
            ))

        for row in data["Reminder Log"]:
            status_raw = str(row.get("Delivery Status") or "Imported")
            status_map = {"delivered": "Sent", "sent": "Sent", "failed": "Failed", "queued": "Queued", "skipped": "Skipped"}
            status = status_map.get(status_raw.casefold(), "Imported")
            log_id = str(row.get("Log ID") or hashlib.sha256(json.dumps(_source(row), sort_keys=True).encode()).hexdigest()[:20])
            person_id = str(row["Person ID"]) if row.get("Person ID") else None
            chat_id = str(row["Telegram Chat ID"]) if row.get("Telegram Chat ID") else None
            created = row.get("Sent At")
            sent_at = created if isinstance(created, datetime) else datetime.fromisoformat(str(created)) if created else None
            item = Notification(
                person_id=person_id, chat_id=chat_id, type=str(row.get("Reminder Type") or "historical_reminder")[:40],
                reference_id=str(row["Reference ID"]) if row.get("Reference ID") else None,
                message=str(row.get("Message Summary") or ""), status=status,
                attempts=1 if status in {"Sent", "Failed"} else 0, error=str(row["Error"]) if row.get("Error") else None,
                dedupe_key=f"workbook-reminder:{data['_file_sha256'][:12]}:{log_id}"[:240],
                available_at=sent_at or datetime.now(UTC), sent_at=sent_at,
            )
            db.add(item); db.flush()
            if status in {"Sent", "Failed"}:
                db.add(NotificationAttempt(notification_id=item.id, attempted_at=sent_at or datetime.now(UTC), outcome=status.casefold(), error=item.error))

        setting_map = {
            "Cfg_BotEnabled": "bot_enabled", "Cfg_Timezone": "timezone",
            "Cfg_DueSoonDays": "due_soon_days", "Cfg_OverdueCooldownHrs": "reminder_cooldown_hours",
            "Cfg_DueCooldownHrs": "due_reminder_cooldown_hours", "Cfg_EscalateAfterDays": "escalate_after_days",
            "Cfg_EscalationCooldownHrs": "escalation_cooldown_hours", "Cfg_ManagerChatID": "manager_chat_id",
            "Cfg_OnTrack": "on_track_threshold", "Cfg_AtRisk": "at_risk_threshold",
            "Cfg_KPILogWeekday": "kpi_log_weekday", "Cfg_ReviewWeekday": "review_weekday",
            "Cfg_DigestTime": "digest_time", "Cfg_QuietHours": "quiet_hours",
            "Cfg_WeekStart": "week_start", "Cfg_KPIWeek": "kpi_week",
        }
        for row in data["Config"]:
            key = str(row.get("Setting Key"))
            value = _setting_value(row.get("Value"))
            db.merge(Setting(key=setting_map.get(key, f"workbook.{key}"), value=value,
                             description=str(row.get("What it does") or "Imported workbook configuration")))
        lists: dict[str, list[Any]] = {}
        for row in data["Lists"]:
            for key, value in row.items():
                if not key.startswith("_") and value is not None:
                    lists.setdefault(key, []).append(_json_value(value))
        db.merge(Setting(key="workbook_lists", value=lists, description="Imported workbook list values; formula-derived entries omitted."))

        result = {
            "teams": len(data["Teams"]), "roles": len(role_rows), "people": len(data["People Directory"]),
            "kpis": len(data["KPI Register"]), "weekly_kpi_results": len(data["Weekly KPI Log"]),
            "tasks": len(data["Master Task Tracker"]), "weekly_reviews": len(review_groups),
            "blockers": len(data["Blocker Log Worksheet"]), "reminder_history": len(data["Reminder Log"]),
        }
        db.add(JobRun(job_name="workbook_import", started_at=datetime.now(UTC),
                      finished_at=datetime.now(UTC), status="success",
                      detail=json.dumps({"sha256": data["_file_sha256"], "counts": result})))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--report", type=Path, default=Path("sageops-reconciliation.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    data, sheet_names = load(args.workbook)
    result = report(data)
    result["source"] = {"file": args.workbook.name, "sha256": data["_file_sha256"], "sheets": sheet_names}
    result["applied"] = False
    if args.apply:
        if not result["ready"]:
            raise SystemExit("Import blocked: resolve reconciliation issues first")
        result["imported"] = apply(data)
        result["applied"] = True
    args.report.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
