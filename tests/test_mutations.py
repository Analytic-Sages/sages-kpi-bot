import sqlite3

import pytest

from app.audit.store import SQLiteAuditLog
from app.sheets.mutations import (
    MutationOutcomeUncertain,
    MutationRejected,
    SafeSheetMutationService,
    SheetMutationPolicy,
    validate_nonempty_text,
    validate_numeric,
)


class FakeSheets:
    def __init__(self, records, headers=None):
        self.records = records
        self.headers = headers or {}
        self.update_record_calls = []
        self.read_header_calls = []
        self.read_record_calls = 0

    async def _headers(self, worksheet):
        self.read_header_calls.append(worksheet)
        return self.headers[worksheet]

    async def read_headers(self, worksheet):
        return await self._headers(worksheet)

    async def read_records(self, worksheet):
        self.read_record_calls += 1
        return [dict(row) for row in self.records[worksheet]]

    async def update_record(self, worksheet, *, key_header, key_value, values, **kwargs):
        self.update_record_calls.append((worksheet, key_header, key_value, values, kwargs))
        matches = [row for row in self.records[worksheet] if row[key_header] == key_value]
        if len(matches) != 1:
            raise RuntimeError("record not unique")
        row = matches[0]
        expected = kwargs.get("expected_values", {})
        if any(row.get(name, "") != value for name, value in expected.items()):
            raise RuntimeError("stale snapshot")
        row.update({key: str(value) for key, value in values.items()})
        return 2


class RecordingAudit:
    def __init__(self):
        self.events = []

    async def record(self, event):
        self.events.append(dict(event))


def make_service(
    tmp_path, *, dry_run=True, writes_enabled=False, bot_writes_enabled=False,
    records=None, audit=None
):
    records = records or {
        "KPI Register": [{"KPI ID": "K001", "Target": "10", "KPI": "Qualified Leads"}]
    }
    sheets = FakeSheets(records, {"KPI Register": ["KPI ID", "KPI", "Target"]})
    audit = audit or SQLiteAuditLog(str(tmp_path / "audit.sqlite3"))
    policy = SheetMutationPolicy(
        worksheet="KPI Register",
        key_header="KPI ID",
        editable_fields=frozenset({"Target"}),
        validators={"Target": validate_numeric},
    )
    service = SafeSheetMutationService(
        sheets,
        policies={"KPI Register": policy},
        audit=audit,
        dry_run=dry_run,
        writes_enabled=writes_enabled,
        bot_writes_enabled=bot_writes_enabled,
    )
    return service, sheets, audit


@pytest.mark.asyncio
async def test_mutation_preview_is_default_and_audited(tmp_path):
    service, sheets, audit = make_service(tmp_path, audit=RecordingAudit())
    result = await service.mutate(
        actor_id="admin@example.test",
        worksheet="KPI Register",
        record_id="K001",
        changes={"Target": "12"},
        expected_values={"Target": "10"},
        request_id="req-preview",
    )
    assert result.status == "preview"
    assert result.changes == {"Target": {"before": "10", "after": "12"}}
    assert not sheets.update_record_calls
    assert audit.events[-1]["outcome"] == "preview"


@pytest.mark.asyncio
async def test_mutation_rejects_unallowlisted_fields(tmp_path):
    service, sheets, _ = make_service(tmp_path)
    with pytest.raises(MutationRejected, match="not editable"):
        await service.mutate(
            actor_id="admin@example.test",
            worksheet="KPI Register",
            record_id="K001",
            changes={"Owner Person ID": "P002"},
            expected_values={"Owner Person ID": "P001"},
        )
    assert not sheets.update_record_calls


@pytest.mark.asyncio
async def test_mutation_rejects_stale_expected_values(tmp_path):
    service, sheets, audit = make_service(tmp_path, audit=RecordingAudit())
    with pytest.raises(MutationRejected, match="changed since it was loaded"):
        await service.mutate(
            actor_id="admin@example.test",
            worksheet="KPI Register",
            record_id="K001",
            changes={"Target": "12"},
            expected_values={"Target": "9"},
        )
    assert not sheets.update_record_calls
    assert audit.events[-1]["outcome"] == "conflict"


