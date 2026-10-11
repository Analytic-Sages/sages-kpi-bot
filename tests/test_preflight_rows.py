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
            {
                "KPI ID": "K1",
                "Team": "Growth",
                "Role": "Analyst",
                "KPI": "Active KPI",
                "Definition / Measurement": "Qualified leads",
                "Direction": "Higher is better",
                "Frequency": "Weekly",
                "Target": "10",
                "Unit": "leads",
                "Owner": "Ada Example",
                "Owner Person ID": "P1",
                "Data Source": "CRM",
                "Active?": "TRUE",
            },
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
    assert "2 people" in inventory.message
    assert "P1" not in inventory.message


def test_row_integrity_blocks_duplicate_identity_and_broken_references():
    records = valid_records()
    records["People Directory"].append(
        {"Person ID": "P1", "Status": "Active", "Telegram Chat ID": "101"}
    )
    records["KPI Register"].append(
        {"KPI ID": "K2", "KPI": "Another KPI", "Owner Person ID": "missing"}
    )
    records["Weekly KPI Log"].append(
        {"KPI ID": "missing", "Owner Person ID": "missing"}
    )
    records["Master Task Tracker"].append(
        {"Task ID": "T2", "Assigned To ID": "missing"}
    )

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



def test_unused_preallocated_kpi_template_rows_do_not_require_formula_owner():
    records = valid_records()
    records["KPI Register"].extend(
        [
            {"KPI ID": "K2", "KPI": "", "Owner": "", "Owner Person ID": ""},
            {"KPI ID": "K3", "KPI": "", "Owner": "", "Owner Person ID": ""},
        ]
    )

    findings = asyncio.run(row_integrity_findings(FakeRowsClient(records)))
    owner_check = next(
        f for f in findings if f.component == "KPI Register / owner references"
    )
    assert owner_check.severity == "PASS"
    assert "1 configured KPI" in owner_check.message


def test_configured_kpi_with_blank_formula_owner_is_blocked():
    records = valid_records()
    records["KPI Register"].append(
        {"KPI ID": "K2", "KPI": "Another KPI", "Owner": "", "Owner Person ID": ""}
    )

    findings = asyncio.run(row_integrity_findings(FakeRowsClient(records)))
    owner_check = next(
        f for f in findings if f.component == "KPI Register / owner references"
    )
    assert owner_check.severity == "BLOCKER"
    assert "1 blank" in owner_check.message



def test_no_configured_kpis_blocks_staging_readiness():
    records = valid_records()
    records["KPI Register"] = [
        {"KPI ID": "K1", "KPI": "", "Owner Person ID": ""}
    ]

    findings = asyncio.run(row_integrity_findings(FakeRowsClient(records)))
    readiness = next(
        f for f in findings
        if f.component == "KPI Register / configuration readiness"
    )
    assert readiness.severity == "BLOCKER"
    assert "No configured KPIs" in readiness.message


def test_configured_kpi_with_missing_setup_field_is_blocked():
    records = valid_records()
    records["KPI Register"][0]["Target"] = ""

    findings = asyncio.run(row_integrity_findings(FakeRowsClient(records)))
    readiness = next(
        f for f in findings
        if f.component == "KPI Register / configuration readiness"
    )
    assert readiness.severity == "BLOCKER"
    assert "1 of 1 configured KPI" in readiness.message


def test_group_chat_ids_are_blocked_for_person_identity_mapping():
    records = valid_records()
    records["People Directory"][0]["Telegram Chat ID"] = "-500"

    findings = asyncio.run(row_integrity_findings(FakeRowsClient(records)))
    identity = next(
        f for f in findings
        if f.component == "People Directory / Telegram identity"
    )
    assert identity.severity == "BLOCKER"
    assert "positive private-chat IDs" in identity.message
