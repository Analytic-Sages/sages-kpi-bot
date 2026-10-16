"""Polaris HTTP application entry point."""
import asyncio
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.admin_auth import create_admin_auth_router
from app.analytics.service import GrowthAnalyticsService
from app.config import get_settings
from app.logging_config import configure_logging
from app.preflight import sheet_findings
from app.preflight_rows import row_integrity_findings
from app.scheduled.workflows import ScheduledWorkflowService, build_scheduler
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError
from app.team.workflows import TeamWorkflowService
from app.telegram.bot import TelegramBot
from app.telegram.client import TelegramClient
from app.webhooks.store import (
    SQLiteWebhookEventStore,
    WebhookConflictError,
    WebhookValidationError,
    parse_event,
    verify_signature,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("polaris")
TELEGRAM_HEALTH_MAX_AGE_SECONDS = 90


async def _supervise_telegram(bot: TelegramBot, stop_event: asyncio.Event) -> None:
    """Restart polling if its coroutine exits unexpectedly; stop cleanly on shutdown."""
    while not stop_event.is_set():
        try:
            await bot.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "telegram_polling_supervisor_caught_exit error_type=%s", type(exc).__name__
            )
        if stop_event.is_set():
            return
        logger.error("telegram_polling_task_exited action=restart delay_seconds=3")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except TimeoutError:
            pass


def _telegram_health(request: Request) -> tuple[bool, dict[str, object]]:
    """Report recent successful Telegram long-poll activity, not just token presence."""
    configured = bool(settings.telegram_bot_token)
    bot = getattr(request.app.state, "telegram_bot", None)
    task = getattr(request.app.state, "telegram_task", None)
    last_success = getattr(bot, "last_poll_success_at", None) if bot else None
    age_seconds = (
        (datetime.now(UTC) - last_success).total_seconds()
        if last_success is not None
        else None
    )
    task_alive = task is not None and not task.done()
    healthy = bool(
        configured
        and bot is not None
        and task_alive
        and age_seconds is not None
        and age_seconds <= TELEGRAM_HEALTH_MAX_AGE_SECONDS
    )
    return healthy, {
        "status": (
            "healthy"
            if healthy
            else "degraded"
            if configured
            else "not_configured"
        ),
        "configured": configured,
        "polling_task_alive": task_alive,
        "last_successful_poll_age_seconds": (
            round(age_seconds, 1) if age_seconds is not None else None
        ),
    }


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info(
        "application_starting app=%s environment=%s dry_run=%s timezone=%s webhooks_enabled=%s",
        settings.app_name,
        settings.app_env,
        settings.dry_run,
        settings.timezone,
        settings.enable_webhooks,
    )
    telegram_client: TelegramClient | None = None
    telegram_bot: TelegramBot | None = None
    telegram_task: asyncio.Task[None] | None = None
    telegram_stop_event = asyncio.Event()
    application.state.telegram_bot = None
    application.state.telegram_task = None
    scheduler = None
    if settings.telegram_bot_token:
        workflows: TeamWorkflowService | None = None
        sheets = None
        if settings.enable_team_workflows or settings.enable_scheduled_workflows:
            sheets = await asyncio.to_thread(GoogleSheetsClient.from_settings, settings)
        if settings.enable_team_workflows and sheets is not None:
            workflows = TeamWorkflowService(
                sheets,
                job_descriptions_worksheet=settings.job_descriptions_worksheet,
                blocker_worksheet=settings.blocker_log_worksheet,
                # Read-only team commands can be staged independently from spreadsheet writes.
                dry_run=settings.dry_run or not settings.enable_team_writes,
            )
            logger.warning(
                "team_workflows_enabled effective_dry_run=%s team_writes_enabled=%s "
                "jd_source_configured=%s blocker_log_configured=%s",
                settings.dry_run or not settings.enable_team_writes,
                settings.enable_team_writes and not settings.dry_run,
                bool(settings.job_descriptions_worksheet),
                bool(settings.blocker_log_worksheet),
            )
        telegram_client = TelegramClient(settings.telegram_bot_token)
        analytics = GrowthAnalyticsService(settings) if settings.enable_analytics else None
        telegram_bot = TelegramBot(
            telegram_client,
            settings,
            workflows=workflows,
            analytics=analytics,
        )
        try:
            await telegram_bot.start()
            application.state.telegram_bot = telegram_bot
            telegram_task = asyncio.create_task(
                _supervise_telegram(telegram_bot, telegram_stop_event),
                name="polaris-telegram-polling-supervisor",
            )
            application.state.telegram_task = telegram_task
            if settings.enable_scheduled_workflows and sheets is not None:
                scheduled = ScheduledWorkflowService(sheets, telegram_client, settings)
                scheduler = build_scheduler(scheduled)
                scheduler.start()
                logger.warning(
                    "scheduled_workflows_enabled timezone=UTC dry_run=%s core_chat_configured=%s",
                    settings.dry_run,
                    bool(settings.telegram_core_chat_id),
                )
        except Exception:
            await telegram_client.close()
            raise
    else:
        logger.info("telegram_disabled reason=missing_bot_token")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        telegram_stop_event.set()
        if telegram_bot is not None:
            await telegram_bot.stop()
        if telegram_task is not None:
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                pass
        if telegram_client is not None:
            await telegram_client.close()
        logger.info("application_stopping app=%s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="Growth and accountability operations service for Analytic Sages.",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.settings = settings
app.include_router(create_admin_auth_router(settings))


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "running",
        "environment": settings.app_env,
        "docs": "/docs",
    }


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/telegram", tags=["health"])
async def telegram_health(request: Request) -> JSONResponse:
    """Health check for Telegram polling; use separately from HTTP liveness."""
    healthy, details = _telegram_health(request)
    return JSONResponse(status_code=200 if healthy else 503, content=details)


