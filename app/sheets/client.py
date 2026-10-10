"""Asynchronous, header-driven Google Sheets repository."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import gspread
from google.oauth2.service_account import Credentials

from app.config import Settings
from app.sheets.schema import SheetRow, normalize_headers, rows_from_values, validate_record_fields

logger = logging.getLogger(__name__)
SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]
MAX_API_ATTEMPTS = 3
T = TypeVar("T")


class GoogleSheetsError(RuntimeError):
    """Raised for Google Sheets access or mutation failures."""


class GoogleSheetsClient:
    """Read and update worksheets by header names, never fixed column positions."""

    def __init__(self, spreadsheet: Any) -> None:
        self._spreadsheet = spreadsheet

    @classmethod
    def from_settings(cls, settings: Settings) -> GoogleSheetsClient:
        """Create an authenticated client after validating required configuration."""
        if not settings.google_sheet_id:
            raise GoogleSheetsError("GOOGLE_SHEET_ID is not configured")
        credentials_path = Path(settings.google_creds_path).expanduser()
        if not credentials_path.is_file():
            raise GoogleSheetsError(f"Google service-account file not found: {credentials_path}")
        try:
            credentials = Credentials.from_service_account_file(
                str(credentials_path), scopes=SHEETS_SCOPE
            )
            client = gspread.authorize(credentials)
            spreadsheet = client.open_by_key(settings.google_sheet_id)
        except Exception as exc:
            raise GoogleSheetsError(
                "Unable to authenticate or open the configured spreadsheet"
            ) from exc
        return cls(spreadsheet)

    async def _api_call(self, operation: Callable[[], T], *, context: str) -> T:
        """Run blocking Google API calls off-loop and retry transient failures a few times."""
        for attempt in range(1, MAX_API_ATTEMPTS + 1):
            try:
                return await asyncio.to_thread(operation)
            except Exception as exc:
                if attempt == MAX_API_ATTEMPTS:
                    raise GoogleSheetsError(context) from exc
                delay = 0.25 * (2 ** (attempt - 1))
                logger.warning(
                    "google_sheets_retry attempt=%s/%s delay_seconds=%s context=%s error_type=%s",
                    attempt,
                    MAX_API_ATTEMPTS,
                    delay,
                    context,
                    type(exc).__name__,
                )
                await asyncio.sleep(delay)
        raise GoogleSheetsError(context)

    def _worksheet(self, worksheet_name: str) -> Any:
        try:
            return self._spreadsheet.worksheet(worksheet_name)
        except Exception as exc:
            raise GoogleSheetsError(f"Unable to open worksheet: {worksheet_name}") from exc

    async def read_rows(self, worksheet_name: str) -> list[SheetRow]:
        """Read rows off the event loop and map values using the first row's headers."""
        worksheet = self._worksheet(worksheet_name)
        try:
            values = await self._api_call(
                worksheet.get_all_values, context=f"Unable to read worksheet: {worksheet_name}"
            )
            return rows_from_values(values)
        except (GoogleSheetsError, ValueError) as exc:
            if isinstance(exc, GoogleSheetsError):
                raise
            raise GoogleSheetsError(f"Invalid worksheet schema: {worksheet_name}") from exc

    async def read_records(self, worksheet_name: str) -> list[dict[str, str]]:
        """Return worksheet records as dictionaries keyed by their header names."""
        return [row.values for row in await self.read_rows(worksheet_name)]

    async def update_record(
        self,
        worksheet_name: str,
        *,
        key_header: str,
        key_value: str,
        values: dict[str, Any],
    ) -> int:
        """Update one unique record and return its row number; reject ambiguous keys."""
        worksheet = self._worksheet(worksheet_name)
        grid = await self._api_call(
            worksheet.get_all_values, context=f"Unable to read worksheet: {worksheet_name}"
        )
        if not grid:
            raise GoogleSheetsError(f"Worksheet has no headers: {worksheet_name}")
        headers = normalize_headers(grid[0])
        if key_header not in headers:
            raise GoogleSheetsError(f"Key header {key_header!r} missing from {worksheet_name}")
        validate_record_fields(values, headers)
        key_index = headers.index(key_header)
        matches = [
            index + 1
            for index, row in enumerate(grid[1:], start=1)
            if key_value.strip()
            and key_index < len(row)
            and str(row[key_index]).strip() == key_value.strip()
        ]
        if len(matches) != 1:
            raise GoogleSheetsError(
                f"Expected one {key_header}={key_value!r} record in {worksheet_name}; "
                f"found {len(matches)}"
            )
        row_number = matches[0]
        updates = [
            {
                "range": gspread.utils.rowcol_to_a1(row_number, headers.index(header) + 1),
                "values": [["" if value is None else str(value)]],
            }
            for header, value in values.items()
        ]
        if updates:
            await self._api_call(
                lambda: worksheet.batch_update(updates, value_input_option="USER_ENTERED"),
                context=f"Unable to update worksheet: {worksheet_name}",
            )
        return row_number

    async def append_record(self, worksheet_name: str, values: dict[str, Any]) -> int:
        """Append a record aligned to worksheet headers and return the resulting row number."""
        worksheet = self._worksheet(worksheet_name)
        grid = await self._api_call(
            worksheet.get_all_values, context=f"Unable to read worksheet: {worksheet_name}"
        )
        if not grid:
            raise GoogleSheetsError(f"Worksheet has no headers: {worksheet_name}")
        headers = normalize_headers(grid[0])
        validate_record_fields(values, headers)
        row = [
            "" if values.get(header) is None else str(values.get(header, ""))
            for header in headers
        ]
        await self._api_call(
            lambda: worksheet.append_row(row, value_input_option="USER_ENTERED"),
            context=f"Unable to append to worksheet: {worksheet_name}",
        )
        return len(grid) + 1
