"""Append-only SQLite audit log for sensitive operational mutations."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class SQLiteAuditLog:
    """Persist mutation audit events without logging secrets or Telegram identifiers."""

    def __init__(self, database_path: str) -> None:
        if not database_path.strip():
            raise ValueError("Audit database path must not be blank")
        self.database_path = Path(database_path).expanduser()

    async def record(self, event: dict[str, Any]) -> None:
        await asyncio.to_thread(self._record_sync, event)

    def _record_sync(self, event: dict[str, Any]) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        event_id = str(uuid4())
        with sqlite3.connect(self.database_path, timeout=5.0) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    worksheet TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    outcome TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, created_at, request_id, actor_id, action, worksheet,
                    record_id, changed_fields_json, before_json, after_json, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    created_at,
                    str(event["request_id"]),
                    str(event["actor_id"]),
                    str(event["action"]),
                    str(event["worksheet"]),
                    str(event["record_id"]),
                    json.dumps(event["changed_fields"], ensure_ascii=False, sort_keys=True),
                    json.dumps(event["before"], ensure_ascii=False, sort_keys=True),
                    json.dumps(event["after"], ensure_ascii=False, sort_keys=True),
                    str(event["outcome"]),
                ),
            )
            connection.commit()