@app.get("/health/ready", tags=["health"])
async def readiness(request: Request) -> JSONResponse:
    telegram_healthy, telegram_details = _telegram_health(request)
    webhook_state = (
        "enabled"
        if settings.enable_webhooks and settings.webhook_secret
        else "misconfigured"
        if settings.enable_webhooks
        else "not_enabled"
    )
    return JSONResponse(
        status_code=503 if settings.telegram_bot_token and not telegram_healthy else 200,
        content={
            "status": (
                "ready"
                if not settings.telegram_bot_token or telegram_healthy
                else "degraded"
            ),
            "integrations": {
                "telegram": telegram_details["status"],
                "telegram_polling_task_alive": telegram_details["polling_task_alive"],
                "telegram_last_successful_poll_age_seconds": (
                    telegram_details["last_successful_poll_age_seconds"]
                ),
                "google_sheets": (
                    "enabled_for_team_or_scheduled_workflows"
                    if settings.enable_team_workflows or settings.enable_scheduled_workflows
                    else "not_enabled"
                ),
                "scheduled_workflows": (
                    "enabled_dry_run"
                    if settings.enable_scheduled_workflows and settings.dry_run
                    else "enabled"
                    if settings.enable_scheduled_workflows
                    else "not_enabled"
                ),
                "analytics": (
                    "enabled_dry_run"
                    if settings.enable_analytics and settings.dry_run
                    else "enabled"
                    if settings.enable_analytics
                    else "not_enabled"
                ),
                "webhooks": webhook_state,
            },
        },
    )


