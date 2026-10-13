"""Scheduled KPI and team accountability messages."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError
from app.telegram.client import TelegramAPIError, TelegramClient

logger = logging.getLogger(__name__)

SCHEDULE_TIMEZONE = "UTC"


def _kpi_label(row: dict[str, str]) -> str:
    """Prefer the concise formula-derived label when the tracker provides it."""
    return row.get("KPI Label", "").strip() or row.get("KPI", "").strip() or "Unnamed KPI"


class ScheduledWorkflowService:
    """Compose scheduled messages and send only to the configured private team chat."""

    def __init__(
        self,
        sheets: GoogleSheetsClient,
        telegram: TelegramClient,
        settings: Settings,
    ) -> None:
        self.sheets = sheets
        self.telegram = telegram
        self.settings = settings

    async def monday_kpi_broadcast(self) -> None:
        """Send a compact KPI snapshot to the private core team chat."""
        try:
            kpis = await self.sheets.read_records("KPI Register")
            logs = await self.sheets.read_records("Weekly KPI Log")
            required_kpis = {"KPI ID", "KPI", "Owner Person ID", "Target", "Active?"}
            required_logs = {"Week Start", "KPI ID", "Actual", "Status"}
            if not kpis or not required_kpis.issubset(kpis[0]):
                raise ValueError("KPI Register headers do not match the scheduled workflow schema")
            if logs and not required_logs.issubset(logs[0]):
                raise ValueError(
                    "Weekly KPI Log headers do not match the scheduled workflow schema"
                )

            active = [
                row for row in kpis
                if row.get("Active?", "").strip().casefold() in {"true", "yes", "1", "active"}
            ]
            latest_by_kpi: dict[str, dict[str, str]] = {}
            for row in logs:
                kpi_id = row.get("KPI ID", "").strip()
                if not kpi_id:
                    continue
                previous = latest_by_kpi.get(kpi_id)
                if previous is None or row.get("Week Start", "") > previous.get("Week Start", ""):
                    latest_by_kpi[kpi_id] = row

            lines = [
                "📊 Analytic Sages — Weekly KPI Snapshot",
                f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
                "",
            ]
            if not active:
                lines.append("No active KPIs were found in the KPI Register.")
            else:
                for row in active[:30]:
                    latest = latest_by_kpi.get(row.get("KPI ID", "").strip())
                    label = _kpi_label(row)
                    target = row.get("Target", "—")
                    if latest is None:
                        lines.append(f"• {label}: no weekly actual recorded (target {target})")
                    else:
                        lines.append(
                            f"• {label}: {latest.get('Actual', '—')} / {target} "
                            f"({latest.get('Status', 'status unavailable')}; "
                            f"week {latest.get('Week Start', 'unknown')})"
                        )
                if len(active) > 30:
                    lines.append(f"Showing 30 of {len(active)} active KPIs.")
            lines.extend(["", "Please review your numbers and flag any risks or blockers."])
            await self._send_private("\n".join(lines), "monday_kpi_broadcast")
        except (GoogleSheetsError, ValueError, KeyError) as exc:
            logger.warning("scheduled_kpi_broadcast_skipped reason=%s", exc)

    async def focus_prompt(self) -> None:
        """Prompt the team to commit to one measurable priority."""
        today = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%A, %d %B")
        message = (
            f"🎯 Team focus — {today}\n\n"
            "1. What is your single most important measurable outcome today?\n"
            "2. What will you ship or complete, and by when?\n"
            "3. Is anything blocking you? Use /blocker <description> "
            "if the workflow is enabled.\n\n"
            "Keep the commitment specific and update the tracker as work progresses."
        )
        await self._send_private(message, "daily_focus_prompt")

    async def friday_checkout(self) -> None:
        """Prompt a concise end-of-week review."""
        message = (
            "🧭 Friday team checkout\n\n"
            "Reply with a short review:\n"
            "• Wins: what did you complete this week?\n"
            "• KPI: what moved, and what missed target?\n"
            "• Blockers: what needs help or a decision?\n"
            "• Next week: what is your highest-priority outcome?\n\n"
            "Please make sure the Master Task Tracker and Weekly KPI Log reflect the latest state."
        )
        await self._send_private(message, "friday_checkout")

    async def _send_private(self, message: str, workflow_name: str) -> None:
        chat_id = self.settings.telegram_core_chat_id
        if not chat_id:
            logger.warning(
                "scheduled_message_skipped workflow=%s reason=missing_core_chat_id",
                workflow_name,
            )
            return
        if self.settings.dry_run:
            logger.info(
                "scheduled_message_dry_run workflow=%s chat_id=%s preview=%r",
                workflow_name,
                chat_id,
                message[:180],
            )
            return
        try:
            await self.telegram.send_message(chat_id, message)
        except TelegramAPIError:
            logger.exception("scheduled_message_failed workflow=%s", workflow_name)
            return
        logger.info("scheduled_message_sent workflow=%s", workflow_name)


def build_scheduler(
    service: ScheduledWorkflowService,
    *,
    timezone: str = SCHEDULE_TIMEZONE,
) -> AsyncIOScheduler:
    """Build the scheduler with the agreed UTC delivery times."""
    zone = ZoneInfo(timezone)
    scheduler = AsyncIOScheduler(timezone=zone)
    scheduler.add_job(
        service.monday_kpi_broadcast,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=zone),
        id="monday-kpi-broadcast",
        name="Monday KPI broadcast",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        service.focus_prompt,
        CronTrigger(day_of_week="tue-fri", hour=9, minute=30, timezone=zone),
        id="weekday-focus-prompt",
        name="Tuesday–Friday focus prompt",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        service.friday_checkout,
        CronTrigger(day_of_week="fri", hour=15, minute=0, timezone=zone),
        id="friday-checkout",
        name="Friday checkout",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler
