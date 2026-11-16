"""Authenticated admin APIs backed exclusively by PostgreSQL."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.admin_auth import require_admin
from app.operations_admin_adapter import list_audit_events, team_performance
from app.operations_db import database_engine

logger = logging.getLogger("sageops.admin_api")


def _response(content: dict[str, Any], status_code: int = 200) -> JSONResponse:
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
            return _response({"detail": "Authentication required"}, 401)
        settings = request.app.state.settings
        engine = database_engine()
        connected = False
        if engine is not None:
            try:
                from sqlalchemy import text

                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                connected = True
            except Exception:
                logger.exception("admin_database_health_failed")
        return _response({
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "admin": {"email": session["email"], "access": "administrator"},
            "service": {
                "name": settings.app_name,
                "environment": settings.app_env,
                "dry_run": settings.dry_run,
            },
            "integrations": {
                "operations_database": {
                    "configured": engine is not None,
                    "active": connected,
                    "status": "connected" if connected else "unavailable",
                    "note": "PostgreSQL is the system of record for operational data.",
                },
                "telegram": {
                    "configured": bool(settings.telegram_bot_token),
                    "status": "configured_not_checked" if settings.telegram_bot_token else "not_configured",
                },
                "scheduled_workflows": {"enabled": settings.operations_notifications_enabled},
                "analytics": {"enabled": True},
                "webhooks": {"enabled": settings.enable_webhooks},
            },
            "safety": {
                "team_workflows_enabled": False,
                "team_writes_enabled": settings.operations_db_writes_enabled,
                "database_writes_enabled": settings.operations_db_writes_enabled,
                "scheduled_workflows_enabled": settings.operations_notifications_enabled,
                "analytics_enabled": True,
                "webhooks_enabled": settings.enable_webhooks,
                "safe_telegram_replies_enabled": False,
                "dry_run_team_replies_enabled": False,
                "live_sheet_mutations_available": False,
            },
            "capabilities": {
                "overview": True,
                "audit_history": True,
                "task_and_kpi_browsing": True,
                "task_and_kpi_editing": settings.operations_db_writes_enabled,
                "task_and_kpi_reason": "Operational records are served from PostgreSQL.",
            },
            "audit_store": {"configured": connected, "database_file_present": False},
        })

    @router.get("/team-performance")
    async def admin_team_performance(
        request: Request,
        start_date: date | None = None,
        end_date: date | None = None,
        team: str | None = Query(default=None, min_length=1, max_length=128),
        person: str | None = Query(default=None, min_length=1, max_length=256),
        status: str | None = Query(default=None, min_length=1, max_length=32,
                                   pattern="^(completed|in_progress|open|blocked)$"),
    ) -> JSONResponse:
        if require_admin(request) is None:
            return _response({"detail": "Authentication required"}, 401)
        today = datetime.now(UTC).date()
        end = end_date or today
        start = start_date or end - timedelta(days=29)
        if start > end:
            return _response({"detail": "start_date must be on or before end_date"}, 422)
        try:
            result = team_performance(
                today=today,
                start_date=start,
                end_date=end,
                team_filter=team,
                person_filter=person,
                status_filter=status,
            )
            return _response(result)
        except Exception:
            logger.exception("admin_team_performance_failed")
            return _response({"detail": "Team performance data is temporarily unavailable"}, 503)

    @router.get("/audit-events")
    async def audit_events(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
        outcome: str | None = Query(default=None, min_length=1, max_length=32),
        worksheet: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> JSONResponse:
        if require_admin(request) is None:
            return _response({"detail": "Authentication required"}, 401)
        try:
            result = list_audit_events(
                limit=limit, offset=offset, outcome=outcome, worksheet=worksheet
            )
            return _response({**result, "limit": limit, "offset": offset})
        except Exception:
            logger.exception("admin_audit_read_failed")
            return _response({"detail": "Audit history is temporarily unavailable"}, 503)

    return router
