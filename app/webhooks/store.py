"""Authenticated, idempotent inbound webhook event ledger."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class WebhookValidationError(ValueError):
    """Raised when a signed webhook is malformed or fails authentication."""


class WebhookConflictError(RuntimeError):
    """Raised when an event ID is reused for a different event body."""


_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def verify_signature(
    *,
    secret: str,
    timestamp: str,
    event_id: str,
    body: bytes,
    signature: str,
    now: int | None = None,
    max_skew_seconds: int = 300,
) -> None:
    """Validate timestamp freshness and HMAC-SHA256 over request metadata and raw body."""
    if not secret:
        raise WebhookValidationError("Webhook signing secret is not configured")
    if not timestamp.isdigit():
        raise WebhookValidationError("Invalid webhook timestamp")
    timestamp_value = int(timestamp)
    current = int(datetime.now(UTC).timestamp()) if now is None else now
    if abs(current - timestamp_value) > max_skew_seconds:
        raise WebhookValidationError("Webhook timestamp is outside the allowed window")
    if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
        raise WebhookValidationError("Invalid webhook event ID")
    if not signature.startswith("sha256="):
        raise WebhookValidationError("Invalid webhook signature format")
    signed_content = timestamp.encode() + b"." + event_id.encode() + b"." + body
    expected = "sha256=" + hmac.new(
        secret.encode(), signed_content, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookValidationError("Webhook signature verification failed")


def parse_event(body: bytes, header_event_id: str) -> tuple[str, str]:
    """Validate the small event envelope; do not retain arbitrary payload data."""
    try:
        payload: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookValidationError("Webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookValidationError("Webhook body must be a JSON object")
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    if not isinstance(event_id, str) or event_id != header_event_id:
        raise WebhookValidationError("Body event_id must match X-Polaris-Event-Id")
    if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
        raise WebhookValidationError("Invalid event_type")
    return event_id, event_type


class SQLiteWebhookEventStore:
    """Durable idempotency ledger. Only event metadata and a body hash are stored."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("Use a file-backed database so webhook IDs persist")
        Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    received_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhook_events_received "
                "ON webhook_events(received_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    async def record(self, event_id: str, event_type: str, body: bytes) -> bool:
        """Return True for first delivery, False for identical duplicate delivery."""
        return await asyncio.to_thread(self._record, event_id, event_type, body)

    def _record(self, event_id: str, event_type: str, body: bytes) -> bool:
        body_hash = hashlib.sha256(body).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT event_type, body_sha256 FROM webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing:
                if (
                    existing["event_type"] != event_type
                    or existing["body_sha256"] != body_hash
                ):
                    raise WebhookConflictError(
                        "Event ID was already used for a different event body"
                    )
                return False
            connection.execute(
                "INSERT INTO webhook_events(event_id, event_type, body_sha256, received_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, event_type, body_hash, datetime.now(UTC).isoformat()),
            )
            return True
