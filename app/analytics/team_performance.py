"""Read-only team performance analytics derived from the operational Google Sheets workbook.

The workbook remains the source of truth. Header matching is tolerant of harmless naming
variations, but the service never writes to Sheets and never fabricates missing metrics.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any


PEOPLE_SHEET = "People Directory"
TASKS_SHEET = "Master Task Tracker"
KPI_REGISTER_SHEET = "KPI Register"
KPI_LOG_SHEET = "Weekly KPI Log"

ALIASES = {
    "person_id": ("Person ID", "Owner Person ID", "Assigned To ID", "Member ID", "Employee ID"),
    "person_name": ("Full Name", "Name", "Person Name", "Member Name", "Employee Name"),
    "team": ("Team", "Department", "Squad", "Workstream"),
    "person_status": ("Status", "Active?", "Employment Status", "Member Status"),
    "task_id": ("Task ID", "ID", "Task Key"),
    "task_name": ("Task Name", "Task", "Title", "Name"),
    "task_owner": ("Assigned To ID", "Owner Person ID", "Assignee ID", "Assigned To", "Owner"),
    "task_status": ("Status", "Task Status", "State"),
    "task_due": ("Due Date", "Deadline", "Due", "Target Date", "Completion Deadline", "Due At"),
    "task_updated": ("Last Updated", "Updated At", "Last Update", "Updated On", "Last Check-in", "Last Check In"),
    "task_blocker": ("Blocker", "Blockers", "Blocker Description", "Blocked Reason", "Blocker Status"),
    "kpi_id": ("KPI ID", "Metric ID"),
    "kpi_name": ("KPI", "KPI Name", "Metric", "Metric Name"),
    "kpi_owner": ("Owner Person ID", "Person ID", "Owner ID"),
    "kpi_target": ("Target", "KPI Target"),
    "kpi_actual": ("Actual", "Actual Value", "Result"),
    "kpi_week": ("Week Start", "Week", "Reporting Week", "Period Start"),
    "kpi_direction": ("Direction", "Target Direction", "Measurement Direction", "Success Direction"),
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _get(row: dict[str, Any], field: str, default: str = "") -> str:
    aliases = {_key(alias) for alias in ALIASES.get(field, (field,))}
    for header, value in row.items():
        if _key(header) in aliases:
            return str(value or "").strip()
    return default


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("/", "-")):
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _status(value: str) -> str:
    normalized = _key(value)
    if normalized in {"done", "complete", "completed", "closed", "resolved", "finished"}:
        return "completed"
    if any(token in normalized for token in ("block", "imped", "stuck")):
        return "blocked"
    if any(token in normalized for token in ("progress", "working", "started", "review")):
        return "in_progress"
    if any(token in normalized for token in ("cancel", "wontdo", "notapplicable")):
        return "cancelled"
    if normalized in {"", "open", "todo", "notstarted", "pending", "backlog", "assigned"}:
        return "open"
    return "other"


def _active(value: str) -> bool:
    return _key(value) not in {"inactive", "disabled", "left", "terminated", "false", "no", "0"}


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round((numerator / denominator) * 100, 1) if denominator else None


def build_team_performance(
    people_rows: list[dict[str, Any]],
    task_rows: list[dict[str, Any]],
    kpi_register_rows: list[dict[str, Any]],
    kpi_log_rows: list[dict[str, Any]],
    *,
    today: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    team_filter: str | None = None,
    person_filter: str | None = None,
    status_filter: str | None = None,
) -> dict[str, Any]:
    """Aggregate metrics using known headers; missing source fields remain explicitly unknown."""
    today = today or date.today()
    end_date = end_date or today
    start_date = start_date or (end_date - timedelta(days=29))
    team_filter = (team_filter or "").strip().casefold()
    person_filter = (person_filter or "").strip()
    status_filter = (status_filter or "").strip().casefold()

    people: dict[str, dict[str, str]] = {}
    quality = Counter()
    for row in people_rows:
        person_id = _get(row, "person_id")
        name = _get(row, "person_name")
        if not person_id:
            quality["people_missing_id"] += 1
            continue
        if person_id in people:
            quality["duplicate_person_ids"] += 1
            continue
        people[person_id] = {
            "id": person_id,
            "name": name or person_id,
            "team": _get(row, "team") or "Unassigned team",
            "status": _get(row, "person_status") or "Unknown",
        }

    active_people = {
        person_id: person for person_id, person in people.items()
        if _active(person["status"])
    }
    if team_filter:
        active_people = {key: value for key, value in active_people.items()
                         if value["team"].casefold() == team_filter}
    if person_filter:
        active_people = {key: value for key, value in active_people.items()
                         if key == person_filter or value["name"].casefold() == person_filter.casefold()}
    allowed_ids = set(active_people)

    tasks: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for row in task_rows:
        task_id = _get(row, "task_id")
        owner_id = _get(row, "task_owner")
        status_raw = _get(row, "task_status")
        state = _status(status_raw)
        due = _date(_get(row, "task_due"))
        updated = _date(_get(row, "task_updated"))
        if not task_id:
            quality["tasks_missing_id"] += 1
        elif task_id in seen_task_ids:
            quality["duplicate_task_ids"] += 1
        else:
            seen_task_ids.add(task_id)
        if not owner_id:
            quality["tasks_missing_owner"] += 1
        elif owner_id not in people:
            quality["tasks_unknown_owner"] += 1
        if _get(row, "task_due") and due is None:
            quality["tasks_unparseable_due_date"] += 1
        if due and due > today + timedelta(days=3650):
            quality["tasks_implausible_due_date"] += 1
        if owner_id not in allowed_ids:
            continue
        if state == "cancelled":
            continue
        is_overdue = bool(due and due < today and state != "completed")
        # Keep outstanding overdue work visible even when its original due date predates
        # the selected reporting window. Otherwise use due date, then last update, as the
        # reporting anchor. Undated tasks remain in current workload/status totals.
        anchor = due or updated
        in_window = bool(anchor and start_date <= anchor <= end_date)
        if anchor and not in_window and not is_overdue:
            continue
        stale_update = bool(updated and (today - updated).days > 7 and state != "completed")
        task = {
            "id": task_id or f"row-{len(tasks) + 1}",
            "name": _get(row, "task_name") or task_id or "Untitled task",
            "owner_id": owner_id,
            "owner_name": people[owner_id]["name"],
            "team": people[owner_id]["team"],
            "status": state,
            "status_label": status_raw or "Unknown",
            "due_date": due.isoformat() if due else None,
            "updated_date": updated.isoformat() if updated else None,
            "overdue": is_overdue,
            "blocked": state == "blocked" or bool(_get(row, "task_blocker")),
            "stale_update": stale_update,
            "has_due_date": due is not None,
        }
        if status_filter and state != status_filter:
            continue
        tasks.append(task)

    by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_person[task["owner_id"]].append(task)
        by_team[task["team"]].append(task)

    members = []
    for person_id, person in active_people.items():
        assigned = by_person[person_id]
        completed = sum(item["status"] == "completed" for item in assigned)
        overdue = sum(item["overdue"] for item in assigned)
        blocked = sum(item["blocked"] for item in assigned)
        in_progress = sum(item["status"] == "in_progress" for item in assigned)
        open_count = sum(item["status"] == "open" for item in assigned)
        last_update = max((item["updated_date"] for item in assigned if item["updated_date"]), default=None)
        flags = []
        if overdue:
            flags.append("overdue_tasks")
        if blocked:
            flags.append("blocked_tasks")
        if any(item["stale_update"] for item in assigned):
            flags.append("stale_updates")
        if not assigned:
            flags.append("no_tasks_in_period")
        if any(not item["has_due_date"] for item in assigned):
            flags.append("missing_due_dates")
        members.append({
            **person,
            "task_count": len(assigned),
            "completed": completed,
            "in_progress": in_progress,
            "open": open_count,
            "overdue": overdue,
            "blocked": blocked,
            "completion_rate": _ratio(completed, len(assigned)),
            "last_update": last_update,
            "follow_up_reasons": flags,
        })
    members.sort(key=lambda item: (-item["overdue"], -item["blocked"], item["completion_rate"] or -1, item["name"].casefold()))

    teams = []
    for team_name in sorted({item["team"] for item in active_people.values()}):
        assigned = by_team[team_name]
        completed = sum(item["status"] == "completed" for item in assigned)
        teams.append({
            "team": team_name,
            "members": sum(person["team"] == team_name for person in active_people.values()),
            "task_count": len(assigned),
            "completed": completed,
            "in_progress": sum(item["status"] == "in_progress" for item in assigned),
            "open": sum(item["status"] == "open" for item in assigned),
            "overdue": sum(item["overdue"] for item in assigned),
            "blocked": sum(item["blocked"] for item in assigned),
            "completion_rate": _ratio(completed, len(assigned)),
        })
    teams.sort(key=lambda item: (item["completion_rate"] is None, -(item["completion_rate"] or 0), item["team"].casefold()))

    register: dict[str, dict[str, Any]] = {}
    for row in kpi_register_rows:
        kpi_id = _get(row, "kpi_id")
        if not kpi_id:
            quality["kpis_missing_id"] += 1
            continue
        if kpi_id in register:
            quality["duplicate_kpi_ids"] += 1
            continue
        register[kpi_id] = {
            "id": kpi_id,
            "name": _get(row, "kpi_name") or kpi_id,
            "owner_id": _get(row, "kpi_owner"),
            "team": _get(row, "team") or people.get(_get(row, "kpi_owner"), {}).get("team", "Unassigned team"),
            "target": _number(_get(row, "kpi_target")),
            "direction": _get(row, "kpi_direction").casefold(),
        }
        if register[kpi_id]["owner_id"] and register[kpi_id]["owner_id"] not in people:
            quality["kpis_unknown_owner"] += 1

    kpi_results = []
    kpi_weeks = set()
    for row in kpi_log_rows:
        kpi_id = _get(row, "kpi_id")
        item = register.get(kpi_id)
        if not item:
            quality["kpi_log_unmatched_register"] += 1
            continue
        owner_id = _get(row, "kpi_owner") or item["owner_id"]
        owner = people.get(owner_id)
        if not owner or owner_id not in allowed_ids:
            continue
        week = _date(_get(row, "kpi_week"))
        if _get(row, "kpi_week") and week is None:
            quality["kpi_log_unparseable_week"] += 1
        if week and not (start_date <= week <= end_date):
            continue
        target = _number(_get(row, "kpi_target"))
        if target is None:
            target = item["target"]
        actual = _number(_get(row, "kpi_actual"))
        direction = item["direction"]
        lower_is_better = any(token in direction for token in ("lower", "decrease", "maximum", "max ", "at most", "reduce"))
        attainment = None
        below_target = None
        if target is not None and actual is not None:
            if lower_is_better:
                attainment = 100.0 if actual <= target else (round(target / actual * 100, 1) if actual else 100.0)
                below_target = actual > target
            elif target > 0:
                attainment = round(actual / target * 100, 1)
                below_target = actual < target
        kpi_weeks.add(week.isoformat() if week else "undated")
        kpi_results.append({
            "kpi_id": kpi_id,
            "kpi": item["name"],
            "owner_id": owner_id,
            "owner_name": owner["name"],
            "team": owner["team"],
            "week_start": week.isoformat() if week else None,
            "target": target,
            "actual": actual,
            "attainment": attainment,
            "below_target": below_target,
            "direction_known": bool(direction),
        })
    kpi_results.sort(key=lambda item: (item["below_target"] is not True, item["attainment"] if item["attainment"] is not None else float("inf"), item["owner_name"].casefold()))

    completed = sum(item["status"] == "completed" for item in tasks)
    overdue = sum(item["overdue"] for item in tasks)
    blocked = sum(item["blocked"] for item in tasks)
    due_known = sum(item["has_due_date"] for item in tasks)
    followups = [item for item in tasks if item["overdue"] or item["blocked"] or item["stale_update"]]
    kpi_measurable = [item for item in kpi_results if item["below_target"] is not None]
    trend = []
    for offset in range(5, -1, -1):
        week_start = end_date - timedelta(days=end_date.weekday()) - timedelta(days=7 * offset)
        week_end = week_start + timedelta(days=6)
        matching = []
        for item in tasks:
            anchor = _date(item["due_date"]) or _date(item["updated_date"])
            if anchor and week_start <= anchor <= week_end:
                matching.append(item)
        trend.append({
            "week_start": week_start.isoformat(),
            "tasks_with_dates": len(matching),
            "completed": sum(item["status"] == "completed" for item in matching),
        })

    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat(), "basis": "task due date when available; otherwise task is included in current status totals; KPI week start"},
        "filters": {"team": team_filter or None, "person": person_filter or None, "status": status_filter or None},
        "summary": {
            "active_members": len(active_people),
            "tasks": len(tasks),
            "completed": completed,
            "in_progress": sum(item["status"] == "in_progress" for item in tasks),
            "open": sum(item["status"] == "open" for item in tasks),
            "overdue": overdue,
            "blocked": blocked,
            "stale_updates": sum(item["stale_update"] for item in tasks),
            "completion_rate": _ratio(completed, len(tasks)),
            "overdue_rate": _ratio(overdue, len(tasks)),
            "tasks_with_due_dates": due_known,
            "kpi_observations": len(kpi_results),
            "kpis_below_target": sum(item["below_target"] is True for item in kpi_results),
        },
        "teams": teams,
        "members": members,
        "tasks": sorted(followups, key=lambda item: (not item["overdue"], item["due_date"] or "9999-99-99", item["owner_name"].casefold())),
        "kpis": kpi_results,
        "trend": trend,
        "data_quality": {
            "counts": dict(sorted(quality.items())),
            "notes": [
                "Task dates and KPI periods are interpreted from recognized header names only.",
                "Tasks without a due date remain in workload/status totals but cannot be classified as overdue.",
                "Outstanding overdue tasks remain visible even when their due date predates the selected reporting window.",
                "A task is flagged for a stale update only when a recognized last-update date exists and is more than seven days old.",
                "KPI below-target classification is only made when target and actual are numeric; direction is treated as higher-is-better unless a direction column explicitly indicates lower-is-better.",
                "The trend counts tasks by due date, or last-update date when no due date exists; it is not a historical status snapshot.",
            ],
        },
    }
