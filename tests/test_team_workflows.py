from unittest.mock import AsyncMock

import pytest

from app.team.workflows import TeamWorkflowService


class FakeSheets:
    def __init__(self, records, headers=None):
        self.records = records
        self.headers = headers or {}
        self.update_record = AsyncMock(return_value=2)
        self.append_record = AsyncMock(return_value=2)

    async def read_records(self, worksheet):
        if worksheet not in self.records:
            raise RuntimeError(f"Unexpected worksheet: {worksheet}")
        return self.records[worksheet]

    async def read_headers(self, worksheet):
        if worksheet in self.headers:
            return self.headers[worksheet]
        rows = self.records.get(worksheet, [])
        if rows:
            return list(rows[0])
        raise RuntimeError(f"Missing header fixture for empty worksheet: {worksheet}")


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
        "Master Task Tracker": [
            {"Task ID": "T001", "Task Name": "Opportunity and Insight Page Update"}
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
async def test_status_and_goal_prefer_kpi_name_over_formula_helper_text():
    records = sample_records()
    records["KPI Register"][0]["KPI"] = "Qualified Leads"
    records["KPI Register"][0]["Definition / Measurement"] = (
        "Count the number of qualified leads accepted by the CRM each week."
    )
    records["KPI Register"][0]["KPI Label"] = (
        "1. Qualified Leads: Count the number of qualified leads accepted by the CRM each week."
    )
    sheets = FakeSheets(records)
    service = TeamWorkflowService(sheets, dry_run=True)

    status = await service.handle("/status", "", "123")
    preview = await service.handle("/goal", "Qualified Leads 15", "123")

    assert "Qualified Leads: 8" in status
    assert "Count the number" not in status
    assert preview == (
        "DRY RUN: I would update Qualified Leads target to 15. "
        "No spreadsheet changes were made."
    )
    sheets.update_record.assert_not_awaited()

@pytest.mark.asyncio
async def test_blocker_dry_run_validates_task_id_and_does_not_write():
    records = sample_records()
    headers = {
        "Blocker Log Worksheet": [
            "Blocker ID",
            "Task ID",
            "Person ID",
            "Full Name",
            "Description",
            "Reported At",
            "Status",
        ]
    }
    sheets = FakeSheets(records, headers=headers)
    service = TeamWorkflowService(
        sheets, blocker_worksheet="Blocker Log Worksheet", dry_run=True
    )

    response = await service.handle(
        "/blocker",
        "T001 waiting for the course card update",
        "123",
    )

    assert response == (
        "DRY RUN: I would record a blocker for task T001 for Ada Example. "
        "No spreadsheet changes were made."
    )
    sheets.append_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocker_rejects_unknown_task_id_without_writing():
    records = sample_records()
    headers = {
        "Blocker Log Worksheet": [
            "Blocker ID",
            "Task ID",
            "Person ID",
            "Full Name",
            "Description",
            "Reported At",
            "Status",
        ]
    }
    sheets = FakeSheets(records, headers=headers)
    service = TeamWorkflowService(
        sheets, blocker_worksheet="Blocker Log Worksheet", dry_run=False
    )

    response = await service.handle("/blocker", "T999 Waiting for review", "123")

    assert "couldn't find exactly one task with ID T999" in response
    sheets.append_record.assert_not_awaited()



@pytest.mark.asyncio
async def test_blocker_rejects_ambiguous_duplicate_task_ids():
    records = sample_records()
    records["Master Task Tracker"].append(
        {"Task ID": "T001", "Task Name": "Duplicate task ID"}
    )
    headers = {
        "Blocker Log Worksheet": [
            "Blocker ID",
            "Task ID",
            "Person ID",
            "Full Name",
            "Description",
            "Reported At",
            "Status",
        ]
    }
    sheets = FakeSheets(records, headers=headers)
    service = TeamWorkflowService(
        sheets, blocker_worksheet="Blocker Log Worksheet", dry_run=False
    )

    response = await service.handle("/blocker", "T001 Waiting for review", "123")

    assert "couldn't find exactly one task with ID T001" in response
    sheets.append_record.assert_not_awaited()

@pytest.mark.asyncio
async def test_blocker_live_write_stores_task_id_separately():
    records = sample_records()
    headers = {
        "Blocker Log Worksheet": [
            "Blocker ID",
            "Task ID",
            "Person ID",
            "Full Name",
            "Description",
            "Reported At",
            "Status",
        ]
    }
    sheets = FakeSheets(records, headers=headers)
    service = TeamWorkflowService(
        sheets, blocker_worksheet="Blocker Log Worksheet", dry_run=False
    )

    response = await service.handle(
        "/blocker",
        "T001 waiting for the course card update",
        "123",
    )

    assert response == "Blocker recorded for task T001 and marked Open."
    sheets.append_record.assert_awaited_once()
    worksheet, values = sheets.append_record.await_args.args
    assert worksheet == "Blocker Log Worksheet"
    assert values["Task ID"] == "T001"
    assert values["Description"] == "waiting for the course card update"
    assert values["Person ID"] == "P001"
    assert values["Status"] == "Open"
    assert values["Blocker ID"]
    assert values["Reported At"]


@pytest.mark.asyncio
async def test_blocker_requires_task_id_and_description():
    sheets = FakeSheets(sample_records())
    service = TeamWorkflowService(sheets, blocker_worksheet="Blocker Log Worksheet")

    response = await service.handle("/blocker", "Waiting for access", "123")

    assert response == "Usage: /blocker <Task ID> <describe the blocker>"
    sheets.append_record.assert_not_awaited()
