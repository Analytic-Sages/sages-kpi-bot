"""Telegram update polling, command dispatch, and safe message routing."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.analytics.service import GrowthAnalyticsService
from app.config import Settings
from app.team.workflows import TeamWorkflowService
from app.telegram.client import TelegramAPIError, TelegramClient

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Polaris helps the Analytic Sages team stay aligned on goals and accountability.\n\n"
    "Available commands:\n"
    "/status — view your accountability status\n"
    "/goal <KPI name> <target> — update your assigned KPI target\n"
    "/blocker <Task ID> <description> — report a blocker for a tracked task\n"
    "/jd [name or @handle] — view an authoritative role description\n"
    "/whoami — show your personal Telegram ID for account mapping\n"
    "/growth — view private website and YouTube growth metrics (when enabled)"
)


class TelegramBot:
    """Long-polling bot runner. Does not send messages in DRY_RUN mode."""

    def __init__(
        self,
        client: TelegramClient,
        settings: Settings,
        workflows: TeamWorkflowService | None = None,
        analytics: GrowthAnalyticsService | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.workflows = workflows
        self.analytics = analytics
        self._stop = asyncio.Event()
        self._offset: int | None = None
        self.last_poll_success_at: datetime | None = None

    async def start(self) -> None:
        await self.client.set_commands()
        self._stop.clear()
        logger.info("telegram_polling_started dry_run=%s", self.settings.dry_run)

    async def stop(self) -> None:
        self._stop.set()
        logger.info("telegram_polling_stopped")

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                updates = await self.client.get_updates(self._offset)
                self.last_poll_success_at = datetime.now(UTC)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = max(self._offset or 0, update_id + 1)
                    try:
                        await self.handle_update(update)
                    except Exception as exc:
                        logger.error(
                            "telegram_update_failed update_id=%s error_type=%s",
                            update_id,
                            type(exc).__name__,
                        )
            except asyncio.CancelledError:
                raise
            except TelegramAPIError as exc:
                logger.error(
                    "telegram_poll_failed error_type=%s; retrying after delay",
                    type(exc).__name__,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=3.0)
                except TimeoutError:
                    pass

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return
        chat_id = chat.get("id")
        if not isinstance(chat_id, int | str):
            return

        parts = text.strip().split(maxsplit=1)
        command = parts[0].split("@", maxsplit=1)[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        if command not in {
            "/start", "/help", "/whoami", "/status", "/goal", "/blocker", "/jd", "/growth"
        }:
            return

        sender_id = sender.get("id") if isinstance(sender, dict) else None
        chat_type = chat.get("type")
        # Telegram IDs are personal identifiers; do not persist them in application logs.
        logger.info(
            "telegram_command_received command=%s chat_type=%s",
            command,
            chat_type,
        )
        if command in {"/start", "/help"}:
            reply = HELP_TEXT
        elif command == "/whoami":
            if chat_type != "private" or not isinstance(sender_id, int | str):
                reply = (
                    "For privacy, /whoami works only in a private chat with "
                    "@AnalyticSagesPolarisBot. Open the bot and send /whoami there."
                )
            elif str(chat_id) != str(sender_id):
                reply = "I couldn't verify this private chat's identity."
            else:
                reply = (
                    f"Your Telegram user ID is {sender_id}. Share it with your team "
                    "administrator through an approved private channel so it can be "
                    "mapped to your Person ID in the People Directory."
                )
        elif command == "/growth":
            reply = await self._growth_reply(chat_id)
        elif command in {"/status", "/goal", "/blocker", "/jd"}:
            # Team identity is a person's private Telegram chat ID, not a shared
            # group chat ID. Fail closed for group/supergroup commands to prevent
            # personal accountability data or command arguments being processed there.
            if chat_type != "private":
                reply = (
                    "For privacy, team commands must be used in a private chat with "
                    "@AnalyticSagesPolarisBot. Open the bot and send /start, then retry "
                    "your command here. Team commands are not processed in groups."
                )
            elif not isinstance(sender_id, int | str) or str(sender_id) != str(chat_id):
                reply = (
                    "I couldn't verify this private chat's identity. No team action was taken."
                )
            elif self.workflows is not None:
                reply = await self.workflows.handle(command, args, sender_id)
            else:
                reply = (
                    "Team workflows are not enabled yet. An administrator must verify the live "
                    "Google Sheet schema, Telegram identity mapping, and role-description source "
                    "before Polaris can read or change team data."
                )
        else:
            return
        await self._reply(
            chat_id,
            reply,
            safe=command in {"/start", "/help", "/whoami"},
            staging_team=(
                command in {"/status", "/goal", "/blocker", "/jd"}
                and chat_type == "private"
                and isinstance(sender_id, int | str)
                and str(chat_id) == str(sender_id)
            ),
        )

    async def _growth_reply(self, chat_id: int | str) -> str:
        if not self.settings.enable_analytics or self.analytics is None:
            return "Growth analytics are disabled. An administrator must configure and enable them."
        core_chat_id = self.settings.telegram_core_chat_id
        if not core_chat_id:
            return (
                "Growth analytics are unavailable because the private core chat "
                "is not configured."
            )
        if str(chat_id) != str(core_chat_id):
            return (
                "For privacy, growth analytics are available only in the configured "
                "private core chat."
            )
        try:
            return await self.analytics.report()
        except Exception as exc:
            logger.error("growth_analytics_command_failed error_type=%s", type(exc).__name__)
            return "Growth analytics could not be loaded. Check provider credentials and logs."

    async def _reply(
        self,
        chat_id: int | str,
        text: str,
        *,
        safe: bool = False,
        staging_team: bool = False,
    ) -> None:
        allowed_dry_run_reply = (
            self.settings.enable_safe_telegram_replies and safe
        ) or (
            self.settings.enable_dry_run_team_replies and staging_team
        )
        if self.settings.dry_run and not allowed_dry_run_reply:
            logger.info("telegram_dry_run_message_suppressed")
            return
        try:
            await self.client.send_message(chat_id, text)
        except TelegramAPIError as exc:
            logger.error(
                "telegram_reply_failed error_type=%s",
                type(exc).__name__,
            )
