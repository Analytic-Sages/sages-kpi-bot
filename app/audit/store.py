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


    async def list_events(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        outcome: str | None = None,
        worksheet: str | None = None,
    ) -> dict[str, Any]:
        """Read a bounded page of audit events without creating or mutating the database."""
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise ValueError("Audit pagination is outside the allowed range")
        if outcome is not None and (not outcome.strip() or len(outcome) > 32):
            raise ValueError("Invalid audit outcome filter")
        if worksheet is not None and (not worksheet.strip() or len(worksheet) > 128):
            raise ValueError("Invalid worksheet filter")
        return await asyncio.to_thread(
            self._list_events_sync,
            limit=limit,
            offset=offset,
            outcome=outcome,
            worksheet=worksheet,
        )

    def _list_events_sync(
        self,
        *,
        limit: int,
        offset: int,
        outcome: str | None,
        worksheet: str | None,
    ) -> dict[str, Any]:
        # A read-only URI prevents this endpoint from creating a missing SQLite file.
        if not self.database_path.is_file():
            return {"items": [], "total": 0}
        database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True, timeout=5.0) as connection:
            connection.row_factory = sqlite3.Row
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("audit_events",),
            ).fetchone()
            if exists is None:
                return {"items": [], "total": 0}

            filters: list[str] = []
            parameters: list[str] = []
            if outcome is not None:
                filters.append("outcome = ?")
                parameters.append(outcome.strip())
            if worksheet is not None:
                filters.append("worksheet = ?")
                parameters.append(worksheet.strip())
            where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
            total = connection.execute(
                f"SELECT COUNT(*) FROM audit_events{where_clause}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT event_id, created_at, request_id, actor_id, action, worksheet,
                       record_id, changed_fields_json, before_json, after_json, outcome
                FROM audit_events{where_clause}
                ORDER BY created_at DESC, event_id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "event_id": row["event_id"],
                    "created_at": row["created_at"],
                    "request_id": row["request_id"],
                    "actor_id": row["actor_id"],
                    "action": row["action"],
                    "worksheet": row["worksheet"],
                    "record_id": row["record_id"],
                    "changed_fields": _decode_json_list(row["changed_fields_json"]),
                    "before": _decode_json_object(row["before_json"]),
                    "after": _decode_json_object(row["after_json"]),
                    "outcome": row["outcome"],
                }
            )
        return {"items": items, "total": int(total)}


def _decode_json_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _decode_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
