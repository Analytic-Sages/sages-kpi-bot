import pytest

from app.change_requests.policy import (
    KPI_WORKSHEET,
    TASK_WORKSHEET,
    WEEKLY_KPI_WORKSHEET,
    FIELD_TIERS,
)
from app.change_requests.workbook_audit import (
    EXTRA_REQUIRED_FIELDS,
    PROTECTED_FORMULA_FIELDS,
    RECORD_KEYS,
    change_policy_findings,
)


class FakeFormulaClient:
    def __init__(self, headers, rows):
        self.headers = headers
        self.rows = rows

    async def read_headers(self, worksheet_name):
        return self.headers[worksheet_name]

    async def read_formula_rows(self, worksheet_name):
        return self.rows[worksheet_name]


def valid_headers():
    result = {}
    for worksheet, fields in FIELD_TIERS.items():
        result[worksheet] = sorted(
            set(fields)
            | set(RECORD_KEYS[worksheet])
            | set(EXTRA_REQUIRED_FIELDS[worksheet])
            | set(PROTECTED_FORMULA_FIELDS.get(worksheet, frozenset()))
        )
    return result


def empty_rows():
    return {worksheet: [] for worksheet in FIELD_TIERS}


@pytest.mark.asyncio
async def test_schema_audit_passes_required_headers_but_warns_without_rows():
    client = FakeFormulaClient(valid_headers(), empty_rows())
    findings = await change_policy_findings(client)
    assert any(
        item.severity == "PASS"
        and item.component == f"{TASK_WORKSHEET} / mutation policy"
        for item in findings
    )
    assert any(
        item.severity == "WARNING"
        and item.component == f"{TASK_WORKSHEET} / formula ownership"
        and "No data rows" in item.message
        for item in findings
    )


@pytest.mark.asyncio
async def test_missing_proposed_header_is_blocker():
    headers = valid_headers()
    headers[TASK_WORKSHEET].remove("Deadline")
    client = FakeFormulaClient(headers, empty_rows())
    findings = await change_policy_findings(client)
    assert any(
        item.severity == "BLOCKER" and "Deadline" in item.message
        for item in findings
    )


@pytest.mark.asyncio
async def test_formula_in_writable_field_blocks_that_field():
    headers = valid_headers()
    rows = empty_rows()
    rows[TASK_WORKSHEET] = [
        {"Task ID": "T001", "Status": "In Progress", "% Complete": "=40"}
    ]
    client = FakeFormulaClient(headers, rows)
    findings = await change_policy_findings(client)
    assert any(
        item.severity == "BLOCKER"
        and item.component == f"{TASK_WORKSHEET} / writable field"
        and "% Complete" in item.message
        for item in findings
    )


@pytest.mark.asyncio
async def test_formula_derived_status_is_not_accepted_as_writable():
    headers = valid_headers()
    rows = empty_rows()
    rows[TASK_WORKSHEET] = [{"Task ID": "T001", "Status": '=IF(A2="","", "Completed")'}]
    client = FakeFormulaClient(headers, rows)
    findings = await change_policy_findings(client)
    assert any(
        item.severity == "BLOCKER"
        and item.component == f"{TASK_WORKSHEET} / writable field"
        and "Status" in item.message
        for item in findings
    )


@pytest.mark.asyncio
async def test_formula_in_protected_column_is_reported_without_exposing_formula():
    headers = valid_headers()
    rows = empty_rows()
    rows[TASK_WORKSHEET] = [
        {"Task ID": "T001", "Assigned To ID": '=VLOOKUP(A2,People,2,FALSE)'}
    ]
    client = FakeFormulaClient(headers, rows)
    findings = await change_policy_findings(client)
    assert any(
        item.severity == "PASS"
        and item.component == f"{TASK_WORKSHEET} / formula ownership"
        and "Assigned To ID: formula detected in 1 row(s)" in item.message
        for item in findings
    )
    assert all("VLOOKUP" not in item.message for item in findings)


@pytest.mark.asyncio
async def test_unknown_worksheet_schema_fails_closed():
    class BrokenClient:
        async def read_headers(self, worksheet_name):
            raise RuntimeError("private workbook detail")

        async def read_formula_rows(self, worksheet_name):
            raise AssertionError("must not continue after header read failure")

    findings = await change_policy_findings(BrokenClient())
    assert len([item for item in findings if item.severity == "BLOCKER"]) == 3
    assert all("private workbook detail" not in item.message for item in findings)
