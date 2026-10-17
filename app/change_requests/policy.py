"""Typed, fail-closed validation for proposed Polaris task/KPI changes.

This module validates proposals only. It deliberately does not read or write Google Sheets.
Worksheet and field names mirror the proposed Phase 2 contract and must not be activated
until the live workbook headers and formula ownership are verified.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

RecordKind = Literal["task", "kpi", "weekly_kpi"]

TASK_WORKSHEET = "Master Task Tracker"
KPI_WORKSHEET = "KPI Register"
WEEKLY_KPI_WORKSHEET = "Weekly KPI Log"

# Proposed policies only. No worksheet policy is enabled or wired to the mutation service.
FIELD_TIERS: dict[str, dict[str, int]] = {
    TASK_WORKSHEET: {
        "Status": 1,
        "% Complete": 1,
        "Remarks / Blocker": 1,
        "Output / Link": 1,
        "Completed Date": 1,
        "Deadline": 2,
        "Assigned To": 2,
        "Priority": 2,
    },
    KPI_WORKSHEET: {"Target": 2},
    WEEKLY_KPI_WORKSHEET: {
        "Actual": 1,
        "Comment": 1,
        "Next Action": 1,
        "Target Override": 2,
    },
}
RECORD_WORKSHEETS = {
    "task": TASK_WORKSHEET,
    "kpi": KPI_WORKSHEET,
    "weekly_kpi": WEEKLY_KPI_WORKSHEET,
}
NUMERIC_FIELDS = {"% Complete", "Actual", "Target", "Target Override"}
TEXT_FIELDS = {"Remarks / Blocker", "Output / Link", "Comment", "Next Action"}
ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "Not Started": frozenset({"In Progress", "Blocked", "On Hold", "Cancelled"}),
    "In Progress": frozenset({"In Review", "Blocked", "On Hold", "Cancelled", "Completed"}),
    "In Review": frozenset({"In Progress", "Blocked", "Completed"}),
    "Blocked": frozenset({"In Progress", "Cancelled"}),
    "On Hold": frozenset({"In Progress", "Cancelled"}),
    "Completed": frozenset({"Closed"}),
    "Cancelled": frozenset({"Closed"}),
    "Closed": frozenset({"In Progress"}),
}
MANAGER_ROLES = frozenset({"team_lead", "operations_manager"})


class ChangePolicyError(ValueError):
    """Raised when a proposed change is invalid, ambiguous, or unauthorized."""


@dataclass(frozen=True, slots=True)
class ChangeProposal:
    """Typed request payload stored in the durable change-request state machine."""

    record_kind: RecordKind
    worksheet: str
    record_id: str
    actor_id: str
    tier: int
    changes: dict[str, str]
    expected_values: dict[str, str]
    row_fingerprint: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Trusted facts resolved server-side from current workbook/People Directory data."""

    actor_id: str
    actor_active: bool
    matching_record_count: int
    headers: tuple[str, ...]
    current_row: dict[str, str]
    current_formula_fields: frozenset[str] = frozenset()
    owner_id: str | None = None
    assigned_to_id: str | None = None
    assigned_by_id: str | None = None
    actor_roles: frozenset[str] = frozenset()
    known_active_person_ids: frozenset[str] = frozenset()
    known_active_assignee_values: frozenset[str] = frozenset()
    # False until live workbook verification identifies the authoritative status cell.
    status_is_authoritative: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedChange:
    """Normalized proposal suitable for preview; not an authorization to write."""

    worksheet: str
    record_id: str
    actor_id: str
    tier: int
    changes: dict[str, str]
    expected_values: dict[str, str]
    current_fingerprint: str
    reason: str


