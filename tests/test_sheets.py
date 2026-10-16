from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError, google_auth_mode
from app.sheets.schema import SheetSchemaError, normalize_headers, rows_from_values


def test_normalize_headers_trims_and_removes_trailing_empty_cells():
    assert normalize_headers([" Task ID ", " Task Name ", ""]) == ["Task ID", "Task Name"]


@pytest.mark.parametrize(
    "headers",
    [[], ["Task ID", "", "Status"], ["Task ID", "task id"]],
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
        "Master Task Tracker",
        key_header="Task ID",
        key_value="T001",
        values={"Status": "Completed"},
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
        "Master Task Tracker",
        {"Task ID": "T002", "Status": "Not Started", "Task Name": "Write brief"},
    )
    assert row == 2
    worksheet.append_row.assert_called_once_with(
        ["Write brief", "T002", "Not Started"], value_input_option="USER_ENTERED"
    )



def test_google_auth_mode_accepts_complete_oauth_configuration():
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
        google_oauth_refresh_token="refresh-token",
    )
    assert google_auth_mode(settings) == "oauth"


def test_google_auth_mode_rejects_partial_oauth_instead_of_falling_back(tmp_path):
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_creds_path=str(credentials_file),
        google_oauth_client_id="client-id",
    )
    with pytest.raises(GoogleSheetsError, match="configured together"):
        google_auth_mode(settings)


def test_google_auth_mode_accepts_supported_credentials_file(tmp_path):
    credentials_file = tmp_path / "external-account.json"
    credentials_file.write_text("{}", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_creds_path=str(credentials_file),
    )
    assert google_auth_mode(settings) == "file"


def test_from_settings_builds_oauth_credentials_without_private_key(monkeypatch):
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
        google_oauth_refresh_token="refresh-token",
    )
    credentials_factory = MagicMock(return_value=MagicMock(name="oauth-credentials"))
    authorized_client = MagicMock()
    spreadsheet = MagicMock(name="spreadsheet")
    authorized_client.open_by_key.return_value = spreadsheet
    monkeypatch.setattr("app.sheets.client.OAuthCredentials", credentials_factory)
    monkeypatch.setattr(
        "app.sheets.client.gspread.authorize", lambda credentials: authorized_client
    )

    client = GoogleSheetsClient.from_settings(settings)

    credentials_factory.assert_called_once_with(
        token=None,
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    authorized_client.open_by_key.assert_called_once_with("sheet-id")
    assert client._spreadsheet is spreadsheet


def test_from_settings_loads_external_account_or_service_account_file(monkeypatch, tmp_path):
    credentials_file = tmp_path / "google-credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_creds_path=str(credentials_file),
    )
    credentials = MagicMock(name="file-credentials")
    authorized_client = MagicMock()
    spreadsheet = MagicMock(name="spreadsheet")
    authorized_client.open_by_key.return_value = spreadsheet
    loader = MagicMock(return_value=(credentials, "project-id"))
    monkeypatch.setattr("app.sheets.client.google.auth.load_credentials_from_file", loader)
    monkeypatch.setattr("app.sheets.client.gspread.authorize", lambda value: authorized_client)

    client = GoogleSheetsClient.from_settings(settings)

    loader.assert_called_once_with(
        str(credentials_file),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    authorized_client.open_by_key.assert_called_once_with("sheet-id")
    assert client._spreadsheet is spreadsheet


@pytest.mark.asyncio
async def test_update_record_rejects_stale_expected_value_before_writing():
    client, worksheet = make_client([
        ["Task ID", "Status"],
        ["T001", "In Progress"],
    ])
    with pytest.raises(GoogleSheetsError, match="changed since it was loaded"):
        await client.update_record(
            "Master Task Tracker",
            key_header="Task ID",
            key_value="T001",
            values={"Status": "Completed"},
            expected_values={"Status": "Not Started"},
            value_input_option="RAW",
        )
    worksheet.batch_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_record_supports_raw_values_with_expected_snapshot():
    client, worksheet = make_client([
        ["Task ID", "Status"],
        ["T001", "In Progress"],
    ])
    row = await client.update_record(
        "Master Task Tracker",
        key_header="Task ID",
        key_value="T001",
        values={"Status": "Completed"},
        expected_values={"Status": "In Progress"},
        value_input_option="RAW",
    )
    assert row == 2
    worksheet.batch_update.assert_called_once_with(
        [{"range": "B2", "values": [["Completed"]]}],
        value_input_option="RAW",
    )
