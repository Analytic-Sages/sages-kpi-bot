"""SageOps PostgreSQL-backed HTTP application."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.admin_api import create_admin_api_router
from app.admin_auth import create_admin_auth_router
from app.admin_records_api import create_admin_records_router
from app.config import get_settings
from app.logging_config import configure_logging
from app.operations_api import create_operations_router
from app.operations_directory_api import create_operations_directory_router
from app.operations_db import SessionLocal, database_engine
from app.operations_management_api import create_operations_management_router
from app.operations_models import WebhookEvent
from app.operations_telegram_api import create_operations_telegram_router
from app.operations_write_api import create_operations_write_router
from app.webhooks.store import WebhookConflictError, WebhookValidationError, parse_event, verify_signature

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("sageops")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Keep web instances stateless; workers and Telegram webhook processing run separately."""
    logger.info("application_starting app=%s environment=%s", settings.app_name, settings.app_env)
    application.state.telegram_bot = None
    application.state.telegram_task = None
    yield
    logger.info("application_stopping app=%s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="Staff operations platform for Analytic Sages, backed by PostgreSQL.",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
)
app.state.settings = settings
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env, traces_sample_rate=0.05,
                    send_default_pii=False)
app.include_router(create_admin_auth_router(settings))
app.include_router(create_admin_api_router())
app.include_router(create_admin_records_router())
app.include_router(create_operations_router())
app.include_router(create_operations_write_router())
app.include_router(create_operations_telegram_router())
app.include_router(create_operations_management_router())
app.include_router(create_operations_directory_router())
app.mount(
    "/control",
    StaticFiles(directory=Path(__file__).resolve().parent / "static" / "admin", html=True),
    name="admin-control-centre",
)


@app.middleware("http")
async def admin_control_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/control" or request.url.path.startswith("/control/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "running", "environment": settings.app_env, "docs": "/docs"}


@app.get("/privacy", tags=["legal"], include_in_schema=False)
async def privacy_policy() -> FileResponse:
    policy_path = Path(__file__).resolve().parent / "static" / "privacy.html"
    return FileResponse(policy_path, media_type="text/html", headers={
        "Cache-Control": "public, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    })


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


def _database_check() -> tuple[bool, str | None]:
    engine = database_engine()
    if engine is None:
        return False, None
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            connection.execute(text("SELECT 1 FROM settings LIMIT 0"))
        return True, str(revision)
    except Exception:
        logger.exception("database_readiness_check_failed")
        return False, None


def _telegram_status() -> dict[str, Any]:
    configured = bool(settings.telegram_bot_token and settings.telegram_webhook_secret)
    return {"status": "webhook_configured" if configured else "not_configured", "configured": configured, "mode": "webhook"}


@app.get("/health/telegram", tags=["health"])
async def telegram_health() -> JSONResponse:
    details = _telegram_status()
    return JSONResponse(status_code=200 if details["configured"] else 503, content=details)


@app.get("/health/ready", tags=["health"])
async def readiness() -> JSONResponse:
    database_ok, revision = _database_check()
    telegram = _telegram_status()
    ready = database_ok
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "integrations": {
                "postgresql": "connected" if database_ok else "unavailable",
                "alembic_revision": revision,
                "telegram": telegram["status"],
                "scheduler": "external_worker",
            },
        },
    )


@app.get("/admin/preflight", tags=["admin"])
async def database_preflight(request: Request) -> JSONResponse:
    """Protected PostgreSQL-only preflight; no spreadsheet or workbook access."""
    expected_token = settings.preflight_token
    if not expected_token:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if len(expected_token) < 32:
        return JSONResponse(status_code=503, content={"detail": "Preflight is misconfigured"})
    scheme, _, supplied_token = request.headers.get("Authorization", "").partition(" ")
    if scheme.casefold() != "bearer" or not secrets.compare_digest(expected_token, supplied_token.strip()):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    healthy, revision = _database_check()
    checks = [{"severity": "INFO" if healthy else "BLOCKER", "component": "PostgreSQL", "message": f"Connection and migration revision {revision} verified." if healthy else "Database connection or migration history is unavailable."}]
    return JSONResponse(status_code=200 if healthy else 503, content={"status": "passed" if healthy else "blocked", "checks": checks})


@app.post("/webhooks/events", tags=["webhooks"])
async def receive_webhook(request: Request) -> JSONResponse:
    """Authenticate and persist event metadata in PostgreSQL without storing the payload."""
    if not settings.enable_webhooks:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if not settings.webhook_secret or SessionLocal is None:
        return JSONResponse(status_code=503, content={"detail": "Webhook receiver is misconfigured"})
    body = await request.body()
    if len(body) > settings.webhook_max_body_bytes:
        return JSONResponse(status_code=413, content={"detail": "Webhook body is too large"})
    timestamp = request.headers.get("X-Polaris-Timestamp", "")
    event_id = request.headers.get("X-Polaris-Event-Id", "")
    signature = request.headers.get("X-Polaris-Signature", "")
    try:
        verify_signature(secret=settings.webhook_secret, timestamp=timestamp, event_id=event_id,
                         body=body, signature=signature, max_skew_seconds=settings.webhook_max_skew_seconds)
        event_id, event_type = parse_event(body, event_id)
    except WebhookValidationError as exc:
        status = 401 if "signature" in str(exc).casefold() or "timestamp" in str(exc).casefold() else 400
        return JSONResponse(status_code=status, content={"detail": "Invalid webhook request"})
    session = SessionLocal()
    try:
        inserted = session.execute(
            insert(WebhookEvent)
            .values(event_id=event_id, event_type=event_type, body_sha256=hashlib.sha256(body).hexdigest())
            .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
            .returning(WebhookEvent.event_id)
        ).scalar_one_or_none()
        if inserted:
            session.commit()
            return JSONResponse(status_code=202, content={"accepted": True, "duplicate": False, "event_id": event_id})
        existing = session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
        if existing and existing.event_type == event_type and hmac.compare_digest(existing.body_sha256, hashlib.sha256(body).hexdigest()):
            session.rollback()
            return JSONResponse(status_code=200, content={"accepted": True, "duplicate": True, "event_id": event_id})
        session.rollback()
        raise WebhookConflictError("Event ID was already used for a different event body")
    except WebhookConflictError:
        return JSONResponse(status_code=409, content={"detail": "Event ID conflicts with prior delivery"})
    except Exception:
        session.rollback()
        logger.exception("webhook_store_failed")
        return JSONResponse(status_code=503, content={"detail": "Webhook event store unavailable"})
    finally:
        session.close()


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
