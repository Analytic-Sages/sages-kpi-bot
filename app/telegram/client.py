"""Small asynchronous Telegram Bot API client built on httpx."""
from __future__ import annotations

from typing import Any

import httpx


class TelegramAPIError(RuntimeError):
    """Raised when Telegram returns an unsuccessful Bot API response."""


class TelegramClient:
    """Async Telegram Bot API client with bounded network timeouts."""

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        if not token.strip():
            raise ValueError("Telegram bot token must not be empty")
        self._base_url = f"https://api.telegram.org/bot{token.strip()}"
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(35.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(f"{self._base_url}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramAPIError(f"Telegram API request {method} failed") from exc
        if not isinstance(body, dict) or not body.get("ok"):
            if isinstance(body, dict):
                description = body.get("description", "unknown Telegram API error")
            else:
                description = "invalid response"
            raise TelegramAPIError(f"Telegram API request {method} failed: {description}")
        return body.get("result")

    async def set_commands(self) -> None:
        commands = [
            {"command": "start", "description": "Start Polaris and see what it does"},
            {"command": "help", "description": "Show available commands"},
            {"command": "status", "description": "View your accountability status"},
            {"command": "goal", "description": "Set a KPI goal"},
            {"command": "blocker", "description": "Report a blocker"},
            {"command": "jd", "description": "Check role and job-description alignment"},
            {"command": "growth", "description": "View private website and YouTube growth metrics"},
        ]
        await self._request("setMyCommands", {"commands": commands})

    async def get_updates(self, offset: int | None, *, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._request("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramAPIError("Telegram getUpdates returned an unexpected result")
        return [item for item in result if isinstance(item, dict)]

    async def send_message(self, chat_id: int | str, text: str) -> None:
        await self._request(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