@pytest.mark.asyncio
async def test_mutation_rejects_duplicate_record_ids(tmp_path):
    records = {"KPI Register": [
        {"KPI ID": "K001", "Target": "10"},
        {"KPI ID": "K001", "Target": "20"},
    ]}
    service, sheets, _ = make_service(tmp_path, records=records)
    with pytest.raises(MutationRejected, match="found 2"):
        await service.mutate(
            actor_id="admin@example.test",
            worksheet="KPI Register",
            record_id="K001",
            changes={"Target": "12"},
            expected_values={"Target": "10"},
        )
    assert not sheets.update_record_calls


@pytest.mark.asyncio
async def test_mutation_live_path_verifies_and_audits(tmp_path):
    audit = RecordingAudit()
    service, sheets, _ = make_service(
        tmp_path, dry_run=False, writes_enabled=True, bot_writes_enabled=True, audit=audit
    )
    result = await service.mutate(
        actor_id="admin@example.test",
        worksheet="KPI Register",
        record_id="K001",
        changes={"Target": "12"},
        expected_values={"Target": "10"},
        request_id="req-live",
    )
    assert result.status == "applied"
    assert result.row_number == 2
    assert sheets.records["KPI Register"][0]["Target"] == "12"
    assert [event["outcome"] for event in audit.events] == ["started", "verified"]


@pytest.mark.asyncio
async def test_mutation_uncertain_write_is_not_reported_as_success(tmp_path):
    audit = RecordingAudit()
    service, sheets, _ = make_service(tmp_path, dry_run=False, writes_enabled=True, audit=audit)
    async def uncertain(*args, **kwargs):
        raise RuntimeError("connection lost after request")
    sheets.update_record = uncertain
    with pytest.raises(MutationOutcomeUncertain):
        await service.mutate(
            actor_id="admin@example.test",
            worksheet="KPI Register",
            record_id="K001",
            changes={"Target": "12"},
            expected_values={"Target": "10"},
        )
    assert [event["outcome"] for event in audit.events] == ["started", "uncertain"]


@pytest.mark.asyncio
async def test_formula_input_is_rejected():
    with pytest.raises(MutationRejected, match="formulas"):
        validate_nonempty_text('=IMPORTXML("https://example.test", "//x")')
    with pytest.raises(MutationRejected, match="formulas"):
        validate_numeric("=1+1")


@pytest.mark.asyncio
async def test_sqlite_audit_log_persists_events_across_instances(tmp_path):
    path = tmp_path / "audit.sqlite3"
    first = SQLiteAuditLog(str(path))
    event = {
        "request_id": "req-1",
        "actor_id": "admin@example.test",
        "action": "sheet_mutation",
        "worksheet": "KPI Register",
        "record_id": "K001",
        "changed_fields": ["Target"],
        "before": {"Target": "10"},
        "after": {"Target": "12"},
        "outcome": "verified",
    }
    await first.record(event)
    second = SQLiteAuditLog(str(path))
    await second.record({**event, "request_id": "req-2"})
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT request_id, outcome FROM audit_events ORDER BY request_id"
        ).fetchall()
    assert rows == [("req-1", "verified"), ("req-2", "verified")]



@pytest.mark.asyncio
async def test_audit_failure_before_write_prevents_mutation(tmp_path):
    class BrokenAudit:
        async def record(self, event):
            raise OSError("audit storage unavailable")

    service, sheets, _ = make_service(
        tmp_path, dry_run=False, writes_enabled=True, bot_writes_enabled=True, audit=BrokenAudit()
    )
    with pytest.raises(OSError, match="audit storage unavailable"):
        await service.mutate(
            actor_id="admin@example.test",
            worksheet="KPI Register",
            record_id="K001",
            changes={"Target": "12"},
            expected_values={"Target": "10"},
        )
    assert not sheets.update_record_calls


@pytest.mark.asyncio
async def test_write_flag_still_blocks_mutation_when_dry_run_is_false(tmp_path):
    service, sheets, _ = make_service(
        tmp_path, dry_run=False, writes_enabled=False, audit=RecordingAudit()
    )
    result = await service.mutate(
        actor_id="admin@example.test",
        worksheet="KPI Register",
        record_id="K001",
        changes={"Target": "12"},
        expected_values={"Target": "10"},
    )
    assert result.status == "preview"
    assert not sheets.update_record_calls
