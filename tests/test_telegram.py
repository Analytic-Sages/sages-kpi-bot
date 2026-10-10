from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.telegram.bot import TelegramBot
from app.telegram.client import TelegramAPIError, TelegramClient


@pytest.mark.asyncio
async def test_command_menu_includes_rollout_commands():
    client = TelegramClient("test-token")
    client._request = AsyncMock(return_value=True)
    await client.set_commands()
    payload = client._request.await_args.args[1]
    assert {item["command"] for item in payload["commands"]} == {
        "start", "help", "status", "goal", "blocker", "jd", "growth"
    }
    await client.close()


@pytest.mark.asyncio
async def test_start_command_dry_run_does_not_send_message():
    client = AsyncMock()
    settings = Settings(_env_file=None, dry_run=True)
    bot = TelegramBot(client, settings)
    await bot.handle_update({
        "update_id": 1,
        "message": {"chat": {"id": 123}, "from": {"id": 456}, "text": "/start"},
    })
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_command_and_non_message_updates_are_ignored():
    client = AsyncMock()
    bot = TelegramBot(client, Settings(_env_file=None, dry_run=True))
    await bot.handle_update({"update_id": 1, "callback_query": {}})
    await bot.handle_update({
        "update_id": 2,
        "message": {"chat": {"id": 123}, "text": "hello"},
    })
    client.send_message.assert_not_awaited()


def test_telegram_client_rejects_blank_token():
    with pytest.raises(ValueError, match="token"):
        TelegramClient("   ")


@pytest.mark.asyncio
async def test_failed_telegram_response_is_reported():
    client = TelegramClient("test-token")
    client._request = AsyncMock(side_effect=TelegramAPIError("bad response"))
    with pytest.raises(TelegramAPIError):
        await client.get_updates(None)
    await client.close()
