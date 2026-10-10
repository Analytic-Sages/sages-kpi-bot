from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.scheduled.workflows import ScheduledWorkflowService, build_scheduler


class FakeSheets:
    def __init__(self, records):
        self.records = records

    async def read_records(self, worksheet):
        if worksheet not in self.records:
            raise RuntimeError(f"Unexpected worksheet: {worksheet}")
        return self.records[worksheet]


def sample_records():
    return {
        "KPI Register": [
            {
                "KPI ID": "K001",
                "KPI": "Qualified Leads",
                "Owner Person ID": "P001",
                "Target": "10",
                "Active?": "TRUE",
            }
        ],
        "Weekly KPI Log": [
            {
                "Week Start": "2026-10-05",
                "KPI ID": "K001",
                "Actual": "8",
                "Status": "On Track",
            }
        ],
    }


@pytest.mark.asyncio
async def test_monday_broadcast_dry_run_does_not_send():
    sheets = FakeSheets(sample_records())
    telegram = AsyncMock()
    settings = Settings(_env_file=None, dry_run=True, telegram_core_chat_id="-100123")
    service = ScheduledWorkflowService(sheets, telegram, settings)

    await service.monday_kpi_broadcast()

    telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_monday_broadcast_sends_snapshot_to_private_core_chat():
    sheets = FakeSheets(sample_records())
    telegram = AsyncMock()
    settings = Settings(_env_file=None, dry_run=False, telegram_core_chat_id="-100123")
    service = ScheduledWorkflowService(sheets, telegram, settings)

    await service.monday_kpi_broadcast()

    telegram.send_message.assert_awaited_once()
    args = telegram.send_message.await_args.args
    assert args[0] == "-100123"
    assert "Qualified Leads: 8 / 10" in args[1]


@pytest.mark.asyncio
async def test_missing_core_chat_id_skips_delivery():
    sheets = FakeSheets(sample_records())
    telegram = AsyncMock()
    settings = Settings(_env_file=None, dry_run=False, telegram_core_chat_id=None)
    service = ScheduledWorkflowService(sheets, telegram, settings)

    await service.focus_prompt()

    telegram.send_message.assert_not_awaited()


def test_scheduler_has_expected_utc_jobs_and_no_overlapping_instances():
    service = AsyncMock()
    scheduler = build_scheduler(service)

    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs) == {
        "monday-kpi-broadcast",
        "weekday-focus-prompt",
        "friday-checkout",
    }
    assert jobs["monday-kpi-broadcast"].trigger.timezone.key == "UTC"
    assert str(jobs["monday-kpi-broadcast"].trigger.fields[5]) == "9"
    assert str(jobs["monday-kpi-broadcast"].trigger.fields[6]) == "0"
    assert str(jobs["weekday-focus-prompt"].trigger.fields[4]) == "tue-fri"
    assert str(jobs["friday-checkout"].trigger.fields[5]) == "15"
    assert jobs["weekday-focus-prompt"].max_instances == 1
    assert jobs["friday-checkout"].max_instances == 1
