"""Header-driven sheet parsing and validation utilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SheetSchemaError(ValueError):
    """Raised when a worksheet cannot be safely interpreted by its headers."""


@dataclass(frozen=True, slots=True)
class SheetRow:
    """A worksheet record and its one-based Google Sheets row number."""

    row_number: int
    values: dict[str, str]


def normalize_headers(raw_headers: list[Any]) -> list[str]:
    """Normalize header cells and reject blank or duplicate names."""
    headers = [str(value).strip() for value in raw_headers]
    while headers and not headers[-1]:
        headers.pop()
    if not headers:
        raise SheetSchemaError("Worksheet has no header row")
    blank_positions = [str(index + 1) for index, header in enumerate(headers) if not header]
    if blank_positions:
        raise SheetSchemaError(
            "Worksheet contains blank header cells at columns " + ", ".join(blank_positions)
        )
    seen: set[str] = set()
    duplicates: set[str] = set()
    for header in headers:
        if header.casefold() in seen:
            duplicates.add(header)
        seen.add(header.casefold())
    if duplicates:
        raise SheetSchemaError("Duplicate header names: " + ", ".join(sorted(duplicates)))
    return headers


def rows_from_values(values: list[list[Any]]) -> list[SheetRow]:
    """Convert get_all_values output into dictionaries keyed by header names."""
    if not values:
        raise SheetSchemaError("Worksheet is empty; expected a header row")
    headers = normalize_headers(values[0])
    records: list[SheetRow] = []
    for row_number, raw_row in enumerate(values[1:], start=2):
        cells = list(raw_row[: len(headers)])
        cells.extend([""] * (len(headers) - len(cells)))
        record = {header: str(value).strip() for header, value in zip(headers, cells, strict=True)}
        if not any(record.values()):
            continue
        records.append(SheetRow(row_number=row_number, values=record))
    return records


def validate_record_fields(values: dict[str, Any], headers: list[str]) -> None:
    """Ensure writes target known worksheet columns rather than silently dropping data."""
    allowed = set(headers)
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise SheetSchemaError("Unknown worksheet columns: " + ", ".join(unknown))