def fingerprint_row(headers: tuple[str, ...] | list[str], row: dict[str, str]) -> str:
    """Hash a stable, header-ordered representation of a row snapshot."""
    normalized_headers = tuple(str(header).strip() for header in headers)
    snapshot = {
        header: str(row.get(header, "")).strip()
        for header in normalized_headers
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_headers(headers: tuple[str, ...]) -> None:
    if not headers:
        raise ChangePolicyError("Worksheet headers are unavailable")
    normalized = [header.strip() for header in headers]
    if any(not header for header in normalized):
        raise ChangePolicyError("Blank worksheet headers fail closed")
    folded = [header.casefold() for header in normalized]
    if len(folded) != len(set(folded)):
        raise ChangePolicyError("Duplicate worksheet headers fail closed")


def _text_value(field: str, value: str) -> str:
    normalized = str(value).strip()
    if field in TEXT_FIELDS and normalized and normalized[0] in "=+-@":
        raise ChangePolicyError(f"{field} contains a formula-injection prefix")
    if "\x00" in normalized:
        raise ChangePolicyError(f"{field} contains a null byte")
    return normalized


def _number(field: str, value: str) -> Decimal:
    try:
        number = Decimal(value.replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ChangePolicyError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise ChangePolicyError(f"{field} must be finite")
    if field == "% Complete" and (
        number != number.to_integral_value() or number < 0 or number > 100
    ):
        raise ChangePolicyError("% Complete must be an integer from 0 through 100")
    if field in {"Target", "Target Override"} and number <= 0:
        raise ChangePolicyError(f"{field} must be greater than zero")
    return number


def _validate_status(current: str, requested: str, tier: int, context: PolicyContext) -> None:
    if not context.status_is_authoritative:
        raise ChangePolicyError(
            "Status changes are disabled until the live authoritative status cell is verified"
        )
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current)
    if allowed is None or requested not in allowed:
        raise ChangePolicyError(f"Status transition {current!r} -> {requested!r} is not allowed")
    if requested == "Completed" and tier != 2:
        raise ChangePolicyError("Moving a task to Completed requires Tier 2 approval")
    if current == "Closed" and (requested != "In Progress" or tier != 2):
        raise ChangePolicyError("Reopening a Closed task requires Tier 2 approval")


def validate_proposal(
    proposal: ChangeProposal,
    context: PolicyContext,
) -> ValidatedChange:
    """Validate a typed proposal against trusted row/identity facts; never performs I/O."""
    if proposal.record_kind not in RECORD_WORKSHEETS:
        raise ChangePolicyError("Unknown record type")
    expected_worksheet = RECORD_WORKSHEETS[proposal.record_kind]
    if proposal.worksheet != expected_worksheet:
        raise ChangePolicyError("Record type and worksheet do not match")
    if not proposal.record_id.strip():
        raise ChangePolicyError("A stable record ID is required")
    if context.matching_record_count != 1:
        raise ChangePolicyError(
            f"Expected exactly one record ID match; found {context.matching_record_count}"
        )
    if not proposal.actor_id.strip() or proposal.actor_id != context.actor_id:
        raise ChangePolicyError("Actor identity must match the server-verified identity")
    if not context.actor_active:
        raise ChangePolicyError("Inactive or unknown people cannot submit changes")
    _validate_headers(context.headers)

    policy = FIELD_TIERS[expected_worksheet]
    if not proposal.changes:
        raise ChangePolicyError("At least one field change is required")
    if set(proposal.expected_values) != set(proposal.changes):
        raise ChangePolicyError("Expected values must be supplied for every changed field")
    unknown = set(proposal.changes) - set(policy)
    if unknown:
        raise ChangePolicyError("Fields are not allowlisted: " + ", ".join(sorted(unknown)))
    missing_headers = (set(proposal.changes) | set(proposal.expected_values)) - set(context.headers)
    if missing_headers:
        raise ChangePolicyError(
            "Configured fields are missing from headers: " + ", ".join(sorted(missing_headers))
        )
    formula_fields = set(proposal.changes) & set(context.current_formula_fields)
    if formula_fields:
        raise ChangePolicyError(
            "Formula-owned fields are never writable: " + ", ".join(sorted(formula_fields))
        )

    required_tier = max(policy[field] for field in proposal.changes)
    if (
        proposal.record_kind == "task"
        and (
            proposal.changes.get("Status", "").strip() == "Completed"
            or context.current_row.get("Status", "").strip() == "Closed"
        )
    ):
        required_tier = 2
    if proposal.tier != required_tier:
        raise ChangePolicyError(f"These fields require Tier {required_tier}")

    if proposal.record_kind == "task":
        is_owner = context.assigned_to_id == context.actor_id
        is_assigner = context.assigned_by_id == context.actor_id
        is_manager = bool(context.actor_roles & MANAGER_ROLES)
        if not (is_owner or is_assigner or is_manager):
            raise ChangePolicyError("Actor is not authorized for this task")
        if "Assigned To" in proposal.changes:
            assignee = proposal.changes["Assigned To"].strip()
            if assignee not in context.known_active_assignee_values:
                raise ChangePolicyError("Assigned To must reference a known active person")
    else:
        if not context.owner_id or context.owner_id != context.actor_id:
            raise ChangePolicyError("Only the resolved record owner may propose this KPI change")

    normalized_changes: dict[str, str] = {}
    normalized_expected: dict[str, str] = {}
    for field, raw_value in proposal.changes.items():
        value = _text_value(field, str(raw_value))
        if field in NUMERIC_FIELDS:
            number = _number(field, value)
            value = format(number, "f")
        elif field == "Deadline":
            try:
                requested_date = date.fromisoformat(value)
            except ValueError as exc:
                raise ChangePolicyError("Deadline must be a valid ISO date (YYYY-MM-DD)") from exc
            assigned_date_raw = context.current_row.get("Date Assigned", "").strip()
            if assigned_date_raw:
                try:
                    assigned_date = date.fromisoformat(assigned_date_raw[:10])
                except ValueError as exc:
                    raise ChangePolicyError("Current Date Assigned is not a valid date") from exc
                if requested_date < assigned_date:
                    raise ChangePolicyError("Deadline must not precede Date Assigned")
        elif field == "Status":
            _validate_status(
                context.current_row.get("Status", "").strip(), value, proposal.tier, context
            )
        elif field == "Completed Date":
            requested_status = normalized_changes.get(
                "Status", context.current_row.get("Status", "").strip()
            )
            if requested_status != "Completed":
                raise ChangePolicyError("Completed Date can only change for a Completed task")
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ChangePolicyError("Completed Date must be a valid ISO date") from exc
        normalized_changes[field] = value
        normalized_expected[field] = str(proposal.expected_values[field]).strip()

    if normalized_changes.get("Status") == "Completed":
        progress = normalized_changes.get(
            "% Complete", context.current_row.get("% Complete", "")
        ).strip()
        if progress and _number("% Complete", progress) != Decimal(100):
            raise ChangePolicyError("Completed requires % Complete to equal 100")
    if proposal.record_kind == "task" and "Completed Date" in normalized_changes:
        if normalized_changes.get("Status", context.current_row.get("Status", "")) != "Completed":
            raise ChangePolicyError("Completed Date requires task status Completed")

    actual_fingerprint = fingerprint_row(context.headers, context.current_row)
    if not proposal.row_fingerprint or proposal.row_fingerprint != actual_fingerprint:
        raise ChangePolicyError("Row snapshot is stale; refresh and create a new preview")
    for field, expected in normalized_expected.items():
        current = str(context.current_row.get(field, "")).strip()
        if current != expected:
            raise ChangePolicyError(f"Expected value for {field} is stale; refresh before retrying")

    return ValidatedChange(
        worksheet=proposal.worksheet,
        record_id=proposal.record_id.strip(),
        actor_id=context.actor_id,
        tier=proposal.tier,
        changes=normalized_changes,
        expected_values=normalized_expected,
        current_fingerprint=actual_fingerprint,
        reason=proposal.reason.strip(),
    )
