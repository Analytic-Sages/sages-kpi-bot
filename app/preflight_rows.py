"""Read-only integrity checks for team records in the operational workbook.

Reports aggregate counts only; never return names, identifiers, chat IDs, or row contents.
"""
from __future__ import annotations

from collections import Counter

from app.preflight import Finding
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError

_ACTIVE_VALUES = {"active", "current", "employed"}


def _values(rows: list[dict[str, str]], field: str) -> list[str]:
    return [row.get(field, "").strip() for row in rows]


def _duplicate_count(values: list[str]) -> int:
    counts = Counter(value for value in values if value)
    return sum(1 for count in counts.values() if count > 1)


async def row_integrity_findings(client: GoogleSheetsClient) -> list[Finding]:
    """Read team records and validate identifiers/references without exposing record values."""
    names = ("People Directory", "KPI Register", "Weekly KPI Log", "Master Task Tracker")
    try:
        people, kpis, logs, tasks = [
            await client.read_records(name) for name in names
        ]
    except GoogleSheetsError:
        return [
            Finding(
                "BLOCKER",
                "Team data integrity",
                "Could not read one or more core worksheets; row-level checks were not completed.",
            )
        ]

    findings: list[Finding] = []
    person_ids = _values(people, "Person ID")
    kpi_ids = _values(kpis, "KPI ID")
    task_ids = _values(tasks, "Task ID")
    known_person_ids = {value for value in person_ids if value}
    # Only configured KPI rows are valid targets for weekly logs;
    # preallocated template IDs are not KPIs.
    known_kpi_ids = {
        row.get("KPI ID", "").strip()
        for row in kpis
        if row.get("KPI", "").strip() and row.get("KPI ID", "").strip()
    }

    checks = (
        ("People Directory", "Person ID", person_ids),
        ("KPI Register", "KPI ID", kpi_ids),
        ("Master Task Tracker", "Task ID", task_ids),
    )
    for sheet, field, values in checks:
        blank_count = sum(not value for value in values)
        duplicate_count = _duplicate_count(values)
        if blank_count or duplicate_count:
            findings.append(
                Finding(
                    "BLOCKER",
                    f"{sheet} / {field}",
                    (
                        f"Found {blank_count} blank and {duplicate_count} duplicated "
                        "identifier value(s). Resolve these before enabling team workflows."
                    ),
                )
            )
        else:
            findings.append(
                Finding("PASS", f"{sheet} / {field}", "Nonblank identifiers are unique.")
            )

    active_people = [
        row for row in people
        if row.get("Status", "").strip().casefold() in _ACTIVE_VALUES
    ]
    active_chat_ids = _values(active_people, "Telegram Chat ID")
    blank_chat_count = sum(not value for value in active_chat_ids)
    duplicate_chat_count = _duplicate_count(active_chat_ids)
    if blank_chat_count or duplicate_chat_count:
        findings.append(
            Finding(
                "BLOCKER",
                "People Directory / Telegram identity",
                f"Among active people, {blank_chat_count} Telegram Chat ID value(s) are blank "
                f"and {duplicate_chat_count} value(s) are shared by multiple people. "
                "The current command handler requires an unambiguous chat-to-person match; "
                "verify identity mapping before enabling team workflows.",
            )
        )
    else:
        findings.append(
            Finding(
                "PASS",
                "People Directory / Telegram identity",
                "Active people have unique Telegram Chat ID values.",
            )
        )

    # KPI Register is preallocated with template rows (including generated KPI IDs).
    # A row becomes a real KPI only when its human-entered KPI field is populated.
    # Formula-driven Owner Person ID cells on unused template rows are expected to be blank.
    configured_kpis = [
        row for row in kpis if row.get("KPI", "").strip()
    ]
    kpi_setup_fields = (
        "Team",
        "Role",
        "Definition / Measurement",
        "Direction",
        "Frequency",
        "Target",
        "Unit",
        "Owner",
        "Data Source",
        "Active?",
    )
    incomplete_kpi_rows = sum(
        any(not row.get(field, "").strip() for field in kpi_setup_fields)
        for row in configured_kpis
    )
    if not configured_kpis:
        findings.append(
            Finding(
                "BLOCKER",
                "KPI Register / configuration readiness",
                "No configured KPIs found. Configure at least one real KPI before staging "
                "team workflows; unused template rows are ignored.",
            )
        )
    elif incomplete_kpi_rows:
        findings.append(
            Finding(
                "BLOCKER",
                "KPI Register / configuration readiness",
                f"{incomplete_kpi_rows} of {len(configured_kpis)} configured KPI row(s) "
                "have one or more blank required setup fields.",
            )
        )
    else:
        findings.append(
            Finding(
                "PASS",
                "KPI Register / configuration readiness",
                f"All required setup fields are populated for {len(configured_kpis)} "
                "configured KPI(s).",
            )
        )

    invalid_kpi_owners = sum(
        bool(row.get("Owner Person ID", "").strip())
        and row.get("Owner Person ID", "").strip() not in known_person_ids
        for row in configured_kpis
    )
    missing_kpi_owners = sum(
        not row.get("Owner Person ID", "").strip() for row in configured_kpis
    )
    if invalid_kpi_owners or missing_kpi_owners:
        findings.append(
            Finding(
                "BLOCKER",
                "KPI Register / owner references",
                (
                    f"Among {len(configured_kpis)} configured KPI(s), found "
                    f"{missing_kpi_owners} blank and {invalid_kpi_owners} "
                    "unknown owner reference(s)."
                ),
            )
        )
    else:
        findings.append(
            Finding(
                "PASS",
                "KPI Register / owner references",
                f"Owner references resolve for all {len(configured_kpis)} configured KPI(s).",
            )
        )

    unknown_log_kpis = sum(
        bool(row.get("KPI ID", "").strip())
        and row.get("KPI ID", "").strip() not in known_kpi_ids
        for row in logs
    )
    unknown_log_people = sum(
        bool(row.get("Owner Person ID", "").strip())
        and row.get("Owner Person ID", "").strip() not in known_person_ids
        for row in logs
    )
    if unknown_log_kpis or unknown_log_people:
        findings.append(
            Finding(
                "BLOCKER",
                "Weekly KPI Log / references",
                f"Found {unknown_log_kpis} unknown KPI reference(s) and "
                f"{unknown_log_people} unknown person reference(s).",
            )
        )
    else:
        findings.append(
            Finding("PASS", "Weekly KPI Log / references", "KPI and person references resolve.")
        )

    unknown_task_people = sum(
        bool(row.get("Assigned To ID", "").strip())
        and row.get("Assigned To ID", "").strip() not in known_person_ids
        for row in tasks
    )
    if unknown_task_people:
        findings.append(
            Finding(
                "BLOCKER",
                "Master Task Tracker / assignee references",
                (
                    f"Found {unknown_task_people} task assignee reference(s) that do not "
                    "resolve to a person."
                ),
            )
        )
    else:
        findings.append(
            Finding(
                "PASS",
                "Master Task Tracker / assignee references",
                "Nonblank task assignee references resolve.",
            )
        )

    findings.append(
        Finding(
            "INFO",
            "Team data inventory",
            f"Read {len(people)} people, {len(kpis)} KPI records, "
            f"{len(logs)} weekly KPI records, and {len(tasks)} task records. "
            "No record values are included in this report.",
        )
    )
    return findings
