"""Telegram update polling, command dispatch, and safe message routing."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings
from app.telegram.client import TelegramAPIError, TelegramClient

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Polaris helps the Analytic Sages team stay aligned on goals and accountability.\\n\\n"
    "Available commands:\\n"
    "/status — view your accountability status (coming in a later rollout)\\n"
    "/goal — record a KPI goal (coming in a later rollout)\\n"
    "/blocker — report a blocker (coming in a later rollout)\\n"
    "/jd — check role alignment (coming in a later rollout)"
)


class TelegramBot:
    """Long-polling bot runner. Does not send messages in DRY_RUN mode."""

    def __init__(self, client: TelegramClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._stop = asyncio.Event()
        self._offset: int | None = None

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
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = max(self._offset or 0, update_id + 1)
                    try:
                        await self.handle_update(update)
                    except Exception:
                        logger.exception("telegram_update_failed update_id=%s", update_id)
            except asyncio.CancelledError:
                raise
            except TelegramAPIError:
                logger.exception("telegram_poll_failed; retrying after delay")
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
        if not isinstance(chat_id, (int, str)):
            return

        command = text.strip().split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
        if command not in {"/start", "/help", "/status", "/goal", "/blocker", "/jd"}:
            return

        sender_id = sender.get("id") if isinstance(sender, dict) else None
        logger.info("telegram_command_received command=%s chat_id=%s sender_id=%s", command, chat_id, sender_id)
        if command in {"/start", "/help"}:
            reply = HELP_TEXT
        else:
            reply = "This command is registered but is not enabled yet. Polaris is being rolled out in stages."
        await self._reply(chat_id, reply)

    async def _reply(self, chat_id: int | str, text: str) -> None:
        if self.settings.dry_run:
            logger.info("telegram_dry_run_message chat_id=%s text=%r", chat_id, text[:160])
            return
        try:
            await self.client.send_message(chat_id, text)
        except TelegramAPIError:
            logger.exception("telegram_reply_failed chat_id=%s", chat_id)
