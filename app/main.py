"""Polaris HTTP application entry point."""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging

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
    # Integrations and scheduler are intentionally wired in later priorities.
    yield
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
                "telegram": "not_configured_or_not_implemented",
                "google_sheets": "not_configured_or_not_implemented",
                "analytics": "not_implemented",
            },
        },
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
