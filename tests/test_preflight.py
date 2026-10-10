from app.config import Settings
from app.preflight import (
    ACCOUNTABILITY_SAFETY_HEADERS,
    CORE_SHEETS,
    Finding,
    configuration_findings,
    missing_headers,
    print_report,
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
