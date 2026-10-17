import asyncio

from app.config import Settings
from app.preflight import (
    ACCOUNTABILITY_SAFETY_HEADERS,
    CORE_SHEETS,
    Finding,
    configuration_findings,
    missing_headers,
    print_report,
    sheet_findings,
)


def test_missing_headers_are_sorted_and_do_not_mutate_inputs():
    actual = {"Task ID", "Status"}
    required = {"Status", "Task Name", "Assigned To ID"}
    assert missing_headers(actual, required) == ["Assigned To ID", "Task Name"]
    assert actual == {"Task ID", "Status"}


def test_preflight_schema_expectations_cover_core_sheets_and_safety_flags():
    expected_sheets = {"People Directory", "KPI Register", "Weekly KPI Log", "Master Task Tracker"}
    assert expected_sheets <= set(CORE_SHEETS)
    assert "Notify?" in ACCOUNTABILITY_SAFETY_HEADERS["People Directory"]
    assert "Reachable?" in ACCOUNTABILITY_SAFETY_HEADERS["People Directory"]
    assert {"Remind Now?", "Escalate?"} <= ACCOUNTABILITY_SAFETY_HEADERS["Master Task Tracker"]


def test_report_returns_failure_when_blockers_exist(capsys):
    result = print_report([Finding("BLOCKER", "Sheets", "Missing header.")])
    output = capsys.readouterr().out
    assert result == 1
    assert "NOT READY" in output


def test_report_returns_zero_without_blockers(capsys):
    result = print_report([Finding("PASS", "Sheets", "Headers present.")])
    output = capsys.readouterr().out
    assert result == 0
    assert "CONFIGURATION CHECK PASSED" in output



def test_preflight_accepts_oauth_configuration_without_service_account_file():
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
        google_oauth_refresh_token="refresh-token",
    )
    findings = configuration_findings(settings)
    google_blockers = [
        finding
        for finding in findings
        if finding.component == "Google Sheets" and finding.severity == "BLOCKER"
    ]
    assert google_blockers == []
    assert any(
        finding.component == "Google Sheets"
        and finding.severity == "INFO"
        and "OAuth refresh-token" in finding.message
        for finding in findings
    )


def test_preflight_flags_incomplete_oauth_configuration():
    settings = Settings(
        _env_file=None,
        google_sheet_id="sheet-id",
        google_oauth_client_id="client-id",
    )
    findings = configuration_findings(settings)
    assert any(
        finding.component == "Google Sheets"
        and finding.severity == "BLOCKER"
        and "configured together" in finding.message
        for finding in findings
    )


def test_sheet_findings_accepts_header_only_worksheets():
    from app.change_requests.policy import FIELD_TIERS
    from app.change_requests.workbook_audit import (
        EXTRA_REQUIRED_FIELDS,
        PROTECTED_FORMULA_FIELDS,
        RECORD_KEYS,
    )

    headers_by_sheet = {name: set(headers) for name, headers in CORE_SHEETS.items()}
    for name, headers in ACCOUNTABILITY_SAFETY_HEADERS.items():
        headers_by_sheet[name].update(headers)
    for name, fields in FIELD_TIERS.items():
        headers_by_sheet[name].update(fields)
        headers_by_sheet[name].update(RECORD_KEYS[name])
        headers_by_sheet[name].update(EXTRA_REQUIRED_FIELDS[name])
        headers_by_sheet[name].update(PROTECTED_FORMULA_FIELDS.get(name, frozenset()))

    class HeaderOnlyClient:
        async def read_headers(self, worksheet_name):
            # Models a worksheet that has a valid row 1 and no data rows.
            return sorted(headers_by_sheet[worksheet_name])

        async def read_formula_rows(self, worksheet_name):
            return []

    findings = asyncio.run(sheet_findings(HeaderOnlyClient()))
    weekly_log = next(f for f in findings if f.component == "Weekly KPI Log")
    assert weekly_log.severity == "PASS"
    assert weekly_log.message == "Core headers are present."
    assert not any(
        f.severity == "BLOCKER" and f.component == "Weekly KPI Log"
        for f in findings
    )

