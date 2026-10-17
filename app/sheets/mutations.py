"""Allowlisted, audited, dry-run-first Google Sheets mutations."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import uuid4

from app.sheets.client import GoogleSheetsClient, GoogleSheetsConflictError


class MutationRejected(ValueError):
    """Raised when a mutation violates policy or the workbook's current state."""


class MutationOutcomeUncertain(RuntimeError):
    """Raised when a write may have succeeded but cannot be verified safely."""


class AuditRecorder(Protocol):
    async def record(self, event: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class SheetMutationPolicy:
    """Explicit allowlist for one worksheet and its stable record key."""

    worksheet: str
    key_header: str
    editable_fields: frozenset[str]
    validators: dict[str, Callable[[str], None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.worksheet.strip() or not self.key_header.strip():
            raise ValueError("Worksheet and key header must be configured")
        if not self.editable_fields:
            raise ValueError("At least one editable field must be explicitly allowlisted")
        if self.key_header in self.editable_fields:
            raise ValueError("The stable key cannot be an editable field")
        if set(self.validators) - set(self.editable_fields):
            raise ValueError("Validators may only be configured for allowlisted fields")


@dataclass(frozen=True, slots=True)
class MutationResult:
    status: str
    worksheet: str
    record_id: str
    row_number: int
    changes: dict[str, dict[str, str]]
    request_id: str


def validate_nonempty_text(value: str) -> None:
    if not value.strip():
        raise MutationRejected("Value must not be blank")
    if value.lstrip().startswith("="):
        raise MutationRejected("Spreadsheet formulas are not accepted as user input")


def validate_numeric(value: str) -> None:
    if value.lstrip().startswith("="):
        raise MutationRejected("Spreadsheet formulas are not accepted as user input")
    try:
        parsed = Decimal(value.replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise MutationRejected("Target must be numeric") from exc
    if not parsed.is_finite():
        raise MutationRejected("Target must be a finite number")


def normalize_value(value: Any) -> str:
    """Normalize mutation values without interpreting them as spreadsheet formulas."""
    if value is None:
        return ""
    normalized = str(value).strip()
    if normalized.lstrip().startswith("="):
        raise MutationRejected("Spreadsheet formulas are not accepted as user input")
    return normalized


class SafeSheetMutationService:
    """Apply explicitly allowlisted changes through the existing Sheets repository.

    This service does not provide authentication. Callers must supply an actor identity
    established by a trusted server-side authentication layer before invoking it.
    """

    def __init__(
        self,
        sheets: GoogleSheetsClient,
        *,
        policies: dict[str, SheetMutationPolicy],
        audit: AuditRecorder,
        dry_run: bool = True,
        writes_enabled: bool = False,
        bot_writes_enabled: bool = False,
    ) -> None:
        self.sheets = sheets
        self.policies = policies
        self.audit = audit
        self.dry_run = dry_run
        self.writes_enabled = writes_enabled
        self.bot_writes_enabled = bot_writes_enabled
        self._record_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def mutate(
        self,
        *,
        actor_id: str,
        worksheet: str,
        record_id: str,
        changes: dict[str, Any],
        expected_values: dict[str, Any],
        request_id: str | None = None,
    ) -> MutationResult:
        actor = actor_id.strip()
        key_value = record_id.strip()
        request = request_id or str(uuid4())
        policy = self.policies.get(worksheet)
        if not actor:
            raise MutationRejected("A verified actor identity is required")
        if policy is None:
            raise MutationRejected("Mutations are not configured for this worksheet")
        if policy.worksheet != worksheet:
            raise MutationRejected(
                "Mutation policy worksheet does not match the requested worksheet"
            )
        if not key_value:
            raise MutationRejected("Record ID must not be blank")
        if not changes:
            raise MutationRejected("At least one field change is required")
        if set(changes) - policy.editable_fields:
            fields = ", ".join(sorted(set(changes) - policy.editable_fields))
            raise MutationRejected(f"Fields are not editable: {fields}")
        if set(expected_values) != set(changes):
            raise MutationRejected("Expected values must be supplied for every changed field")

        normalized_changes: dict[str, str] = {}
        normalized_expected: dict[str, str] = {}
        for name, raw_value in changes.items():
            value = normalize_value(raw_value)
            validator = policy.validators.get(name)
            if validator is not None:
                validator(value)
            normalized_changes[name] = value
            normalized_expected[name] = normalize_value(expected_values[name])

        lock_key = (worksheet, key_value)
        lock = self._record_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            headers = await self.sheets.read_headers(worksheet)
            if policy.key_header not in headers:
                raise MutationRejected(
                    f"Stable key header {policy.key_header!r} is missing from {worksheet}"
                )
            missing_fields = (set(normalized_changes) | set(normalized_expected)) - set(headers)
            if missing_fields:
                raise MutationRejected(
                    "Configured fields are missing from worksheet headers: "
                    + ", ".join(sorted(missing_fields))
                )
            records = await self.sheets.read_records(worksheet)
            matches = [
                row for row in records
                if row.get(policy.key_header, "").strip() == key_value
            ]
            if len(matches) != 1:
                raise MutationRejected(
                    f"Expected exactly one {policy.key_header}={key_value!r}; found {len(matches)}"
                )
            current = matches[0]
            before = {name: current.get(name, "").strip() for name in normalized_changes}
            if before != normalized_expected:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="conflict",
                    before=before,
                    after=normalized_changes,
                )
                raise MutationRejected(
                    "Record changed since it was loaded; refresh before retrying"
                )

            change_set = {
                name: {"before": before[name], "after": normalized_changes[name]}
                for name in normalized_changes
            }
            enabled = (
                self.bot_writes_enabled and self.writes_enabled and not self.dry_run
            )
            if not enabled:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="preview",
                    before=before,
                    after=normalized_changes,
                )
                return MutationResult("preview", worksheet, key_value, 0, change_set, request)

            await self._audit(
                request=request,
                actor=actor,
                policy=policy,
                record_id=key_value,
                outcome="started",
                before=before,
                after=normalized_changes,
            )
            try:
                row_number = await self.sheets.update_record(
                    worksheet,
                    key_header=policy.key_header,
                    key_value=key_value,
                    values=normalized_changes,
                    expected_values=normalized_expected,
                    value_input_option="RAW",
                )
            except GoogleSheetsConflictError as exc:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="conflict",
                    before=before,
                    after=normalized_changes,
                )
                raise MutationRejected(
                    "Record changed during the update; refresh before retrying"
                ) from exc
            except Exception as exc:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="uncertain",
                    before=before,
                    after=normalized_changes,
                )
                raise MutationOutcomeUncertain(
                    "The write result is uncertain. Refresh the record and audit log "
                    "before retrying."
                ) from exc

            try:
                after_records = await self.sheets.read_records(worksheet)
                after_matches = [
                    row for row in after_records
                    if row.get(policy.key_header, "").strip() == key_value
                ]
                verified = (
                    len(after_matches) == 1
                    and all(
                        after_matches[0].get(name, "").strip() == value
                        for name, value in normalized_changes.items()
                    )
                )
            except Exception as exc:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="uncertain",
                    before=before,
                    after=normalized_changes,
                )
                raise MutationOutcomeUncertain(
                    "Write was sent but read-after-write verification failed."
                ) from exc

            if not verified:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="uncertain",
                    before=before,
                    after=normalized_changes,
                )
                raise MutationOutcomeUncertain(
                    "The written values could not be verified. Do not retry until reconciled."
                )

            try:
                await self._audit(
                    request=request,
                    actor=actor,
                    policy=policy,
                    record_id=key_value,
                    outcome="verified",
                    before=before,
                    after=normalized_changes,
                )
            except Exception as exc:
                raise MutationOutcomeUncertain(
                    "Values were verified, but the final audit event could not be persisted."
                ) from exc
            return MutationResult("applied", worksheet, key_value, row_number, change_set, request)

    async def _audit(
        self,
        *,
        request: str,
        actor: str,
        policy: SheetMutationPolicy,
        record_id: str,
        outcome: str,
        before: dict[str, str],
        after: dict[str, str],
    ) -> None:
        await self.audit.record(
            {
                "request_id": request,
                "actor_id": actor,
                "action": "sheet_mutation",
                "worksheet": policy.worksheet,
                "record_id": record_id,
                "changed_fields": sorted(after),
                "before": before,
                "after": after,
                "outcome": outcome,
            }
        )
