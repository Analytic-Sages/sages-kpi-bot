from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.accountability.engine import AccountabilityEngine, ReminderDecision, evaluate_task
from app.accountability.receipts import SQLiteReceiptStore

TZ = ZoneInfo("Africa/Lagos")
TASK = {
    "Task ID": "T-001",
    "Task Name": "Publish dashboard",
    "Assigned To ID": "P-001",
    "Remind Now?": "TRUE",
    "Escalate?": "FALSE",
}
PERSON = {
    "Person ID": "P-001",
    "Status": "Active",
    "Notify?": "Yes",
    "Reachable?": "TRUE",
    "Telegram Chat ID": "12345",
    "Manager Chat ID": "67890",
}


def test_formula_flags_drive_reminder_decision():
    decision = evaluate_task(TASK, PERSON)
    assert decision.eligible is True
    assert decision.reminder_type == "reminder"
    assert decision.recipient_chat_id == "12345"


def test_formula_escalation_flag_routes_to_manager():
    decision = evaluate_task({**TASK, "Escalate?": "Yes"}, PERSON)
    assert decision.eligible is True
    assert decision.reminder_type == "escalation"
    assert decision.recipient_chat_id == "67890"


@pytest.mark.parametrize(
    ("task", "person", "reason", "eligible"),
    [
        ({**TASK, "Remind Now?": "FALSE"}, PERSON, "spreadsheet_reminder_flag_false", False),
        ({**TASK, "Escalate?": None}, PERSON, "eligible_reminder", True),
        (TASK, {**PERSON, "Notify?": "No"}, "owner_notifications_disabled", False),
        (TASK, {**PERSON, "Reachable?": "No"}, "owner_not_reachable", False),
        (TASK, {**PERSON, "Status": "Inactive"}, "owner_not_active", False),
        (TASK, None, "owner_not_found", False),
        (
            {key: value for key, value in TASK.items() if key != "Escalate?"},
            PERSON,
            "task_schema_missing_formula_flags",
            False,
        ),
    ],
)
def test_safety_gates(task, person, reason, eligible):
    decision = evaluate_task(task, person)
    assert decision.reason == reason
    assert decision.eligible is eligible


@pytest.mark.asyncio
async def test_dry_run_never_sends_or_records(tmp_path):
    sender = AsyncMock()
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    engine = AccountabilityEngine(store, sender, dry_run=True)
    now = datetime(2026, 10, 10, 10, tzinfo=TZ)
    result = await engine.process(evaluate_task(TASK, PERSON), now=now)
    assert result.status == "dry_run"
    sender.assert_not_awaited()
    assert await store.count_for_task_on_day("T-001", now.replace(hour=0, minute=0)) == 0


@pytest.mark.asyncio
async def test_receipt_written_only_after_successful_delivery(tmp_path):
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    sender = AsyncMock()
    engine = AccountabilityEngine(store, sender, dry_run=False, cooldown_minutes=30)
    now = datetime(2026, 10, 10, 10, tzinfo=TZ)
    result = await engine.process(evaluate_task(TASK, PERSON), now=now)
    assert result.status == "sent"
    sender.assert_awaited_once()
    assert await store.count_for_task_on_day("T-001", now.replace(hour=0, minute=0)) == 1
    blocked = await engine.process(evaluate_task(TASK, PERSON), now=now + timedelta(minutes=5))
    assert blocked.status == "skipped"
    assert blocked.reason == "cooldown_daily_limit_or_in_flight"


@pytest.mark.asyncio
async def test_failed_delivery_does_not_write_receipt_and_releases_claim(tmp_path):
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    sender = AsyncMock(side_effect=RuntimeError("network unavailable"))
    engine = AccountabilityEngine(store, sender, dry_run=False)
    now = datetime(2026, 10, 10, 10, tzinfo=TZ)
    result = await engine.process(evaluate_task(TASK, PERSON), now=now)
    assert result.status == "failed"
    assert await store.count_for_task_on_day("T-001", now.replace(hour=0, minute=0)) == 0
    sender.side_effect = None
    sender.reset_mock()
    retry = await engine.process(evaluate_task(TASK, PERSON), now=now + timedelta(minutes=1))
    assert retry.status == "sent"


@pytest.mark.asyncio
async def test_ineligible_decision_never_sends(tmp_path):
    sender = AsyncMock()
    engine = AccountabilityEngine(SQLiteReceiptStore(tmp_path / "receipts.db"), sender, dry_run=False)
    result = await engine.process(ReminderDecision(False, "owner_not_active"))
    assert result.status == "skipped"
    sender.assert_not_awaited()
