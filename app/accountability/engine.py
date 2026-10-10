"""Formula-driven reminder eligibility and delivery orchestration."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.accountability.receipts import SQLiteReceiptStore

logger = logging.getLogger(__name__)


def _truthy(value: Any) -> bool:
    """Interpret common spreadsheet checkbox values conservatively."""
    return str(value or "").strip().casefold() in {"true", "yes", "y", "1", "checked"}


def _required(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


@dataclass(frozen=True, slots=True)
class ReminderDecision:
    """A deterministic decision explaining whether a task may be messaged."""

    eligible: bool
    reason: str
    task_id: str | None = None
    task_name: str | None = None
    reminder_type: str | None = None
    recipient_chat_id: str | None = None
    recipient_person_id: str | None = None


def evaluate_task(
    task: dict[str, Any],
    person: dict[str, Any] | None,
) -> ReminderDecision:
    """Use spreadsheet formula flags and directory eligibility fields as the policy source.

    Required columns are explicit. If the live workbook does not contain them, the engine
    blocks rather than inferring equivalent business rules.
    """
    task_id = _required(task, "Task ID")
    task_name = _required(task, "Task Name")
    owner_id = _required(task, "Assigned To ID")
    if not task_id or not task_name or not owner_id:
        return ReminderDecision(False, "task_schema_missing_required_fields", task_id, task_name)
    if "Remind Now?" not in task or "Escalate?" not in task:
        return ReminderDecision(False, "task_schema_missing_formula_flags", task_id, task_name)
    if not _truthy(task.get("Remind Now?")):
        return ReminderDecision(False, "spreadsheet_reminder_flag_false", task_id, task_name)
    if person is None:
        return ReminderDecision(False, "owner_not_found", task_id, task_name)

    person_id = _required(person, "Person ID")
    status = _required(person, "Status")
    if not person_id or person_id != owner_id:
        return ReminderDecision(False, "owner_identity_mismatch", task_id, task_name)
    if not status or status.casefold() != "active":
        return ReminderDecision(False, "owner_not_active", task_id, task_name)
    if "Notify?" not in person or "Reachable?" not in person:
        return ReminderDecision(
            False, "people_schema_missing_notification_flags", task_id, task_name
        )
    if not _truthy(person.get("Notify?")):
        return ReminderDecision(False, "owner_notifications_disabled", task_id, task_name)
    if not _truthy(person.get("Reachable?")):
        return ReminderDecision(False, "owner_not_reachable", task_id, task_name)

    escalating = _truthy(task.get("Escalate?"))
    recipient_field = "Manager Chat ID" if escalating else "Telegram Chat ID"
    recipient_id = _required(person, recipient_field)
    if not recipient_id:
        return ReminderDecision(False, "recipient_chat_id_missing", task_id, task_name)
    return ReminderDecision(
        True,
        "eligible_escalation" if escalating else "eligible_reminder",
        task_id,
        task_name,
        "escalation" if escalating else "reminder",
        recipient_id,
        person_id,
    )


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: str
    reason: str
    receipt_id: int | None = None


class AccountabilityEngine:
    """Apply safety gates, cooldowns, daily limits, delivery, and durable receipts."""

    def __init__(
        self,
        receipts: SQLiteReceiptStore,
        sender: Callable[[str, str], Awaitable[None]],
        *,
        timezone: str = "Africa/Lagos",
        cooldown_minutes: int = 240,
        max_per_task_per_day: int = 2,
        dry_run: bool = True,
    ) -> None:
        if cooldown_minutes < 1 or max_per_task_per_day < 1:
            raise ValueError("Cooldown and daily limit must be positive")
        self.receipts = receipts
        self.sender = sender
        self.timezone = ZoneInfo(timezone)
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.max_per_task_per_day = max_per_task_per_day
        self.dry_run = dry_run

    async def process(
        self,
        decision: ReminderDecision,
        *,
        now: datetime | None = None,
    ) -> DeliveryResult:
        """Deliver one approved reminder; only confirmed sends create receipts."""
        if not decision.eligible:
            return DeliveryResult("skipped", decision.reason)
        if not all(
            [
                decision.task_id,
                decision.task_name,
                decision.reminder_type,
                decision.recipient_chat_id,
            ]
        ):
            return DeliveryResult("skipped", "decision_missing_delivery_fields")
        instant = now or datetime.now(self.timezone)
        if instant.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        local_now = instant.astimezone(self.timezone)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.dry_run:
            logger.info(
                "accountability_dry_run task_id=%s type=%s recipient=%s",
                decision.task_id,
                decision.reminder_type,
                decision.recipient_chat_id,
            )
            return DeliveryResult("dry_run", "dry_run_enabled_no_send_no_receipt")

        claim_id = await self.receipts.claim(
            task_id=decision.task_id,
            recipient_chat_id=decision.recipient_chat_id,
            reminder_type=decision.reminder_type,
            now=local_now,
            day_start=day_start,
            cooldown=self.cooldown,
            max_per_day=self.max_per_task_per_day,
        )
        if claim_id is None:
            return DeliveryResult("skipped", "cooldown_daily_limit_or_in_flight")

        if decision.reminder_type == "escalation":
            message = (
                f"Task escalation: {decision.task_name}\n"
                "The spreadsheet escalation flag is active. Please review the task tracker."
            )
        else:
            message = (
                f"Accountability reminder: {decision.task_name}\n"
                "Please review the task in the Master Task Tracker and update its status."
            )
        try:
            await self.sender(decision.recipient_chat_id, message)
        except Exception:
            await self.receipts.release(claim_id)
            logger.exception("accountability_delivery_failed task_id=%s", decision.task_id)
            return DeliveryResult("failed", "telegram_delivery_failed_receipt_not_written")

        receipt_id = await self.receipts.mark_sent(
            claim_id=claim_id,
            task_id=decision.task_id,
            recipient_chat_id=decision.recipient_chat_id,
            reminder_type=decision.reminder_type,
            sent_at=local_now,
        )
        if receipt_id is None:
            logger.error("accountability_receipt_write_failed task_id=%s", decision.task_id)
            return DeliveryResult("sent_unreceipted", "message_sent_but_receipt_write_failed")
        return DeliveryResult("sent", "telegram_delivery_confirmed", receipt_id)
