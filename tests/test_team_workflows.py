from unittest.mock import AsyncMock

import pytest

from app.team.workflows import TeamWorkflowService


class FakeSheets:
    def __init__(self, records):
        self.records = records
        self.update_record = AsyncMock(return_value=2)
        self.append_record = AsyncMock(return_value=2)

    async def read_records(self, worksheet):
        if worksheet not in self.records:
            raise RuntimeError(f"Unexpected worksheet: {worksheet}")
        return self.records[worksheet]


def sample_records():
    return {
        "People Directory": [
            {
                "Person ID": "P001",
                "Full Name": "Ada Example",
                "Role": "Data Analyst",
                "Status": "Active",
                "Telegram Chat ID": "123",
                "Notes": "Not a job description",
            }
        ],
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
            {"Week Start": "2026-09-28", "KPI ID": "K001", "Actual": "7", "Status": "Behind"},
            {"Week Start": "2026-10-05", "KPI ID": "K001", "Actual": "8", "Status": "On Track"},
        ],
    }


@pytest.mark.asyncio
async def test_status_shows_latest_kpi_observation_for_authenticated_person():
    sheets = FakeSheets(sample_records())
    response = await TeamWorkflowService(sheets).handle("/status", "", 123)
    assert "Ada Example" in response
    assert "Qualified Leads: 8" in response
    assert "target 10; On Track" in response


@pytest.mark.asyncio
async def test_goal_updates_only_a_unique_active_kpi_owned_by_requester():
    sheets = FakeSheets(sample_records())
    response = await TeamWorkflowService(sheets, dry_run=False).handle(
        "/goal", "Qualified Leads 15", "123"
    )
    assert response == "Target updated for Qualified Leads: 15."
    sheets.update_record.assert_awaited_once_with(
        "KPI Register",
        key_header="KPI ID",
        key_value="K001",
        values={"Target": "15"},
    )


@pytest.mark.asyncio
async def test_goal_requires_numeric_target_and_does_not_write_invalid_value():
    sheets = FakeSheets(sample_records())
    response = await TeamWorkflowService(sheets).handle("/goal", "Qualified Leads many", "123")
    assert "must be numeric" in response
    sheets.update_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_jd_does_not_fall_back_to_people_notes():
    sheets = FakeSheets(sample_records())
    response = await TeamWorkflowService(sheets).handle("/jd", "", "123")
    assert "authoritative Job Descriptions source has not been configured" in response
    assert "Not a job description" not in response


@pytest.mark.asyncio
async def test_blocker_is_not_claimed_saved_without_configured_destination():
    sheets = FakeSheets(sample_records())
    response = await TeamWorkflowService(sheets).handle("/blocker", "Waiting for access", "123")
    assert "was not stored" in response
    sheets.append_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_telegram_identity_header_fails_closed():
    records = sample_records()
    records["People Directory"] = [
        {
            "Person ID": "P001",
            "Full Name": "Ada Example",
            "Role": "Data Analyst",
            "Status": "Active",
        }
    ]
    sheets = FakeSheets(records)
    response = await TeamWorkflowService(sheets).handle("/status", "", "123")
    assert "couldn't safely complete" in response


@pytest.mark.asyncio
async def test_status_and_goal_prefer_concise_kpi_label_over_definition():
    records = sample_records()
    records["KPI Register"][0]["KPI Label"] = "Qualified Leads"
    records["KPI Register"][0]["KPI"] = (
        "Count the number of qualified leads accepted by the CRM each week."
    )
    sheets = FakeSheets(records)
    service = TeamWorkflowService(sheets, dry_run=True)

    status = await service.handle("/status", "", "123")
    preview = await service.handle("/goal", "Qualified Leads 15", "123")

    assert "Qualified Leads: 8" in status
    assert "Count the number" not in status
    assert "DRY RUN: I would update Qualified Leads target to 15" == preview
    sheets.update_record.assert_not_awaited()
