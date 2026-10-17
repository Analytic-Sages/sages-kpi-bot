"""Authenticated, read-only administration API for Polaris.

Operational write endpoints intentionally do not live here. Task/KPI data endpoints remain
unavailable until the live workbook schema and editable-field policy have been approved.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.admin_auth import require_admin
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
        except (OSError, RuntimeError, sqlite3.Error):
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
