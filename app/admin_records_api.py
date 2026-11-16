"""Authenticated admin record reads backed exclusively by PostgreSQL."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app import operations_admin_adapter
from app.admin_auth import require_admin

logger = logging.getLogger("sageops.admin_records")


def _response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def create_admin_records_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-records"])

    def authorize(request: Request) -> JSONResponse | None:
        if require_admin(request) is None:
            return _response({"detail": "Authentication required"}, 401)
        return None

    @router.get("/tasks")
    def list_tasks(
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
        denied = authorize(request)
        if denied:
            return denied
        try:
            result = operations_admin_adapter.list_tasks(
                limit=limit,
                offset=offset,
                status=status,
                team=team,
                person=person,
                query=query,
                overdue_only=overdue_only,
            )
            result["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            return _response(result)
        except Exception:
            logger.exception("admin_task_list_failed")
            return _response({"detail": "Task data is temporarily unavailable"}, 503)

    @router.get("/tasks/{task_id}")
    def get_task(request: Request, task_id: str) -> JSONResponse:
        denied = authorize(request)
        if denied:
            return denied
        if not task_id.strip() or len(task_id) > 200:
            return _response({"detail": "Invalid task ID"}, 422)
        try:
            result = operations_admin_adapter.get_task(task_id.strip())
            return _response(result) if result else _response({"detail": "Task not found"}, 404)
        except Exception:
            logger.exception("admin_task_detail_failed")
            return _response({"detail": "Task data is temporarily unavailable"}, 503)

    @router.get("/kpis")
    def list_kpis(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
        team: str | None = Query(default=None, min_length=1, max_length=128),
        person: str | None = Query(default=None, min_length=1, max_length=256),
        active_only: bool = True,
    ) -> JSONResponse:
        denied = authorize(request)
        if denied:
            return denied
        try:
            result = operations_admin_adapter.list_kpis(
                limit=limit,
                offset=offset,
                team=team,
                person=person,
                active_only=active_only,
            )
            result["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            return _response(result)
        except Exception:
            logger.exception("admin_kpi_list_failed")
            return _response({"detail": "KPI data is temporarily unavailable"}, 503)

    @router.get("/kpis/{kpi_id}")
    def get_kpi(request: Request, kpi_id: str) -> JSONResponse:
        denied = authorize(request)
        if denied:
            return denied
        if not kpi_id.strip() or len(kpi_id) > 200:
            return _response({"detail": "Invalid KPI ID"}, 422)
        try:
            result = operations_admin_adapter.get_kpi(kpi_id.strip())
            return _response(result) if result else _response({"detail": "KPI not found"}, 404)
        except Exception:
            logger.exception("admin_kpi_detail_failed")
            return _response({"detail": "KPI data is temporarily unavailable"}, 503)

    return router
