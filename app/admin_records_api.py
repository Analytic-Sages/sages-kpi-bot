"""Authenticated, read-only task and KPI record APIs for the Polaris control centre.

Google Sheets remains the source of truth. This module intentionally exposes no mutations;
the write API must be added only after a field-level policy and pilot workbook are approved.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.admin_auth import require_admin
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError

logger = logging.getLogger("polaris.admin_records")
TASKS = "Master Task Tracker"
PEOPLE = "People Directory"
KPIS = "KPI Register"
KPI_LOG = "Weekly KPI Log"


def _response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _unauthorized() -> JSONResponse:
    return _response({"detail": "Authentication required"}, 401)


def _status(value: str) -> str:
    normalized = "".join(char for char in value.casefold() if char.isalnum())
    if normalized in {"done", "complete", "completed", "closed", "resolved", "finished"}:
        return "completed"
    if any(token in normalized for token in ("block", "imped", "stuck")):
        return "blocked"
    if any(token in normalized for token in ("progress", "working", "started", "review")):
        return "in_progress"
    if normalized in {"", "open", "todo", "notstarted", "pending", "backlog", "assigned"}:
        return "open"
    if any(token in normalized for token in ("cancel", "wontdo", "notapplicable")):
        return "cancelled"
    return "other"


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for date_format in (
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%b %d, %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def _first_value(row: dict[str, str], headers: tuple[str, ...]) -> str:
    for header in headers:
        value = row.get(header, "").strip()
        if value:
            return value
    return ""


def _record_date(row: dict[str, str], headers: tuple[str, ...]) -> str | None:
    parsed = _parse_date(_first_value(row, headers))
    return parsed.isoformat() if parsed else None


def _record_id(row: dict[str, str], header: str) -> str:
    return row.get(header, "").strip()


async def _load_source(settings: Any) -> tuple[GoogleSheetsClient, dict[str, list[str]]]:
    sheets = await asyncio.to_thread(GoogleSheetsClient.from_settings, settings)
    contracts = {
        PEOPLE: ["Person ID", "Full Name", "Team", "Status"],
        TASKS: ["Task ID", "Assigned To ID", "Status"],
        KPIS: ["KPI ID", "KPI", "Owner Person ID", "Target", "Active?"],
        KPI_LOG: ["Week Start", "KPI ID", "Owner Person ID", "Target", "Actual"],
    }
    for worksheet, required in contracts.items():
        headers = await sheets.read_headers(worksheet)
        if not set(required).issubset(headers):
            raise ValueError("Required worksheet headers are missing")
    return sheets, contracts


def create_admin_records_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-records"])

    @router.get("/tasks")
    async def list_tasks(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
        status: str | None = Query(
            default=None,
            pattern="^(completed|in_progress|open|blocked|cancelled|other)$",
        ),
        team: str | None = Query(default=None, min_length=1, max_length=128),
        person: str | None = Query(default=None, min_length=1, max_length=256),
        query: str | None = Query(default=None, min_length=1, max_length=200),
        overdue_only: bool = False,
    ) -> JSONResponse:
        session = require_admin(request)
        if session is None:
            return _unauthorized()
        settings = request.app.state.settings
        if not settings.google_sheet_id:
            return _response({"detail": "Google Sheets is not configured"}, 503)
        try:
            sheets, _ = await _load_source(settings)
            people_rows, task_rows = await asyncio.gather(
                sheets.read_records(PEOPLE), sheets.read_records(TASKS)
            )
            today = datetime.now(UTC).date()
            person_ids = Counter(
                _record_id(row, "Person ID") for row in people_rows
                if _record_id(row, "Person ID")
            )
            duplicate_people = sum(count > 1 for count in person_ids.values())
            people: dict[str, dict[str, str]] = {}
            for row in people_rows:
                person_id = _record_id(row, "Person ID")
                if not person_id or person_ids[person_id] != 1:
                    continue
                people[person_id] = {
                    "id": person_id,
                    "name": row.get("Full Name", "").strip() or person_id,
                    "team": row.get("Team", "").strip() or "Unassigned team",
                }
            task_ids = Counter(
                _record_id(row, "Task ID") for row in task_rows
                if _record_id(row, "Task ID")
            )
            duplicate_tasks = sum(count > 1 for count in task_ids.values())
            items: list[dict[str, Any]] = []
            missing_task_ids = sum(not _record_id(row, "Task ID") for row in task_rows)
            missing_task_owners = 0
            unknown_task_owners = 0
            needle = (query or "").casefold().strip()
            for row in task_rows:
                task_id = _record_id(row, "Task ID")
                if not task_id or task_ids[task_id] != 1:
                    continue
                owner_id = _record_id(row, "Assigned To ID")
                if not owner_id:
                    missing_task_owners += 1
                elif owner_id not in people:
                    unknown_task_owners += 1
                owner = people.get(owner_id, {})
                raw_status = row.get("Status", "").strip()
                state = _status(raw_status)
                due_text = (
                    row.get("Due Date", "")
                    or row.get("Deadline", "")
                    or row.get("Target Date", "")
                )
                due = _parse_date(due_text)
                if status and state != status:
                    continue
                if team and owner.get("team", "").casefold() != team.casefold():
                    continue
                if person and person.casefold() not in {
                    owner_id.casefold(), owner.get("name", "").casefold()
                }:
                    continue
                searchable = " ".join(
                    (task_id, row.get("Task Name", ""), owner.get("name", ""))
                ).casefold()
                if needle and needle not in searchable:
                    continue
                overdue = bool(
                    due and due < today and state not in {"completed", "cancelled"}
                )
                if overdue_only and not overdue:
                    continue
                items.append({
                    "id": task_id,
                    "name": row.get("Task Name", "").strip() or task_id,
                    "owner_id": owner_id or None,
                    "owner_name": owner.get("name") or "Unassigned",
                    "team": owner.get("team") or "Unassigned team",
                    "status": state,
                    "status_label": raw_status or "Unknown",
                    "due_date": due.isoformat() if due else None,
                    "updated_date": _record_date(
                        row, ("Last Updated", "Updated At", "Last Check-in")
                    ),
                    "overdue": overdue,
                    "blocker": _first_value(row, ("Blocker", "Blockers", "Blocked Reason")),
                })
            items.sort(
                key=lambda item: (
                    not item["overdue"], item["due_date"] or "9999-12-31", item["id"]
                )
            )
            return _response({
                "source": {"system": "Google Sheets", "status": "read_success", "worksheet": TASKS},
                "items": items[offset:offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
                "data_quality": {
                    "duplicate_person_ids": duplicate_people,
                    "duplicate_task_ids": duplicate_tasks,
                    "tasks_missing_id": missing_task_ids,
                    "tasks_missing_owner": missing_task_owners,
                    "tasks_unknown_or_ambiguous_owner": unknown_task_owners,
                },
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            })
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_task_list_failed reason=source_unavailable")
            return _response(
                {
                    "detail": (
                        "Task data is temporarily unavailable. "
                        "Check workbook connection and schema."
                    )
                },
                503,
            )

    @router.get("/tasks/{task_id}")
    async def get_task(request: Request, task_id: str) -> JSONResponse:
        session = require_admin(request)
        if session is None:
            return _unauthorized()
        if not task_id.strip() or len(task_id) > 200:
            return _response({"detail": "Invalid task ID"}, 422)
        settings = request.app.state.settings
        if not settings.google_sheet_id:
            return _response({"detail": "Google Sheets is not configured"}, 503)
        try:
            sheets, _ = await _load_source(settings)
            people_rows, task_rows = await asyncio.gather(
                sheets.read_records(PEOPLE), sheets.read_records(TASKS)
            )
            matches = [row for row in task_rows if _record_id(row, "Task ID") == task_id.strip()]
            if len(matches) != 1:
                return _response({"detail": "Task not found or task ID is ambiguous"}, 404)
            row = matches[0]
            owner_id = _record_id(row, "Assigned To ID")
            owners = [p for p in people_rows if _record_id(p, "Person ID") == owner_id]
            owner = owners[0] if len(owners) == 1 else {}
            return _response({
                "source": {"system": "Google Sheets", "status": "read_success", "worksheet": TASKS},
                "item": {
                    "id": task_id.strip(),
                    "name": row.get("Task Name", "").strip() or task_id.strip(),
                    "owner_id": owner_id or None,
                    "owner_name": owner.get("Full Name", "").strip() or "Unassigned",
                    "team": owner.get("Team", "").strip() or "Unassigned team",
                    "status_label": row.get("Status", "").strip() or "Unknown",
                    "due_date": _first_value(
                        row, ("Due Date", "Deadline", "Target Date")
                    ) or None,
                    "updated_date": _first_value(
                        row, ("Last Updated", "Updated At", "Last Check-in")
                    ) or None,
                    "blocker": _first_value(row, ("Blocker", "Blockers", "Blocked Reason")),
                },
            })
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_task_detail_failed reason=source_unavailable")
            return _response(
                {
                    "detail": (
                        "Task data is temporarily unavailable. "
                        "Check workbook connection and schema."
                    )
                },
                503,
            )

    @router.get("/kpis")
    async def list_kpis(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
        team: str | None = Query(default=None, min_length=1, max_length=128),
        person: str | None = Query(default=None, min_length=1, max_length=256),
        active_only: bool = True,
    ) -> JSONResponse:
        session = require_admin(request)
        if session is None:
            return _unauthorized()
        settings = request.app.state.settings
        if not settings.google_sheet_id:
            return _response({"detail": "Google Sheets is not configured"}, 503)
        try:
            sheets, _ = await _load_source(settings)
            people_rows, kpi_rows, log_rows = await asyncio.gather(
                sheets.read_records(PEOPLE), sheets.read_records(KPIS), sheets.read_records(KPI_LOG)
            )
            person_id_counts = Counter(
                _record_id(row, "Person ID") for row in people_rows
                if _record_id(row, "Person ID")
            )
            duplicate_people = sum(count > 1 for count in person_id_counts.values())
            people_by_id = {
                _record_id(row, "Person ID"): row for row in people_rows
                if _record_id(row, "Person ID")
                and person_id_counts[_record_id(row, "Person ID")] == 1
            }
            kpi_id_counts = Counter(
                _record_id(row, "KPI ID") for row in kpi_rows
                if _record_id(row, "KPI ID")
            )
            duplicate_kpis = sum(count > 1 for count in kpi_id_counts.values())
            latest: dict[str, dict[str, str]] = {}
            for row in log_rows:
                key = _record_id(row, "KPI ID")
                if not key:
                    continue
                if key not in latest or row.get("Week Start", "") > latest[key].get(
                    "Week Start", ""
                ):
                    latest[key] = row
            items = []
            for row in kpi_rows:
                kpi_id = _record_id(row, "KPI ID")
                if not kpi_id or kpi_id_counts[kpi_id] != 1:
                    continue
                active = row.get("Active?", "").strip().casefold() in {
                    "true", "yes", "1", "active", "enabled"
                }
                if active_only and not active:
                    continue
                owner_id = _record_id(row, "Owner Person ID")
                owner = people_by_id.get(owner_id, {})
                if team and owner.get("Team", "").strip().casefold() != team.casefold():
                    continue
                if person and person.casefold() not in {
                    owner_id.casefold(), owner.get("Full Name", "").strip().casefold()
                }:
                    continue
                observation = latest.get(kpi_id, {})
                items.append({
                    "id": kpi_id,
                    "name": (
                        row.get("KPI", "").strip()
                        or row.get("KPI Label", "").strip()
                        or kpi_id
                    ),
                    "owner_id": owner_id or None,
                    "owner_name": owner.get("Full Name", "").strip() or "Unassigned",
                    "team": owner.get("Team", "").strip() or "Unassigned team",
                    "target": row.get("Target", "").strip() or None,
                    "actual": observation.get("Actual", "").strip() or None,
                    "week_start": observation.get("Week Start", "").strip() or None,
                    "active": active,
                    "direction": (
                        row.get("Direction", "").strip()
                        or row.get("Target Direction", "").strip()
                        or None
                    ),
                })
            items.sort(
                key=lambda item: (
                    item["team"].casefold(), item["name"].casefold(), item["id"]
                )
            )
            return _response({
                "source": {"system": "Google Sheets", "status": "read_success", "worksheet": KPIS},
                "items": items[offset:offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
                "data_quality": {
                    "duplicate_person_ids": duplicate_people,
                    "duplicate_kpi_ids": duplicate_kpis,
                    "kpis_missing_id": sum(not _record_id(row, "KPI ID") for row in kpi_rows),
                },
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            })
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_kpi_list_failed reason=source_unavailable")
            return _response(
                {
                    "detail": (
                        "KPI data is temporarily unavailable. "
                        "Check workbook connection and schema."
                    )
                },
                503,
            )

    return router
