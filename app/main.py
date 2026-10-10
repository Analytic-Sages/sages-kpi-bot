"""Polaris HTTP application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.analytics.service import GrowthAnalyticsService
from app.config import get_settings
from app.logging_config import configure_logging
from app.scheduled.workflows import ScheduledWorkflowService, build_scheduler
from app.sheets.client import GoogleSheetsClient
from app.team.workflows import TeamWorkflowService
from app.telegram.bot import TelegramBot
from app.telegram.client import TelegramClient

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("polaris")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "application_starting app=%s environment=%s dry_run=%s timezone=%s",
        settings.app_name,
        settings.app_env,
        settings.dry_run,
        settings.timezone,
    )
    telegram_client: TelegramClient | None = None
    telegram_bot: TelegramBot | None = None
    telegram_task: asyncio.Task[None] | None = None
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
                dry_run=settings.dry_run,
            )
            logger.warning(
                "team_workflows_enabled dry_run=%s jd_source_configured=%s "
                "blocker_log_configured=%s",
                settings.dry_run,
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
            telegram_task = asyncio.create_task(telegram_bot.run(), name="polaris-telegram-polling")
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


@app.get("/health/ready", tags=["health"])
async def readiness() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "integrations": {
                "telegram": "configured" if settings.telegram_bot_token else "not_configured",
                "google_sheets": (
                    "enabled_for_team_or_scheduled_workflows"
                    if settings.enable_team_workflows or settings.enable_scheduled_workflows
                    else "not_enabled"
                ),
                "scheduled_workflows": (
                    "enabled_dry_run" if settings.enable_scheduled_workflows and settings.dry_run
                    else "enabled" if settings.enable_scheduled_workflows
                    else "not_enabled"
                ),
                "analytics": (
                    "enabled_dry_run" if settings.enable_analytics and settings.dry_run
                    else "enabled" if settings.enable_analytics
                    else "not_enabled"
                ),
            },
        },
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
