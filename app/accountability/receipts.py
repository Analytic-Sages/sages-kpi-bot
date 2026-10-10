"""SQLite-backed delivery claims and successful-send receipts."""
from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path


class SQLiteReceiptStore:
    """Durable receipts with expiring claims to avoid concurrent duplicate sends."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("Use a file-backed database so receipts persist across connections")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reminder_receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    recipient_chat_id TEXT NOT NULL,
                    reminder_type TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_task_sent "
                "ON reminder_receipts(task_id, sent_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reminder_claims (
                    claim_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    recipient_chat_id TEXT NOT NULL,
                    reminder_type TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(task_id, recipient_chat_id, reminder_type)
                )"""
            )

    async def claim(
        self,
        *,
        task_id: str,
        recipient_chat_id: str,
        reminder_type: str,
        now: datetime,
        day_start: datetime,
        cooldown: timedelta,
        max_per_day: int,
    ) -> str | None:
        return await asyncio.to_thread(
            self._claim,
            task_id,
            recipient_chat_id,
            reminder_type,
            now,
            day_start,
            cooldown,
            max_per_day,
        )

    def _claim(
        self,
        task_id: str,
        recipient_chat_id: str,
        reminder_type: str,
        now: datetime,
        day_start: datetime,
        cooldown: timedelta,
        max_per_day: int,
    ) -> str | None:
        claim_id = uuid.uuid4().hex
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM reminder_claims WHERE expires_at <= ?", (now_iso,))
            latest = connection.execute(
                "SELECT sent_at FROM reminder_receipts WHERE task_id = ? "
                "AND recipient_chat_id = ? AND reminder_type = ? ORDER BY sent_at DESC LIMIT 1",
                (task_id, recipient_chat_id, reminder_type),
            ).fetchone()
            if latest and datetime.fromisoformat(latest["sent_at"]) > now - cooldown:
                return None
            count = connection.execute(
                "SELECT COUNT(*) AS total FROM reminder_receipts "
                "WHERE task_id = ? AND sent_at >= ?",
                (task_id, day_start.isoformat()),
            ).fetchone()["total"]
            pending = connection.execute(
                "SELECT COUNT(*) AS total FROM reminder_claims WHERE task_id = ?",
                (task_id,),
            ).fetchone()["total"]
            if count + pending >= max_per_day:
                return None
            try:
                connection.execute(
                    "INSERT INTO reminder_claims VALUES (?, ?, ?, ?, ?)",
                    (
                        claim_id,
                        task_id,
                        recipient_chat_id,
                        reminder_type,
                        (now + timedelta(minutes=2)).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            return claim_id

    async def mark_sent(
        self,
        *,
        claim_id: str,
        task_id: str,
        recipient_chat_id: str,
        reminder_type: str,
        sent_at: datetime,
    ) -> int | None:
        return await asyncio.to_thread(
            self._mark_sent, claim_id, task_id, recipient_chat_id, reminder_type, sent_at
        )

    def _mark_sent(
        self,
        claim_id: str,
        task_id: str,
        recipient_chat_id: str,
        reminder_type: str,
        sent_at: datetime,
    ) -> int | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            claim = connection.execute(
                "SELECT claim_id FROM reminder_claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if not claim:
                return None
            cursor = connection.execute(
                "INSERT INTO reminder_receipts(task_id, recipient_chat_id, reminder_type, sent_at) "
                "VALUES (?, ?, ?, ?)",
                (task_id, recipient_chat_id, reminder_type, sent_at.isoformat()),
            )
            connection.execute("DELETE FROM reminder_claims WHERE claim_id = ?", (claim_id,))
            return int(cursor.lastrowid)

    async def release(self, claim_id: str) -> None:
        await asyncio.to_thread(self._release, claim_id)

    def _release(self, claim_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM reminder_claims WHERE claim_id = ?", (claim_id,))

    async def count_for_task_on_day(self, task_id: str, day_start: datetime) -> int:
        return await asyncio.to_thread(self._count_for_task_on_day, task_id, day_start)

    def _count_for_task_on_day(self, task_id: str, day_start: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM reminder_receipts "
                "WHERE task_id = ? AND sent_at >= ?",
                (task_id, day_start.isoformat()),
            ).fetchone()
            return int(row["total"])
