from app.preflight import (
    ACCOUNTABILITY_SAFETY_HEADERS,
    CORE_SHEETS,
    Finding,
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
