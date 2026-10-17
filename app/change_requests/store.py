"""Durable, dry-run-first change-request state machine.

This store records requests only. It does not authorize workbook access or perform
Google Sheets writes; callers must apply those checks independently.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

RequestStatus = Literal[
    "DRAFT",
    "AWAITING_CONFIRMATION",
    "AWAITING_APPROVAL",
    "APPROVED",
    "APPLIED",
    "REJECTED",
    "EXPIRED",
    "CONFLICT",
    "FAILED",
    "CANCELLED",
]
RequestTier = Literal[1, 2]

TERMINAL_STATUSES = frozenset(
    {"APPLIED", "REJECTED", "EXPIRED", "CONFLICT", "FAILED", "CANCELLED"}
)


class ChangeRequestError(ValueError):
    """Base error for an invalid or unauthorized change-request operation."""


class ChangeRequestNotFound(ChangeRequestError):
    """Raised when a request ID does not exist."""


class ChangeRequestForbidden(ChangeRequestError):
    """Raised when the actor is not allowed to perform the requested transition."""


class ChangeRequestInvalidTransition(ChangeRequestError):
    """Raised when a request is not in a state that permits the requested action."""


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    request_id: str
    actor_id: str
    tier: RequestTier
    status: RequestStatus
    payload: dict[str, Any]
    allowed_approver_ids: tuple[str, ...]
    source_chat_id: str | None
    source_message_id: str | None
    reason: str | None
    created_at: datetime
    updated_at: datetime
    confirmation_expires_at: datetime | None
    approval_expires_at: datetime | None
    confirmed_at: datetime | None
    approver_id: str | None
    applied_at: datetime | None
    outcome_code: str | None


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware")
    return current.astimezone(UTC)


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Stored change-request payload is invalid")
    return parsed


class SQLiteChangeRequestStore:
    """Persist request state and make transitions atomic across processes."""

    def __init__(self, database_path: str) -> None:
        if not database_path.strip():
            raise ValueError("Change-request database path must not be blank")
        self.database_path = Path(database_path).expanduser()

    async def create_draft(
        self,
        *,
        actor_id: str,
        tier: RequestTier,
        payload: dict[str, Any],
        allowed_approver_ids: tuple[str, ...] | list[str] = (),
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> ChangeRequest:
        actor = actor_id.strip()
        if not actor:
            raise ChangeRequestError("A verified actor ID is required")
        if tier not in (1, 2):
            raise ChangeRequestError("Request tier must be 1 or 2")
        if not isinstance(payload, dict) or not payload:
            raise ChangeRequestError("A non-empty validated payload is required")
        if tier == 2 and not allowed_approver_ids:
            raise ChangeRequestError("Tier 2 requests require at least one approver")
        approvers = tuple(dict.fromkeys(str(item).strip() for item in allowed_approver_ids))
        if any(not item for item in approvers):
            raise ChangeRequestError("Approver IDs must not be blank")
        if actor in approvers:
            approvers = tuple(item for item in approvers if item != actor)
        if tier == 2 and not approvers:
            raise ChangeRequestError("Requester cannot approve their own request")
        if (source_chat_id is None) != (source_message_id is None):
            raise ChangeRequestError("Source chat and message IDs must be supplied together")
        identifier = (request_id or str(uuid4())).strip()
        if not identifier:
            raise ChangeRequestError("Request ID must not be blank")
        timestamp = _utc(now)
        record = await asyncio.to_thread(
            self._create_draft_sync,
            request_id=identifier,
            actor_id=actor,
            tier=tier,
            payload=payload,
            allowed_approver_ids=approvers,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            reason=reason.strip() if reason and reason.strip() else None,
            now=timestamp,
        )
        return record

    def _create_draft_sync(
        self,
        *,
        request_id: str,
        actor_id: str,
        tier: RequestTier,
        payload: dict[str, Any],
        allowed_approver_ids: tuple[str, ...],
        source_chat_id: str | None,
        source_message_id: str | None,
        reason: str | None,
        now: datetime,
    ) -> ChangeRequest:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if source_chat_id is not None:
                existing = connection.execute(
                    """SELECT * FROM change_requests
                       WHERE source_chat_id = ? AND source_message_id = ?""",
                    (source_chat_id, source_message_id),
                ).fetchone()
                if existing is not None:
                    if existing["actor_id"] != actor_id:
                        raise ChangeRequestForbidden(
                            "Source message is already bound to another actor"
                        )
                    existing_request = self._from_row(existing)
                    if existing_request.payload != payload or existing_request.tier != tier:
                        raise ChangeRequestError(
                            "Source message is already bound to different request data"
                        )
                    return existing_request
            existing_id = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing_id is not None:
                existing_request = self._from_row(existing_id)
                if existing_request.actor_id != actor_id:
                    raise ChangeRequestForbidden("Request ID is already bound to another actor")
                if (
                    existing_request.payload != payload
                    or existing_request.tier != tier
                    or existing_request.allowed_approver_ids != allowed_approver_ids
                ):
                    raise ChangeRequestError(
                        "Request ID is already bound to different request data"
                    )
                return existing_request
            connection.execute(
                """INSERT INTO change_requests (
                    request_id, actor_id, tier, status, payload_json,
                    allowed_approver_ids_json, source_chat_id, source_message_id,
                    reason, created_at, updated_at, confirmation_expires_at,
                    approval_expires_at, confirmed_at, approver_id, applied_at, outcome_code
                ) VALUES (
                    ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL
                )""",
                (
                    request_id,
                    actor_id,
                    tier,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(allowed_approver_ids),
                    source_chat_id,
                    source_message_id,
                    reason,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            record = self._from_row(row)
            self._record_event(
                connection,
                request=record,
                actor_id=actor_id,
                action="created",
                from_status=None,
                to_status="DRAFT",
                occurred_at=now,
            )
            return record

    async def submit_for_confirmation(
        self, request_id: str, *, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="submit",
        )

    async def confirm(
        self, request_id: str, *, actor_id: str, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="confirm",
            actor_id=actor_id.strip(),
        )

    async def approve(
        self, request_id: str, *, approver_id: str, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="approve",
            actor_id=approver_id.strip(),
        )

    async def reject(
        self, request_id: str, *, approver_id: str, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="reject",
            actor_id=approver_id.strip(),
        )

    async def cancel(
        self, request_id: str, *, actor_id: str, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="cancel",
            actor_id=actor_id.strip(),
        )

    async def mark_applied(
        self, request_id: str, *, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="applied",
        )

    async def mark_conflict(
        self, request_id: str, *, now: datetime | None = None
    ) -> ChangeRequest:
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="conflict",
        )

    async def mark_failed(
        self, request_id: str, *, outcome_code: str, now: datetime | None = None
    ) -> ChangeRequest:
        safe_code = outcome_code.strip().upper()
        if not safe_code or len(safe_code) > 64 or not safe_code.replace("_", "").isalnum():
            raise ChangeRequestError("Outcome code must be a short identifier")
        return await self._transition(
            request_id,
            now=_utc(now),
            operation="failed",
            outcome_code=safe_code,
        )

    async def get(self, request_id: str) -> ChangeRequest:
        return await asyncio.to_thread(self._get_sync, request_id.strip())

    def _get_sync(self, request_id: str) -> ChangeRequest:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise ChangeRequestNotFound("Change request was not found")
        return self._from_row(row)

    async def expire_due(self, *, now: datetime | None = None) -> int:
        timestamp = _utc(now)
        return await asyncio.to_thread(self._expire_due_sync, timestamp)

    def _expire_due_sync(self, now: datetime) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM change_requests
                   WHERE (status = 'AWAITING_CONFIRMATION'
                          AND confirmation_expires_at <= ?)
                      OR (status = 'AWAITING_APPROVAL' AND approval_expires_at <= ?)""",
                (now.isoformat(), now.isoformat()),
            ).fetchall()
            for row in rows:
                request = self._from_row(row)
                code = (
                    "CONFIRMATION_EXPIRED"
                    if request.status == "AWAITING_CONFIRMATION"
                    else "APPROVAL_EXPIRED"
                )
                connection.execute(
                    """UPDATE change_requests
                       SET status = 'EXPIRED', updated_at = ?, outcome_code = ?
                       WHERE request_id = ?""",
                    (now.isoformat(), code, request.request_id),
                )
                self._record_event(
                    connection,
                    request=request,
                    actor_id=request.actor_id,
                    action="expire",
                    from_status=request.status,
                    to_status="EXPIRED",
                    occurred_at=now,
                    outcome_code=code,
                )
            return len(rows)

    async def _transition(
        self,
        request_id: str,
        *,
        now: datetime,
        operation: str,
        actor_id: str | None = None,
        outcome_code: str | None = None,
    ) -> ChangeRequest:
        return await asyncio.to_thread(
            self._transition_sync,
            request_id.strip(),
            now,
            operation,
            actor_id,
            outcome_code,
        )

    def _transition_sync(
        self,
        request_id: str,
        now: datetime,
        operation: str,
        actor_id: str | None,
        outcome_code: str | None,
    ) -> ChangeRequest:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ChangeRequestNotFound("Change request was not found")
            request = self._from_row(row)
            status = request.status
            updates: dict[str, Any] = {"updated_at": now.isoformat()}

            if operation == "submit":
                if status != "DRAFT":
                    raise ChangeRequestInvalidTransition("Only draft requests can be submitted")
                updates.update(
                    status="AWAITING_CONFIRMATION",
                    confirmation_expires_at=(now + timedelta(minutes=10)).isoformat(),
                )
            elif operation == "confirm":
                if actor_id != request.actor_id:
                    raise ChangeRequestForbidden("Only the requester can confirm this request")
                if status != "AWAITING_CONFIRMATION":
                    raise ChangeRequestInvalidTransition("Request is not awaiting confirmation")
                if (
                    request.confirmation_expires_at is None
                    or now >= request.confirmation_expires_at
                ):
                    updates.update(status="EXPIRED", outcome_code="CONFIRMATION_EXPIRED")
                elif request.tier == 1:
                    updates.update(status="APPROVED", confirmed_at=now.isoformat())
                else:
                    updates.update(
                        status="AWAITING_APPROVAL",
                        confirmed_at=now.isoformat(),
                        approval_expires_at=(now + timedelta(hours=48)).isoformat(),
                    )
            elif operation in {"approve", "reject"}:
                if status != "AWAITING_APPROVAL":
                    raise ChangeRequestInvalidTransition("Request is not awaiting approval")
                if actor_id == request.actor_id:
                    raise ChangeRequestForbidden(
                        "Requester cannot approve or reject their own request"
                    )
                if actor_id not in request.allowed_approver_ids:
                    raise ChangeRequestForbidden("Actor is not an authorized approver")
                if request.approval_expires_at is None or now >= request.approval_expires_at:
                    updates.update(status="EXPIRED", outcome_code="APPROVAL_EXPIRED")
                elif operation == "approve":
                    updates.update(status="APPROVED", approver_id=actor_id)
                else:
                    updates.update(status="REJECTED", approver_id=actor_id)
            elif operation == "cancel":
                if actor_id != request.actor_id:
                    raise ChangeRequestForbidden("Only the requester can cancel this request")
                if status not in {"DRAFT", "AWAITING_CONFIRMATION", "AWAITING_APPROVAL"}:
                    raise ChangeRequestInvalidTransition("Request can no longer be cancelled")
                updates.update(status="CANCELLED", outcome_code="REQUEST_CANCELLED")
            elif operation == "applied":
                if status == "APPLIED":
                    return request
                if status != "APPROVED":
                    raise ChangeRequestInvalidTransition("Only approved requests can be applied")
                updates.update(status="APPLIED", applied_at=now.isoformat(), outcome_code=None)
            elif operation == "conflict":
                if status != "APPROVED":
                    raise ChangeRequestInvalidTransition(
                        "Only approved requests can conflict at apply time"
                    )
                updates.update(status="CONFLICT", outcome_code="STALE_SNAPSHOT")
            elif operation == "failed":
                if status != "APPROVED":
                    raise ChangeRequestInvalidTransition(
                        "Only approved requests can fail at apply time"
                    )
                updates.update(status="FAILED", outcome_code=outcome_code)
            else:
                raise ChangeRequestError("Unknown change-request operation")

            columns = ", ".join(f"{name} = ?" for name in updates)
            values = list(updates.values()) + [request_id]
            connection.execute(
                f"UPDATE change_requests SET {columns} WHERE request_id = ?", values
            )
            refreshed = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            result = self._from_row(refreshed)
            event_actor = actor_id or request.actor_id
            self._record_event(
                connection,
                request=result,
                actor_id=event_actor,
                action=operation,
                from_status=status,
                to_status=result.status,
                occurred_at=now,
                outcome_code=result.outcome_code,
            )
            return result

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        *,
        request: ChangeRequest,
        actor_id: str,
        action: str,
        from_status: str | None,
        to_status: str,
        occurred_at: datetime,
        outcome_code: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO change_request_events (
                event_id, request_id, occurred_at, actor_id, action,
                from_status, to_status, outcome_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                request.request_id,
                occurred_at.isoformat(),
                actor_id,
                action,
                from_status,
                to_status,
                outcome_code,
            ),
        )

    async def list_events(self, request_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_events_sync, request_id.strip())

    def _list_events_sync(self, request_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_id, request_id, occurred_at, actor_id, action,
                          from_status, to_status, outcome_code
                   FROM change_request_events WHERE request_id = ?
                   ORDER BY sequence""",
                (request_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS change_requests (
                request_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                allowed_approver_ids_json TEXT NOT NULL,
                source_chat_id TEXT,
                source_message_id TEXT,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmation_expires_at TEXT,
                approval_expires_at TEXT,
                confirmed_at TEXT,
                approver_id TEXT,
                applied_at TEXT,
                outcome_code TEXT,
                UNIQUE (source_chat_id, source_message_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS change_request_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                outcome_code TEXT,
                FOREIGN KEY (request_id) REFERENCES change_requests(request_id)
            )"""
        )
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ChangeRequest:
        return ChangeRequest(
            request_id=row["request_id"],
            actor_id=row["actor_id"],
            tier=int(row["tier"]),  # type: ignore[arg-type]
            status=row["status"],  # type: ignore[arg-type]
            payload=_json_object(row["payload_json"]),
            allowed_approver_ids=tuple(json.loads(row["allowed_approver_ids_json"])),
            source_chat_id=row["source_chat_id"],
            source_message_id=row["source_message_id"],
            reason=row["reason"],
            created_at=_parse_time(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_time(row["updated_at"]),  # type: ignore[arg-type]
            confirmation_expires_at=_parse_time(row["confirmation_expires_at"]),
            approval_expires_at=_parse_time(row["approval_expires_at"]),
            confirmed_at=_parse_time(row["confirmed_at"]),
            approver_id=row["approver_id"],
            applied_at=_parse_time(row["applied_at"]),
            outcome_code=row["outcome_code"],
        )
