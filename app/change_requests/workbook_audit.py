"""Read-only audit of proposed mutation headers and formula ownership."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from app.change_requests.policy import (
    FIELD_TIERS,
    KPI_WORKSHEET,
    TASK_WORKSHEET,
    WEEKLY_KPI_WORKSHEET,
)
from app.sheets.client import GoogleSheetsError
from app.sheets.schema import SheetSchemaError, normalize_headers

# These are formula-owned or otherwise protected by the Phase 2 policy contract.
# This list is a proposed contract, not proof of the live workbook's configuration.
PROTECTED_FORMULA_FIELDS: dict[str, frozenset[str]] = {
    TASK_WORKSHEET: frozenset(
        {
            "Status",
            "Assigned To ID",
            "Days To Deadline",
            "Remind Now?",
            "Escalate?",
            "Achievement %",
        }
    ),
    KPI_WORKSHEET: frozenset({"Achievement %"}),
    WEEKLY_KPI_WORKSHEET: frozenset({"Target", "Achievement %"}),
}
RECORD_KEYS = {
    TASK_WORKSHEET: ("Task ID",),
    KPI_WORKSHEET: ("KPI ID",),
    WEEKLY_KPI_WORKSHEET: ("Week Start", "KPI ID", "Owner Person ID"),
}
EXTRA_REQUIRED_FIELDS = {
    TASK_WORKSHEET: frozenset({"Date Assigned"}),
    KPI_WORKSHEET: frozenset({"Owner Person ID", "Active?"}),
    WEEKLY_KPI_WORKSHEET: frozenset({"Owner Person ID"}),
}


class FormulaAuditClient(Protocol):
    async def read_headers(self, worksheet_name: str) -> list[str]: ...

    async def read_formula_rows(self, worksheet_name: str) -> list[dict[str, str]]: ...


@dataclass(frozen=True, slots=True)
class WorkbookAuditFinding:
    severity: str
    component: str
    message: str


async def change_policy_findings(client: FormulaAuditClient) -> list[WorkbookAuditFinding]:
    """Check proposed writable headers and formula cells without returning cell contents."""
    findings: list[WorkbookAuditFinding] = []
    for worksheet, editable_fields in FIELD_TIERS.items():
        component = f"{worksheet} / mutation policy"
        try:
            raw_headers = await client.read_headers(worksheet)
            headers = normalize_headers(raw_headers)
            formula_rows = await client.read_formula_rows(worksheet)
        except (GoogleSheetsError, SheetSchemaError, ValueError, AttributeError):
            findings.append(
                WorkbookAuditFinding(
                    "BLOCKER",
                    component,
                    "Could not safely inspect headers and formula-visible cells.",
                )
            )
            continue

        header_set = set(headers)
        required = (
            set(editable_fields)
            | set(RECORD_KEYS[worksheet])
            | set(EXTRA_REQUIRED_FIELDS[worksheet])
            | set(PROTECTED_FORMULA_FIELDS.get(worksheet, frozenset()))
        )
        missing = sorted(required - header_set)
        if missing:
            findings.append(
                WorkbookAuditFinding(
                    "BLOCKER",
                    component,
                    "Proposed policy headers missing: " + ", ".join(missing) + ".",
                )
            )
        else:
            findings.append(
                WorkbookAuditFinding(
                    "PASS",
                    component,
                    "All proposed policy and stable-key headers are present.",
                )
            )

        formula_counts: Counter[str] = Counter()
        for row in formula_rows:
            for field in required:
                value = str(row.get(field, "")).strip()
                if value.startswith("="):
                    formula_counts[field] += 1

        protected = PROTECTED_FORMULA_FIELDS.get(worksheet, frozenset())
        for field in sorted(protected):
            if field not in header_set:
                continue
            count = formula_counts[field]
            if count:
                findings.append(
                    WorkbookAuditFinding(
                        "PASS",
                        f"{worksheet} / formula ownership",
                        f"{field}: formula detected in {count} row(s); "
                        "keep this field non-writable.",
                    )
                )
            else:
                findings.append(
                    WorkbookAuditFinding(
                        "WARNING",
                        f"{worksheet} / formula ownership",
                        f"{field}: no formula detected in inspected rows; "
                        "ownership still requires manual confirmation.",
                    )
                )

        for field in sorted(set(editable_fields) & header_set):
            count = formula_counts[field]
            if count:
                findings.append(
                    WorkbookAuditFinding(
                        "BLOCKER",
                        f"{worksheet} / writable field",
                        f"{field}: formula detected in {count} row(s); field must remain disabled.",
                    )
                )

        if not formula_rows:
            findings.append(
                WorkbookAuditFinding(
                    "WARNING",
                    f"{worksheet} / formula ownership",
                    "No data rows were available to inspect; formula ownership is unverified.",
                )
            )

    findings.append(
        WorkbookAuditFinding(
            "WARNING",
            "Mutation policy",
            "This audit is read-only. Passing checks does not enable writes "
            "or replace owner approval.",
        )
    )
    return findings
