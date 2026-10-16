import sqlite3

import pytest

from app.audit.store import SQLiteAuditLog


@pytest.mark.asyncio
async def test_audit_log_creates_parent_and_persists_event(tmp_path):
    path = tmp_path / "nested" / "audit.sqlite3"
    audit = SQLiteAuditLog(str(path))
    await audit.record({
        "request_id": "request-1",
        "actor_id": "admin-1",
        "action": "sheet_mutation",
        "worksheet": "KPI Register",
        "record_id": "K001",
        "changed_fields": ["Target"],
        "before": {"Target": "10"},
        "after": {"Target": "12"},
        "outcome": "preview",
    })
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT actor_id, worksheet, record_id, outcome FROM audit_events"
        ).fetchone()
    assert row == ("admin-1", "KPI Register", "K001", "preview")