@app.get("/admin/preflight", tags=["admin"])
async def read_only_deployment_preflight(request: Request) -> JSONResponse:
    """Verify Google Sheets access, headers, and aggregate row integrity without writes.

    The endpoint is hidden unless PREFLIGHT_TOKEN is configured and requires a high-entropy
    bearer token. It reports aggregate counts and check outcomes only, never record values.
    """
    expected_token = settings.preflight_token
    if not expected_token:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if len(expected_token) < 32:
        logger.error("deployment_preflight_unavailable reason=token_too_short")
        return JSONResponse(
            status_code=503,
            content={"detail": "Deployment preflight is misconfigured"},
        )

    authorization = request.headers.get("Authorization", "")
    scheme, _, supplied_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not supplied_token.strip():
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    if not secrets.compare_digest(expected_token, supplied_token.strip()):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    if not settings.google_sheet_id:
        return JSONResponse(
            status_code=503,
            content={
                "status": "blocked",
                "checks": [
                    {
                        "severity": "BLOCKER",
                        "component": "Google Sheets",
                        "message": "GOOGLE_SHEET_ID is not configured.",
                    }
                ],
            },
        )

    try:
        sheets = await asyncio.to_thread(GoogleSheetsClient.from_settings, settings)
        findings = await sheet_findings(sheets)
        findings.extend(await row_integrity_findings(sheets))
    except GoogleSheetsError:
        logger.warning("deployment_preflight_failed reason=google_sheets_connection")
        return JSONResponse(
            status_code=503,
            content={
                "status": "blocked",
                "checks": [
                    {
                        "severity": "BLOCKER",
                        "component": "Google Sheets",
                        "message": (
                            "Could not authenticate or open the spreadsheet. Check OAuth "
                            "configuration and spreadsheet sharing."
                        ),
                    }
                ],
            },
        )

    checks = [
        {
            "severity": finding.severity,
            "component": finding.component,
            "message": finding.message,
        }
        for finding in findings
    ]
    blocked = any(finding.severity == "BLOCKER" for finding in findings)
    return JSONResponse(
        status_code=503 if blocked else 200,
        content={"status": "blocked" if blocked else "passed", "checks": checks},
    )


@app.post("/webhooks/events", tags=["webhooks"])
async def receive_webhook(request: Request) -> JSONResponse:
    """Authenticate and persist event metadata; do not execute event payloads."""
    if not settings.enable_webhooks:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if not settings.webhook_secret:
        logger.error("webhook_rejected reason=missing_secret")
        return JSONResponse(
            status_code=503,
            content={"detail": "Webhook receiver is misconfigured"},
        )

    body = await request.body()
    if len(body) > settings.webhook_max_body_bytes:
        return JSONResponse(status_code=413, content={"detail": "Webhook body is too large"})

    timestamp = request.headers.get("X-Polaris-Timestamp", "")
    event_id = request.headers.get("X-Polaris-Event-Id", "")
    signature = request.headers.get("X-Polaris-Signature", "")
    try:
        verify_signature(
            secret=settings.webhook_secret,
            timestamp=timestamp,
            event_id=event_id,
            body=body,
            signature=signature,
            max_skew_seconds=settings.webhook_max_skew_seconds,
        )
    except WebhookValidationError:
        logger.warning("webhook_rejected reason=authentication")
        return JSONResponse(status_code=401, content={"detail": "Invalid webhook authentication"})

    try:
        event_id, event_type = parse_event(body, event_id)
    except WebhookValidationError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    try:
        store = await asyncio.to_thread(SQLiteWebhookEventStore, settings.webhook_db_path)
        first_delivery = await store.record(event_id, event_type, body)
    except WebhookConflictError:
        logger.warning("webhook_rejected reason=event_id_conflict event_id=%s", event_id)
        return JSONResponse(
            status_code=409,
            content={"detail": "Event ID conflicts with prior delivery"},
        )
    except (OSError, RuntimeError, sqlite3.Error):
        logger.exception("webhook_store_failed event_id=%s", event_id)
        return JSONResponse(status_code=503, content={"detail": "Webhook event store unavailable"})

    logger.info(
        "webhook_event_received event_id=%s event_type=%s duplicate=%s",
        event_id,
        event_type,
        not first_delivery,
    )
    return JSONResponse(
        status_code=202 if first_delivery else 200,
        content={"accepted": True, "duplicate": not first_delivery, "event_id": event_id},
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
