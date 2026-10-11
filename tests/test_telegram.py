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
        "start", "help", "whoami", "status", "goal", "blocker", "jd", "growth"
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
async def test_private_team_command_maps_using_sender_id_not_group_chat_id():
    client = AsyncMock()
    workflows = AsyncMock()
    workflows.handle.return_value = "private status"
    settings = Settings(_env_file=None, dry_run=False)
    bot = TelegramBot(client, settings, workflows=workflows)

    await bot.handle_update({
        "update_id": 10,
        "message": {
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 456},
            "text": "/status",
        },
    })

    workflows.handle.assert_awaited_once_with("/status", "", 456)
    client.send_message.assert_awaited_once_with(456, "private status")


@pytest.mark.asyncio
async def test_group_team_command_is_rejected_without_running_workflow():
    client = AsyncMock()
    workflows = AsyncMock()
    settings = Settings(_env_file=None, dry_run=False)
    bot = TelegramBot(client, settings, workflows=workflows)

    await bot.handle_update({
        "update_id": 11,
        "message": {
            "chat": {"id": -500, "type": "supergroup"},
            "from": {"id": 456},
            "text": "/status",
        },
    })

    workflows.handle.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert "private chat" in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_private_team_command_rejects_mismatched_sender_and_chat():
    client = AsyncMock()
    workflows = AsyncMock()
    bot = TelegramBot(
        client,
        Settings(_env_file=None, dry_run=False),
        workflows=workflows,
    )

    await bot.handle_update({
        "update_id": 12,
        "message": {
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "/goal Qualified Leads 20",
        },
    })

    workflows.handle.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert "couldn't verify" in client.send_message.await_args.args[1]


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


@pytest.mark.asyncio
async def test_whoami_returns_personal_id_only_in_private_chat():
    client = AsyncMock()
    bot = TelegramBot(client, Settings(_env_file=None, dry_run=False))

    await bot.handle_update({
        "update_id": 20,
        "message": {
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 456},
            "text": "/whoami",
        },
    })

    client.send_message.assert_awaited_once()
    assert "456" in client.send_message.await_args.args[1]
    assert "private channel" in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_whoami_does_not_disclose_id_in_group_chat():
    client = AsyncMock()
    bot = TelegramBot(client, Settings(_env_file=None, dry_run=False))

    await bot.handle_update({
        "update_id": 21,
        "message": {
            "chat": {"id": -500, "type": "supergroup"},
            "from": {"id": 456},
            "text": "/whoami",
        },
    })

    client.send_message.assert_awaited_once()
    assert "private chat" in client.send_message.await_args.args[1]
    assert "456" not in client.send_message.await_args.args[1]
