"""Authenticated, read-only administration API for Polaris.

Operational write endpoints intentionally do not live here. Task/KPI data endpoints remain
unavailable until the live workbook schema and editable-field policy have been approved.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.admin_auth import require_admin
from app.analytics.team_performance import build_team_performance
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError
from app.audit.store import SQLiteAuditLog

logger = logging.getLogger("polaris.admin_api")


def _unauthorized() -> JSONResponse:
    response = JSONResponse(status_code=401, content={"detail": "Authentication required"})
    response.headers["Cache-Control"] = "no-store"
    return response


def _json_response(content: dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def create_admin_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-api"])

    @router.get("/overview")
    async def overview(request: Request) -> JSONResponse:
        session = require_admin(request)
        if session is None:
            return _unauthorized()

        settings = request.app.state.settings
        audit_path = Path(settings.audit_db_path).expanduser()
        return _json_response(
            {
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "admin": {"email": session["email"], "access": "read_only"},
                "service": {
                    "name": settings.app_name,
                    "environment": settings.app_env,
                    "dry_run": settings.dry_run,
                },
                "integrations": {
                    "google_sheets": {
                        "configured": bool(settings.google_sheet_id),
                        "status": (
                            "configured_not_checked"
                            if settings.google_sheet_id
                            else "not_configured"
                        ),
                        "note": "Connectivity is not tested by this read-only overview.",
                    },
                    "telegram": {
                        "configured": bool(settings.telegram_bot_token),
                        "status": (
                            "configured_not_checked"
                            if settings.telegram_bot_token
                            else "not_configured"
                        ),
                    },
                    "scheduled_workflows": {
                        "enabled": settings.enable_scheduled_workflows,
                    },
                    "analytics": {"enabled": settings.enable_analytics},
                    "webhooks": {"enabled": settings.enable_webhooks},
                },
                "safety": {
                    "team_workflows_enabled": settings.enable_team_workflows,
                    "team_writes_enabled": settings.enable_team_writes,
                    "scheduled_workflows_enabled": settings.enable_scheduled_workflows,
                    "analytics_enabled": settings.enable_analytics,
                    "webhooks_enabled": settings.enable_webhooks,
                    "safe_telegram_replies_enabled": settings.enable_safe_telegram_replies,
                    "dry_run_team_replies_enabled": settings.enable_dry_run_team_replies,
                    "live_sheet_mutations_available": False,
                },
                "capabilities": {
                    "overview": True,
                    "audit_history": True,
                    "task_and_kpi_browsing": False,
                    "task_and_kpi_editing": False,
                    "task_and_kpi_reason": (
                        "Pending Operations Manager workbook completion and approved "
                        "live worksheet schema/editable-field review."
                    ),
                },
                "audit_store": {
                    "configured": bool(str(settings.audit_db_path).strip()),
                    "database_file_present": audit_path.is_file(),
                },
            }
        )

    @router.get("/team-performance")
    async def team_performance(
        request: Request,
        start_date: date | None = Query(default=None),
        end_date: date | None = Query(default=None),
        team: str | None = Query(default=None, min_length=1, max_length=128),
        person: str | None = Query(default=None, min_length=1, max_length=256),
        status: str | None = Query(default=None, min_length=1, max_length=32, pattern="^(completed|in_progress|open|blocked)$"),
    ) -> JSONResponse:
        """Return read-only team/task/KPI aggregates from the live workbook."""
        session = require_admin(request)
        if session is None:
            return _unauthorized()

        today = datetime.now(UTC).date()
        effective_end = end_date or today
        effective_start = start_date or (effective_end - __import__("datetime").timedelta(days=29))
        if effective_start > effective_end:
            return _json_response(
                {"detail": "start_date must be on or before end_date"}, status_code=422
            )
        settings = request.app.state.settings
        if not settings.google_sheet_id:
            return _json_response(
                {"detail": "Google Sheets is not configured"}, status_code=503
            )
        try:
            sheets = await asyncio.to_thread(GoogleSheetsClient.from_settings, settings)
            people_rows, task_rows, kpi_register_rows, kpi_log_rows = await asyncio.gather(
                sheets.read_records("People Directory"),
                sheets.read_records("Master Task Tracker"),
                sheets.read_records("KPI Register"),
                sheets.read_records("Weekly KPI Log"),
            )
            result = build_team_performance(
                people_rows,
                task_rows,
                kpi_register_rows,
                kpi_log_rows,
                today=today,
                start_date=effective_start,
                end_date=effective_end,
                team_filter=team,
                person_filter=person,
                status_filter=status,
            )
        except (GoogleSheetsError, OSError, RuntimeError, ValueError):
            logger.warning("admin_team_performance_failed reason=source_unavailable")
            return _json_response(
                {"detail": "Team performance data is temporarily unavailable. Check the workbook connection and schema."},
                status_code=503,
            )
        result["source"] = {
            "system": "Google Sheets",
            "status": "read_success",
            "worksheets": [
                "People Directory",
                "Master Task Tracker",
                "KPI Register",
                "Weekly KPI Log",
            ],
        }
        return _json_response(result)

    @router.get("/audit-events")
    async def audit_events(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
        outcome: str | None = Query(default=None, min_length=1, max_length=32),
        worksheet: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> JSONResponse:
        session = require_admin(request)
        if session is None:
            return _unauthorized()

        try:
            result = await SQLiteAuditLog(
                request.app.state.settings.audit_db_path
            ).list_events(limit=limit, offset=offset, outcome=outcome, worksheet=worksheet)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            logger.warning("admin_audit_read_failed reason=store_unavailable")
            return _json_response(
                {"detail": "Audit history is temporarily unavailable"}, status_code=503
            )
        return _json_response(
            {
                "items": result["items"],
                "total": result["total"],
                "limit": limit,
                "offset": offset,
            }
        )

    return router
