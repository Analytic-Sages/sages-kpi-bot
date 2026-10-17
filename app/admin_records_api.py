"""Authenticated, read-only task and KPI record APIs for the Polaris control centre.

Google Sheets remains the source of truth. This module intentionally exposes no mutations;
the write API must be added only after a field-level policy and pilot workbook are approved.
"""
from __future__ import annotations

import asyncio
import logging
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
    try:
        return date.fromisoformat(value.strip()[:10]) if value.strip() else None
    except ValueError:
        return None


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
        status: str | None = Query(default=None, pattern="^(completed|in_progress|open|blocked|cancelled|other)$"),
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
            people: dict[str, dict[str, str]] = {}
            for row in people_rows:
                person_id = _record_id(row, "Person ID")
                if not person_id or person_id in people:
                    continue
                people[person_id] = {
                    "id": person_id,
                    "name": row.get("Full Name", "").strip() or person_id,
                    "team": row.get("Team", "").strip() or "Unassigned team",
                    "active": _status(row.get("Status", "")) != "cancelled"
                        and row.get("Status", "").strip().casefold() in
                        {"active", "yes", "true", "1", "enabled", "y", "current"},
                }
            seen: set[str] = set()
            items: list[dict[str, Any]] = []
            needle = (query or "").casefold().strip()
            for row in task_rows:
                task_id = _record_id(row, "Task ID")
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                owner_id = _record_id(row, "Assigned To ID")
                owner = people.get(owner_id, {})
                raw_status = row.get("Status", "").strip()
                state = _status(raw_status)
                due = _parse_date(row.get("Due Date", "") or row.get("Deadline", "") or row.get("Target Date", ""))
                if status and state != status:
                    continue
                if team and owner.get("team", "").casefold() != team.casefold():
                    continue
                if person and person.casefold() not in {owner_id.casefold(), owner.get("name", "").casefold()}:
                    continue
                if needle and needle not in " ".join((task_id, row.get("Task Name", ""), owner.get("name", ""))).casefold():
                    continue
                overdue = bool(due and due < today and state != "completed" and state != "cancelled")
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
                    "updated_date": (
                        _parse_date(row.get("Last Updated", "") or row.get("Updated At", "") or row.get("Last Check-in", "")).isoformat()
                        if _parse_date(row.get("Last Updated", "") or row.get("Updated At", "") or row.get("Last Check-in", "")) else None
                    ),
                    "overdue": overdue,
                    "blocker": row.get("Blocker", "") or row.get("Blockers", "") or row.get("Blocked Reason", ""),
                })
            items.sort(key=lambda item: (not item["overdue"], item["due_date"] or "9999-12-31", item["id"]))
            return _response({
                "source": {"system": "Google Sheets", "status": "read_success", "worksheet": TASKS},
                "items": items[offset:offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            })
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_task_list_failed reason=source_unavailable")
            return _response({"detail": "Task data is temporarily unavailable. Check the workbook connection and schema."}, 503)

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
                    "due_date": row.get("Due Date", "") or row.get("Deadline", "") or row.get("Target Date", "") or None,
                    "updated_date": row.get("Last Updated", "") or row.get("Updated At", "") or row.get("Last Check-in", "") or None,
                    "blocker": row.get("Blocker", "") or row.get("Blockers", "") or row.get("Blocked Reason", ""),
                },
            })
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_task_detail_failed reason=source_unavailable")
            return _response({"detail": "Task data is temporarily unavailable. Check the workbook connection and schema."}, 503)

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
            people_by_id = {
                _record_id(row, "Person ID"): row for row in people_rows
                if _record_id(row, "Person ID")
            }
            latest: dict[str, dict[str, str]] = {}
            for row in log_rows:
                key = _record_id(row, "KPI ID")
                if not key:
                    continue
                if key not in latest or row.get("Week Start", "") > latest[key].get("Week Start", ""):
                    latest[key] = row
            items = []
            for row in kpi_rows:
                kpi_id = _record_id(row, "KPI ID")
                if not kpi_id:
                    continue
                active = row.get("Active?", "").strip().casefold() in {"true", "yes", "1", "active", "enabled"}
                if active_only and not active:
                    continue
                owner_id = _record_id(row, "Owner Person ID")
                owner = people_by_id.get(owner_id, {})
                if team and owner.get("Team", "").strip().casefold() != team.casefold():
                    continue
                if person and person.casefold() not in {owner_id.casefold(), owner.get("Full Name", "").strip().casefold()}:
                    continue
                observation = latest.get(kpi_id, {})
                items.append({
                    "id": kpi_id,
                    "name": row.get("KPI", "").strip() or row.get("KPI Label", "").strip() or kpi_id,
                    "owner_id": owner_id or None,
                    "owner_name": owner.get("Full Name", "").strip() or "Unassigned",
                    "team": owner.get("Team", "").strip() or "Unassigned team",
                    "target": row.get("Target", "").strip() or None,
                    "actual": observation.get("Actual", "").strip() or None,
                    "week_start": observation.get("Week Start", "").strip() or None,
                    "active": active,
                    "direction": row.get("Direction", "").strip() or row.get("Target Direction", "").strip() or None,
                })
            items.sort(key=lambda item: (item["team"].casefold(), item["name"].casefold(), item["id"]))
            return _response({
                "source": {"system": "Google Sheets", "status": "read_success", "worksheet": KPIS},
                "items": items[offset:offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            })
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_kpi_list_failed reason=source_unavailable")
            return _response({"detail": "KPI data is temporarily unavailable. Check the workbook connection and schema."}, 503)

    return router
