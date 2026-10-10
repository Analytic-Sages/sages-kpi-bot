from unittest.mock import MagicMock

import pytest

from app.sheets.client import GoogleSheetsClient, GoogleSheetsError
from app.sheets.schema import SheetSchemaError, normalize_headers, rows_from_values


def test_normalize_headers_trims_and_removes_trailing_empty_cells():
    assert normalize_headers([" Task ID ", " Task Name ", ""]) == ["Task ID", "Task Name"]


@pytest.mark.parametrize(
    "headers",
    [[], ["Task ID", ""], ["Task ID", "task id"]],
)
def test_normalize_headers_rejects_unsafe_headers(headers):
    with pytest.raises(SheetSchemaError):
        normalize_headers(headers)


def test_rows_are_keyed_by_headers_and_keep_actual_sheet_row_number():
    rows = rows_from_values([
        ["Task ID", "Task Name", "Status"],
        ["T001", "Publish report", "In Progress"],
        ["", "", ""],
        ["T002", "", "Completed"],
    ])
    assert rows[0].row_number == 2
    assert rows[0].values == {
        "Task ID": "T001", "Task Name": "Publish report", "Status": "In Progress"
    }
    assert rows[1].row_number == 4
    assert rows[1].values["Task ID"] == "T002"


def make_client(grid):
    worksheet = MagicMock()
    worksheet.get_all_values.return_value = grid
    spreadsheet = MagicMock()
    spreadsheet.worksheet.return_value = worksheet
    return GoogleSheetsClient(spreadsheet), worksheet


@pytest.mark.asyncio
async def test_update_record_uses_header_position_not_fixed_column_number():
    client, worksheet = make_client([
        ["Task Name", "Status", "Task ID"],
        ["Publish report", "In Progress", "T001"],
    ])
    row = await client.update_record(
        "Master Task Tracker", key_header="Task ID", key_value="T001", values={"Status": "Completed"}
    )
    assert row == 2
    worksheet.batch_update.assert_called_once_with(
        [{"range": "B2", "values": [["Completed"]]}], value_input_option="USER_ENTERED"
    )


@pytest.mark.asyncio
async def test_update_record_rejects_duplicate_stable_ids():
    client, _ = make_client([
        ["Person ID", "Name"], ["P001", "First"], ["P001", "Duplicate"]
    ])
    with pytest.raises(GoogleSheetsError, match="found 2"):
        await client.update_record(
            "People Directory", key_header="Person ID", key_value="P001", values={"Name": "New"}
        )


@pytest.mark.asyncio
async def test_writes_reject_unknown_columns():
    client, _ = make_client([["Task ID", "Task Name"], ["T001", "Work"]])
    with pytest.raises(GoogleSheetsError, match="Unknown worksheet columns"):
        await client.append_record("Master Task Tracker", {"Owner Chat ID": "123"})


@pytest.mark.asyncio
async def test_append_record_aligns_values_to_headers():
    client, worksheet = make_client([["Task Name", "Task ID", "Status"]])
    row = await client.append_record(
        "Master Task Tracker", {"Task ID": "T002", "Status": "Not Started", "Task Name": "Write brief"}
    )
    assert row == 2
    worksheet.append_row.assert_called_once_with(
        ["Write brief", "T002", "Not Started"], value_input_option="USER_ENTERED"
    )
