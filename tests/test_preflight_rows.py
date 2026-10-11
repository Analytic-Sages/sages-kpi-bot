import asyncio

from app.preflight_rows import row_integrity_findings


class FakeRowsClient:
    def __init__(self, records):
        self.records = records

    async def read_records(self, worksheet_name):
        return self.records[worksheet_name]


def valid_records():
    return {
        "People Directory": [
            {"Person ID": "P1", "Status": "Active", "Telegram Chat ID": "101"},
            {"Person ID": "P2", "Status": "Inactive", "Telegram Chat ID": ""},
        ],
        "KPI Register": [
            {"KPI ID": "K1", "Owner Person ID": "P1"},
        ],
        "Weekly KPI Log": [
            {"KPI ID": "K1", "Owner Person ID": "P1"},
        ],
        "Master Task Tracker": [
            {"Task ID": "T1", "Assigned To ID": "P1"},
        ],
    }


def test_row_integrity_passes_for_unique_ids_and_resolved_references():
    findings = asyncio.run(row_integrity_findings(FakeRowsClient(valid_records())))
    assert not any(f.severity == "BLOCKER" for f in findings)
    assert any(
        f.component == "People Directory / Telegram identity" and f.severity == "PASS"
        for f in findings
    )
    inventory = next(f for f in findings if f.component == "Team data inventory")
    assert "1 people" in inventory.message
    assert "P1" not in inventory.message


def test_row_integrity_blocks_duplicate_identity_and_broken_references():
    records = valid_records()
    records["People Directory"].append(
        {"Person ID": "P1", "Status": "Active", "Telegram Chat ID": "101"}
    )
    records["KPI Register"].append({"KPI ID": "K2", "Owner Person ID": "missing"})
    records["Weekly KPI Log"].append({"KPI ID": "missing", "Owner Person ID": "missing"})
    records["Master Task Tracker"].append({"Task ID": "T2", "Assigned To ID": "missing"})

    findings = asyncio.run(row_integrity_findings(FakeRowsClient(records)))
    assert any(
        f.severity == "BLOCKER" and "duplicated identifier" in f.message
        for f in findings
    )
    assert any(
        f.severity == "BLOCKER"
        and f.component == "People Directory / Telegram identity"
        for f in findings
    )
    assert any(
        f.severity == "BLOCKER" and f.component == "KPI Register / owner references"
        for f in findings
    )
    assert any(
        f.severity == "BLOCKER" and f.component == "Weekly KPI Log / references"
        for f in findings
    )
    assert any(
        f.severity == "BLOCKER"
        and f.component == "Master Task Tracker / assignee references"
        for f in findings
    )
    assert all("missing" not in f.message for f in findings)
