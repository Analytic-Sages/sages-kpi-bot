"""Polaris HTTP application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging
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
    if settings.telegram_bot_token:
        telegram_client = TelegramClient(settings.telegram_bot_token)
        telegram_bot = TelegramBot(telegram_client, settings)
        try:
            await telegram_bot.start()
            telegram_task = asyncio.create_task(telegram_bot.run(), name="polaris-telegram-polling")
        except Exception:
            await telegram_client.close()
            raise
    else:
        logger.info("telegram_disabled reason=missing_bot_token")
    try:
        yield
    finally:
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
                "google_sheets": "not_configured_or_not_implemented",
                "analytics": "not_implemented",
            },
        },
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
